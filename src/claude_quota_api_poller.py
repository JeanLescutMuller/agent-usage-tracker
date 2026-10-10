#!/usr/bin/env python3
"""Collector: samples Anthropic's quota API (GET /api/oauth/usage) on a timer,
because that side has no history - a missed reading is permanently lost.
Hands one row per poll to ingest_account_quota.py, which stores it in
data/claude/account_quotas.db: the full raw response, unfiltered, plus its
HTTP response headers - or, when the reading can't be taken, an `error`
object saying which stage failed and why (the poll_errors table). A missing
reading is itself data (roughly 11% of rows historically), and "the token
expired" and "the wifi dropped" need very different responses.

Token usage is deliberately NOT logged here. It's fully recomputable at
analysis time from the transcripts Claude Code itself already writes under
~/.claude/projects/ (transcript_reader.py reads them) - logging it here too
would just be storing a copy of data that already durably exists elsewhere
on disk (cleanupPeriodDays=365 on this machine, so "durably" means about a
year). Only log what can't be recomputed after the fact.

When to poll is not decided here: the job-runner entrypoint claude-quota.sh
(deployed to ~/opt/agent-usage-tracker/, run every 60s by its trigger) skips
while state/quota/claude is fresh enough, and waits out a 429's Retry-After
on this machine only - 429s are counted per login token, i.e. per machine
(USAGE_DATA_SOURCES.md §3.5) - (this poller exits 75 with "retry-after N" as its last
line). One start = one attempt, unless --peer finds a fresh reading first.

The MacBook runs it with --peer=H-Frank-1 (the VM cannot reach the Mac, so
the Mac does both directions, over ssh, through the VM's own account gate,
ingest_account_quota.py):
  1. it first asks the VM for its freshest reading (`--latest`); younger than
     --fresh-within seconds: stored here through the local gate, exit 10,
     no API call;
  2. else it polls, and hands a reading to the VM's gate, which stores it
     and refreshes the VM's state/quota/claude - so the VM's own check then
     finds it fresh and skips. A failed attempt stays here.
The VM unreachable: the Mac goes on alone (a note on stderr).

Exit codes: 0 a reading, its summary last ("5h 42% · 7d 18%"); 10 the
peer's reading taken; 75 a 429; 1 any other failure (401, network...).
Every attempt is stored, reading or failure, as before.

Note this poller's own `source: "claude_api"` rows are a fallback path now, not
the primary one: statusline_payload_reader.py stores a free
`source: "claude_statusline"` reading on every real message, riding
Claude Code's own in-memory rate_limits state - no network call, never
rate-limited. This poller still matters for the gap that push path can't
cover: a session that hasn't sent its first message yet, or a stretch with
no statusline rendering anywhere on the machine at all. Both sources share
data/claude/account_quotas.db (Codex has its own - see
codex_quota_api_poller.py).
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import ingest_account_quota
import usage_db

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
# Where Claude Code keeps the same credential blob on Linux (no Keychain).
CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"

# The peer's account gate, run over ssh (a non-login shell: python3 is
# /usr/bin/python3 on the VM, as for push_to_central.py).
PEER_GATE = "python3 ~/opt/agent-usage-tracker/src/ingest_account_quota.py --agent claude"
PEER_TIMEOUT_S = 20

# Response headers worth keeping - request-id lets a specific reading be
# cross-referenced/reported to Anthropic support if a number ever looks
# wrong; date is the server's own clock, useful to sanity-check local
# clock skew; org/workspace id distinguish readings if this ever runs
# under more than one account.
HEADERS_TO_KEEP = (
    "date", "request-id", "anthropic-organization-id",
    "anthropic-workspace-id", "server-timing",
)


def fetch_token() -> tuple[str | None, dict | None]:
    """Claude Code itself writes the OAuth token on login: in the macOS
    Keychain under this exact service name, or on Linux in
    ~/.claude/.credentials.json (same JSON) - this is the only place on the
    machine reading it for quota purposes (see the module docstring).
    Returns (token, d_error); exactly one is non-None."""
    try:
        if sys.platform == "darwin":
            text = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout
        else:
            text = CREDENTIALS_FILE.read_text()
        d_creds = json.loads(text)
        token = d_creds.get("claudeAiOauth", {}).get("accessToken")
        if not token:
            return None, {"stage": "keychain", "type": "MissingAccessToken"}
        return token, None
    except Exception as exc:
        # Type only, never str(exc): this stage handles the credential blob,
        # and an exception message here could echo part of it into the log.
        # The type alone is enough to tell "not logged in" (CalledProcessError)
        # from "Keychain locked/slow" (TimeoutExpired) from "format changed"
        # (JSONDecodeError).
        return None, {"stage": "keychain", "type": type(exc).__name__}


def fetch_usage(token: str) -> tuple[dict | None, dict | None, dict | None]:
    """Returns (body, headers, d_error); d_error is None on success."""
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            # Everything below matches what the real `claude` binary's own
            # fetchUtilization() sends to this same endpoint (recovered by
            # decompiling ~/.local/share/claude/versions/*, 2026-08-30) -
            # urllib's bare defaults (Python-urllib/x.y UA, no Accept, no
            # anthropic-version) fingerprint this as a raw script hitting an
            # internal OAuth-only endpoint, unlike anything the real client
            # ever sends. Untested hypothesis: worth an honest data point,
            # not a confirmed fix - see data/claude-quota-history.jsonl going
            # forward.
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
            "User-Agent": "claude-cli/2.1.251 (external, cli)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            d_body = json.loads(resp.read())
            d_headers = {k: v for k, v in resp.headers.items() if k.lower() in HEADERS_TO_KEEP}
            return d_body, d_headers, None
    # HTTPError first - it subclasses URLError. The status is the whole point:
    # 401 means the OAuth access token expired (8 h lifetime): this poller only
    # reads it and never renews it, so it 401s whenever no `claude` process ran
    # here in the last 8 h - the next `claude` run renews it. Only if the
    # refresh token itself expired (refreshTokenExpiresAt) is a re-login needed. 429
    # means our own 5-minute polling is being rate-limited, 5xx is Anthropic's
    # side. Those need completely different responses, and until now the log
    # recorded all three identically as `"api": null`.
    except urllib.error.HTTPError as exc:
        d_err = {"stage": "http", "type": "HTTPError",
                 "status": exc.code, "detail": exc.reason}
        # 2026-08-30: mitmproxy capture showed the server DOES send a real
        # Retry-After on 429 (e.g. 1820s) - this poller was silently
        # discarding it (HEADERS_TO_KEEP never covered it, and only the
        # success path even looked at headers) and retrying every 60-300s
        # regardless, almost certainly re-tripping the exact backoff window
        # the server asked for. The entrypoint now waits it out (exit 75).
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after is not None:
            try:
                d_err["retry_after_s"] = int(retry_after)
            except ValueError:
                pass
        return None, None, d_err
    except urllib.error.URLError as exc:
        # No HTTP status at all - offline, DNS, TLS, or the 5s timeout.
        return None, None, {"stage": "network", "type": type(exc).__name__,
                            "detail": str(exc.reason)}
    except json.JSONDecodeError as exc:
        # 200 OK but the body isn't JSON - e.g. a captive portal's login page.
        return None, None, {"stage": "parse", "type": "JSONDecodeError",
                            "detail": exc.msg}
    except OSError as exc:
        # Transport failures urllib doesn't wrap in URLError - e.g.
        # http.client.RemoteDisconnected when the server closes the
        # connection mid-request. Before this, they crashed the run and left
        # no row at all (7 times by 2026-09-29, see logs/quota-poll.err).
        return None, None, {"stage": "network", "type": type(exc).__name__,
                            "detail": str(exc)}


def peer_gate(host: str, args: str = "", stdin: str = "") -> str | None:
    """Runs HOST's account gate over ssh; its stdout, or None when HOST
    cannot be reached or the gate failed."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, PEER_GATE + args],
            input=stdin, capture_output=True, text=True, timeout=PEER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def take_peer_reading(host: str, fresh_within: int) -> str | None:
    """HOST's freshest reading, stored here through the local gate when it
    is younger than fresh_within seconds; returns the line to print, or None
    (none fresh enough, or HOST not reached: then poll)."""
    l_lines = (peer_gate(host, " --latest") or "").strip().splitlines()
    if not l_lines:
        return None
    raw = l_lines[-1]  # the gate's one line, whatever a login script printed first
    try:
        d_row = json.loads(raw)
        age = int(time.time()) - ingest_account_quota.columns("claude", d_row)["ts"]
        if age >= fresh_within:
            return None
        ingest_account_quota.add_row(usage_db.open_account("claude"), "claude", d_row, raw)
    except (ValueError, KeyError, TypeError):
        return None
    return f"{host}'s reading is fresh ({age} s old): taken"


