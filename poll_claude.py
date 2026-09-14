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
            # Everything below matches what the real `claude` binary's own
            # fetchUtilization() sends to this same endpoint (recovered by
            # decompiling ~/.local/share/claude/versions/*, 2026-08-30) -
            # urllib's bare defaults (Python-urllib/x.y UA, no Accept, no
            # anthropic-version) fingerprint this as a raw script hitting an
            # internal OAuth-only endpoint, unlike anything the real client
            # ever sends. Untested hypothesis: worth an honest data point,
            # not a confirmed fix - see data/utilization-log.jsonl going
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
    # 401 means the OAuth token expired and Claude Code needs a re-login, 429
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
        # the server asked for. _should_poll() now enforces this.
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


def _is_active(now: float) -> bool:
    """Is a Claude Code statusline rendering somewhere right now?"""
    try:
        return (now - HEARTBEAT_FILE.stat().st_mtime) < ACTIVE_WINDOW_SECONDS
    except OSError:
        return False


def _tail_rows() -> list[dict]:
    """Parsed rows from the tail of the log, newest last - shared by both
    lookups below so there's one place doing the truncation-tolerant read."""
    if not UTIL_LOG_FILE.exists():
        return []
    with UTIL_LOG_FILE.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        chunk = min(size, 16384)
        f.seek(size - chunk)
        data = f.read(chunk)
    lines = [line for line in data.splitlines() if line.strip()]
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # A line this close to the 16KB chunk boundary being truncated,
            # or a corrupt row - skip it rather than risk raising here, this
            # runs every tick forever.
            continue
    return rows


def _last_log_row() -> dict | None:
    """The last logged row (any source, any outcome) - used only for the
    idle-cadence fallback below, which deliberately doesn't care which
    poller (Claude or Codex) was last active on this machine."""
    rows = _tail_rows()
    return rows[-1] if rows else None


def _last_claude_log_row() -> dict | None:
    """The last logged row from THIS poller specifically. poll_all.py
    interleaves Claude and Codex rows in the same file (see its docstring),
    so scanning back past intervening Codex rows is required here - reading
    the literal last line missed a real Retry-After backoff for a full tick
    once already (2026-08-30: a Codex row landed as the tail seconds before
    this ran, its `error` was silently treated as "no backoff active", and
    the poller polled straight into a live 429 lockout it should have been
    sitting out - the whole point of the backoff check below)."""
    for row in reversed(_tail_rows()):
        if row.get("source", "claude") == "claude":
            return row
    return None


def _should_poll(now: float) -> bool:
    last_claude_row = _last_claude_log_row()
    if last_claude_row is not None:
        d_err = last_claude_row.get("error") or {}
        retry_after_s = d_err.get("retry_after_s")
        if retry_after_s is not None:
            backoff_until = last_claude_row["ts"] + retry_after_s
            if now < backoff_until:
                # Server-mandated backoff always wins, active session or not -
                # see the note on the retry_after_s capture above.
                return False
    if _is_active(now):
        return True
    last_row = _last_log_row()
    last_ts = last_row["ts"] if last_row else None
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
