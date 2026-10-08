#!/usr/bin/env python3
"""Collector: fetches Codex's plan-limit history from the ChatGPT backend
about once a day and hands the raw response to ingest_account_quota.py
(data/codex/account_quotas.db), tagged `source: "codex_plan_limit_history"`.

Why: it is the only sub-percent quota source for either agent. Each
finished 5-hour / 7-day window comes back with `used_basis_points`
(hundredths of a percent, fractional), where every live feed - app-server,
session files, Claude's headers - is whole percents. It covers the last 7
days, so one fetch a day with overlap never loses a window, and a missed
day is recovered by the next fetch. See USAGE_DATA_SOURCES.md §4.4.

The endpoint is not exposed through `codex app-server` (the Codex TUI calls
it directly over HTTP), so this reads the same bearer token and account id
Codex itself uses from ~/.codex/auth.json. Read-only; the token is never
logged. Codex refreshes that token when it runs; if it has expired, the
row records the HTTP 401 and the next attempt retries.

Cadence: the LaunchAgent ticks every 60s (via run_pollers.py), but this
only calls the backend when the last attempt (read back from the database,
reading or failure) is SUCCESS_INTERVAL_SECONDS old after a success, or
RETRY_INTERVAL_SECONDS old after a failure.

These rows carry no current reading, so their percent columns are NULL and
the `latest` view skips them.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import ingest_account_quota
import usage_db

AUTH_FILE = Path.home() / ".codex" / "auth.json"
URL = "https://chatgpt.com/backend-api/wham/usage/plan_limit_history?days=7"

SUCCESS_INTERVAL_SECONDS = 86400
RETRY_INTERVAL_SECONDS = 3600
HTTP_TIMEOUT_S = 15


def fetch_plan_history() -> tuple[dict | None, dict | None]:
    """(response, None) on success, (None, error dict) otherwise - the same
    typed stage/type/detail error shape as the other pollers."""
    try:
        d_tokens = json.loads(AUTH_FILE.read_text())["tokens"]
        token, account_id = d_tokens["access_token"], d_tokens["account_id"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Deliberately no str(exc): a KeyError/ValueError message could echo
        # part of the auth file into the log.
        return None, {"stage": "auth", "type": type(exc).__name__}

    req = urllib.request.Request(URL, headers={
        "Authorization": f"Bearer {token}",
        "ChatGPT-Account-Id": account_id,
        "User-Agent": "codex_cli_rs",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read()), None
    # HTTPError first - it subclasses URLError, which subclasses OSError.
    except urllib.error.HTTPError as exc:
        return None, {"stage": "http", "type": "HTTPError", "status": exc.code, "detail": exc.reason}
    except urllib.error.URLError as exc:
        return None, {"stage": "network", "type": type(exc).__name__, "detail": str(exc.reason)}
    except json.JSONDecodeError as exc:
        return None, {"stage": "parse", "type": "JSONDecodeError", "detail": exc.msg}
    except OSError as exc:
        # e.g. http.client.RemoteDisconnected, which urllib doesn't wrap.
        return None, {"stage": "network", "type": type(exc).__name__, "detail": str(exc)}


def main() -> None:
    now = time.time()
    d_last = ingest_account_quota.last_attempt(usage_db.open_account("codex", readonly=True), ("codex_plan_limit_history",))
    if d_last is not None:
        interval = SUCCESS_INTERVAL_SECONDS if d_last["error"] is None else RETRY_INTERVAL_SECONDS
        if now - d_last["ts"] < interval:
            print(f"skip: last plan-history attempt is under {interval}s old")
            return

    d_history, d_error = fetch_plan_history()
    d_record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "codex_plan_limit_history",
        "plan_limit_history": d_history,  # full raw response, unfiltered
        "error": d_error,  # None on success; why the reading is missing otherwise
    }
    ingest_account_quota.add_row(usage_db.open_account("codex"), "codex", d_record)


if __name__ == "__main__":
    main()