def _pct(d_window) -> str:
    pct = d_window.get("utilization") if isinstance(d_window, dict) else None
    return "?" if pct is None else f"{pct:.0f}"


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description="One reading of the Claude account's quota (see the module docstring).")
    parser.add_argument("--peer", metavar="HOST", help="first take HOST's fresh reading; hand HOST a new reading")
    parser.add_argument("--fresh-within", type=int, default=0, metavar="SECONDS",
                        help="with --peer: HOST's reading younger than this is taken instead of polling")
    args = parser.parse_args(argv)
    if args.peer:
        taken = take_peer_reading(args.peer, args.fresh_within)
        if taken:
            print(taken)
            sys.exit(10)

    token, d_error = fetch_token()
    d_api = d_api_headers = None
    if token:
        d_api, d_api_headers, d_error = fetch_usage(token)

    d_record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "claude_api",  # "claude" before 2026-10-04 (renamed in place by rename_sources.py)
        "api": d_api,
        "api_headers": d_api_headers,
        "error": d_error,  # None on success; why the reading is missing otherwise
    }
    raw = json.dumps(d_record)
    # The gate stores a reading in account_quotas (and refreshes
    # state/quota/claude when newer), a failed attempt in poll_errors.
    ingest_account_quota.add_row(usage_db.open_account("claude"), "claude", d_record, raw)
    if d_error is None:
        if args.peer and peer_gate(args.peer, stdin=raw + "\n") is None:
            print(f"{args.peer} not reached: this reading stays here", file=sys.stderr, flush=True)
        print(f"5h {_pct(d_api.get('five_hour'))}% · 7d {_pct(d_api.get('seven_day'))}%")
        return
    if d_error.get("status") == 429:
        print(f"retry-after {d_error.get('retry_after_s') or 0}")
        sys.exit(75)
    print("failed: " + " ".join(str(d_error[k]) for k in ("stage", "type", "status") if d_error.get(k) is not None))
    sys.exit(1)


if __name__ == "__main__":
    main()
