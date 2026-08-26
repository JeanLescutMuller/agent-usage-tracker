#!/usr/bin/env python3
"""Samples Anthropic's utilization API on a timer, because that side has no
history - a missed reading is permanently lost. Appends one record per run
to data/utilization-log.jsonl: the full raw GET /api/oauth/usage response,
unfiltered, plus its HTTP response headers.

Token usage is deliberately NOT logged here. It's fully recomputable at
analysis time from the transcripts Claude Code itself already writes under
~/.claude/projects/ (see recompute_token_events.py) - logging it here too
would just be storing a copy of data that already durably exists elsewhere
on disk (cleanupPeriodDays=365 on this machine, so "durably" means about a
year). Only log what can't be recomputed after the fact.
"""
import json
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
UTIL_LOG_FILE = DATA_DIR / "utilization-log.jsonl"

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"

# Response headers worth keeping - request-id lets a specific reading be
# cross-referenced/reported to Anthropic support if a number ever looks
# wrong; date is the server's own clock, useful to sanity-check local
# clock skew; org/workspace id distinguish readings if this ever runs
# under more than one account.
HEADERS_TO_KEEP = (
    "date", "request-id", "anthropic-organization-id",
    "anthropic-workspace-id", "server-timing",
)


def fetch_token() -> str | None:
    """Same macOS Keychain read statusline-usage-fetch.sh uses - Claude
    Code itself writes the OAuth token here on login, under this exact
    service name."""
    try:
        d_raw = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        d_creds = json.loads(d_raw.stdout)
        return d_creds.get("claudeAiOauth", {}).get("accessToken") or None
    except Exception:
        return None


def fetch_usage(token: str) -> tuple[dict | None, dict | None]:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            d_headers = {k: v for k, v in resp.headers.items() if k.lower() in HEADERS_TO_KEEP}
            return body, d_headers
    except (urllib.error.URLError, json.JSONDecodeError):
        return None, None


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    token = fetch_token()
    d_api, d_api_headers = fetch_usage(token) if token else (None, None)

    record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "api": d_api,
        "api_headers": d_api_headers,
    }
    with UTIL_LOG_FILE.open("a") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
