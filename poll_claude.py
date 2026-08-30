#!/usr/bin/env python3
"""Samples Anthropic's utilization API on a timer, because that side has no
history - a missed reading is permanently lost. Appends one record per run
to data/utilization-log.jsonl: the full raw GET /api/oauth/usage response,
unfiltered, plus its HTTP response headers - or, when the reading can't be
taken, an `error` object saying which stage failed and why. A missing
reading is itself data (roughly 11% of rows historically), and "the token
expired" and "the wifi dropped" need very different responses.

Token usage is deliberately NOT logged here. It's fully recomputable at
analysis time from the transcripts Claude Code itself already writes under
~/.claude/projects/ (see recompute_token_events.py) - logging it here too
would just be storing a copy of data that already durably exists elsewhere
on disk (cleanupPeriodDays=365 on this machine, so "durably" means about a
year). Only log what can't be recomputed after the fact.

The LaunchAgent ticks this every 60s (see install.sh), but every tick isn't
necessarily a real poll: agent-statusline (a separate project - see its
AGENTS.md's "Cross-project dependency") touches a heartbeat file on every
statusline render, so a heartbeat younger than ACTIVE_WINDOW_SECONDS means a
statusline is being drawn somewhere *right now*. If so, poll for real -
that's the whole point of a 60s tick. If not, only poll if the last logged
reading (of either outcome, success or error) is already
IDLE_INTERVAL_SECONDS old, so a fully idle machine still settles to roughly
the old flat 5-minute cadence instead of a 60s busy-loop for no reason. This
deliberately does NOT key off token/message activity - a usage window
resetting to 0% moves the meter with zero new tokens spent, so "is anyone
even looking at a statusline" is the right signal, not "did tokens move."
A missing heartbeat file (statusline not installed, or never rendered) just
means is_active() is always False, which degrades gracefully to the old
flat 5-minute cadence.
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

# See the module docstring for the gating rationale.
HEARTBEAT_FILE = Path.home() / "opt" / "agent-statusline" / "state" / "providers" / "claude.heartbeat"
ACTIVE_WINDOW_SECONDS = 90
IDLE_INTERVAL_SECONDS = 300

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
    """Claude Code itself writes the OAuth token here on login, under this
    exact service name - this is now the only place on the machine reading
    it for quota purposes; agent-statusline reads this project's log instead
    of the Keychain directly (see the module docstring). Returns
    (token, d_error); exactly one is non-None."""
    try:
        d_raw = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        d_creds = json.loads(d_raw.stdout)
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
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            d_body = json.loads(resp.read())
            d_headers = {k: v for k, v in resp.headers.items() if k.lower() in HEADERS_TO_KEEP}
            return d_body, d_headers, None
    # HTTPError first - it subclasses URLError. The status is the whole point:
    # 401 means the OAuth token expired and Claude Code needs a re-login, 429
    # means our own 5-minute polling is being rate-limited, 5xx is Anthropic's
    # side. Those need completely different responses, and until now the log
    # recorded all three identically as `"api": null`.
    except urllib.error.HTTPError as exc:
        return None, None, {"stage": "http", "type": "HTTPError",
                            "status": exc.code, "detail": exc.reason}
    except urllib.error.URLError as exc:
        # No HTTP status at all - offline, DNS, TLS, or the 5s timeout.
        return None, None, {"stage": "network", "type": type(exc).__name__,
                            "detail": str(exc.reason)}
    except json.JSONDecodeError as exc:
        # 200 OK but the body isn't JSON - e.g. a captive portal's login page.
        return None, None, {"stage": "parse", "type": "JSONDecodeError",
                            "detail": exc.msg}


def _is_active(now: float) -> bool:
    """Is a Claude Code statusline rendering somewhere right now?"""
    try:
        return (now - HEARTBEAT_FILE.stat().st_mtime) < ACTIVE_WINDOW_SECONDS
    except OSError:
        return False


def _last_log_ts() -> int | None:
    """Timestamp of the last logged row (any source, any outcome), read from
    the tail of the file rather than a full scan - this runs every tick,
    forever, and the log only grows."""
    if not UTIL_LOG_FILE.exists():
        return None
    with UTIL_LOG_FILE.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        chunk = min(size, 16384)
        f.seek(size - chunk)
        data = f.read(chunk)
    lines = [line for line in data.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1]).get("ts")
    except json.JSONDecodeError:
        # A line this close to a 16KB chunk boundary being truncated, or a
        # corrupt row, are both edge cases - fail open (treat as due) rather
        # than risk silently going quiet.
        return None


def _should_poll(now: float) -> bool:
    if _is_active(now):
        return True
    last_ts = _last_log_ts()
    return last_ts is None or (now - last_ts) >= IDLE_INTERVAL_SECONDS


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    now = time.time()
    if not _should_poll(now):
        print(f"skip: idle, last reading is under {IDLE_INTERVAL_SECONDS}s old")
        return

    token, d_error = fetch_token()
    d_api = d_api_headers = None
    if token:
        d_api, d_api_headers, d_error = fetch_usage(token)

    d_record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "claude",
        "api": d_api,
        "api_headers": d_api_headers,
        "error": d_error,  # None on success; why the reading is missing otherwise
    }
    with UTIL_LOG_FILE.open("a") as f:
        f.write(json.dumps(d_record) + "\n")


if __name__ == "__main__":
    main()
