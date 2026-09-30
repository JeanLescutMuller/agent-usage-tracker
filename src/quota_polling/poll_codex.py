#!/usr/bin/env python3
"""Samples Codex CLI's rate-limit/usage state on a timer, same rationale as
poll_claude.py: this side has no history either, a missed reading is
permanently lost. Appends one record per run to
data/codex/account.jsonl (this agent's account-scope file, a sibling of
poll_claude.py's data/claude/account.jsonl - per-provider since 2026-08-31, per-scope since 2026-09-30, see
AGENTS.md's "Naming history") - the full raw `account/rateLimits/read` and
`account/usage/read` results, unfiltered, or an `error` object saying which
stage failed and why.

Unlike Claude Code, Codex has no plain HTTP usage endpoint. Its CLI
statusline gets rate-limit data from a JSON-RPC method on `codex
app-server` (confirmed by reading ~/.codex/statusline-command.sh and by
grepping ~/.codex/logs_2.sqlite for rpc.method="account/rateLimits/read"),
so this script speaks that protocol directly: spawn `codex app-server
--stdio`, send `initialize` then the two `account/*` requests over stdio,
read the matching JSON-RPC responses. No credential handling needed here -
the subprocess reads ~/.codex/auth.json itself, the same file `codex`
interactive sessions use.

Per-thread token/cost detail (account/usage/read with a threadId param)
is deliberately NOT fetched here - it needs an enumerable list of thread
ids and isn't part of "the current rate-limit snapshot", the same
"unrecoverable" bar poll_claude.py applies. That's future work for a
recompute_codex_events.py analogue, not this poller.

The shared LaunchAgent tick is 60s. Three cadence tiers, checked in order,
mirroring poll_claude.py's heartbeat-driven speedup but adding a tier it
has no equivalent of:

1. A local Codex session file was modified within SESSION_FRESH_SECONDS -
   an active session already writes its own rate_limits snapshot to
   ~/.codex/sessions/**/*.jsonl on every turn (the `token_count` event; see
   recompute_codex_events.py and AGENTS.md's 2026-08-30 correction), fresher
   than any poll here could be. Skip entirely. poll_claude.py has no
   equivalent tier - Claude Code's in-memory rate-limit state is never
   written to disk on its own (see ../../bin/ingest-claude-statusline.sh), so
   polling is Claude's *only* source of truth even mid-session.
2. Otherwise, the Codex statusline's heartbeat/codex file (written by the
   separate agent-statusline project, same shape as heartbeat/claude) is
   fresh within
   HEARTBEAT_ACTIVE_WINDOW_SECONDS - a statusline is open and idle, so the
   local file above is stale, but someone is watching and other
   sessions/devices on the account can still move the meter. Poll at
   WATCHED_POLL_INTERVAL_SECONDS, which must equal the LaunchAgent's own
   tick exactly (60s) - a slower threshold degrades to half the intended
   cadence, since the skip window then spans more than one tick and never
   clears it on a single tick's elapsed time (proven during
   the 2026-08-31 merge into agent-statusline, see poll_claude.py's history).
3. Neither - nothing local to lean on and nobody watching. Poll only if the
   last logged reading is IDLE_POLL_INTERVAL_SECONDS old, so a fully idle
   machine settles to the old flat 5-minute cadence instead of a 60s
   busy-loop for no reason. This tier is genuinely a backstop, same as
   poll_claude.py's idle cadence - just here to keep the log from going
   dark for long unattended stretches, not to catch anything reliably.

Deliberately no dependency on poll_claude.py for any of this - only on
agent-statusline's heartbeat file, read-only, which degrades gracefully to
tier 3 if the statusline is not installed.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import _quota_common

# src/quota_polling/ is deployed two levels under the runtime root
# (~/opt/agent-usage-tracker/src/quota_polling/) - parent.parent.parent,
# not parent.parent, or this would look for a nonexistent
# src/data/ instead of the real sibling-of-src/ data/.
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
QUOTA_LOG_FILE = DATA_DIR / "codex" / "account.jsonl"  # account scope: the meter (USAGE_DATA_REFERENCE.md §1)
SESSIONS_DIR = Path.home() / ".codex" / "sessions"
HEARTBEAT_FILE = Path.home() / "opt" / "agent-statusline" / "state" / "heartbeat" / "codex"

# See the module docstring for the three-tier gating these constants drive.
SESSION_FRESH_SECONDS = 300
HEARTBEAT_ACTIVE_WINDOW_SECONDS = 90
WATCHED_POLL_INTERVAL_SECONDS = 60  # must equal the LaunchAgent's StartInterval exactly
IDLE_POLL_INTERVAL_SECONDS = 300

# launchd runs jobs with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin) that
# doesn't include ~/.local/bin, where this user's `codex` symlink lives (see
# ~/dev/CLAUDE.md's "~/.local/bin/ - symlinks into ~/opt/<project>/"
# convention) - shutil.which() alone would silently fail under the
# LaunchAgent even though `codex` works fine from an interactive shell.
CODEX_BIN = shutil.which("codex") or str(Path.home() / ".local" / "bin" / "codex")
RPC_TIMEOUT_S = 10


class RpcError(Exception):
    """Wraps a typed (stage, type, detail?) error dict, so main() can catch
    one exception type regardless of which step of the handshake failed."""
    def __init__(self, d_error: dict):
        self.d_error = d_error


def _read_response(proc, request_id: int) -> dict:
    """Reads lines from the app-server's stdout until the one matching
    request_id, skipping unsolicited server notifications (e.g.
    remoteControl/status/changed) along the way. Raises RpcError on
    timeout, malformed JSON, process exit, or a JSON-RPC `error` object."""
    import select

    deadline = time.monotonic() + RPC_TIMEOUT_S
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RpcError({"stage": "timeout", "type": "ReadTimeout",
                            "detail": f"no response to request {request_id} within {RPC_TIMEOUT_S}s"})
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            continue  # loop back, deadline check above will catch true timeout
        line = proc.stdout.readline()
        if not line:
            raise RpcError({"stage": "spawn", "type": "ProcessExited",
                            "detail": f"stdout closed before request {request_id} answered"})
        line = line.strip()
        if not line:
            continue
        try:
            d_msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RpcError({"stage": "parse", "type": "JSONDecodeError", "detail": exc.msg})
        if d_msg.get("id") != request_id:
            continue  # a notification, or a response to an id we didn't send
        if "error" in d_msg:
            err = d_msg["error"]
            raise RpcError({"stage": "rpc", "type": "JSONRPCError",
                            "detail": f"{err.get('code')}: {err.get('message')}"})
        return d_msg.get("result", {})


def fetch_codex_state() -> tuple[dict | None, dict | None, dict | None]:
    """Returns (d_rate_limits, d_usage, d_error); d_error is None on success."""
    try:
        proc = subprocess.Popen(
            [CODEX_BIN, "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except (FileNotFoundError, OSError) as exc:
        return None, None, {"stage": "spawn", "type": type(exc).__name__}

    def send(d_obj):
        proc.stdin.write(json.dumps(d_obj) + "\n")
        proc.stdin.flush()

    try:
        send({"id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "agent-quota-tracker", "version": "0.1"}}})
        _read_response(proc, 1)

        send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        d_rate_limits = _read_response(proc, 2)

        send({"id": 3, "method": "account/usage/read", "params": {}})
        d_usage = _read_response(proc, 3)

        return d_rate_limits, d_usage, None
    except RpcError as exc:
        return None, None, exc.d_error
    except BrokenPipeError as exc:
        # stdin write failed - the subprocess died before/while we wrote to it.
        return None, None, {"stage": "spawn", "type": type(exc).__name__}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def _last_codex_log_ts() -> int | None:
    """Timestamp of the last codex-sourced row, from the tail of the log
    (see _quota_common.tail_json_rows). The source filter is now redundant
    in the common case (the Codex log only ever gets
    `source: "codex"` rows written to it since the 2026-08-31 per-provider
    split), but kept as a cheap defensive check against a stray/malformed
    row rather than trusting file identity alone."""
    for d_row in reversed(_quota_common.tail_json_rows(QUOTA_LOG_FILE)):
        if d_row.get("source") == "codex":
            return d_row.get("ts")
    return None


def _codex_session_recently_active(now: float) -> bool:
    """True if some Codex session rollout file was modified within the
    last SESSION_FRESH_SECONDS - meaning a live session already has a
    fresher rate_limits snapshot on disk than polling would get us (see
    module docstring). Only scans today's and yesterday's local-date
    directories (~/.codex/sessions/YYYY/MM/DD/, bucketed by *local* time -
    confirmed empirically, do NOT swap in time.gmtime() here or this reads
    the wrong folder near a UTC/local day-boundary mismatch), not the
    whole tree - that tree only grows forever and this runs every tick.
    Yesterday's directory still matters because a session that started
    just before local midnight keeps appending to its original file, which
    stays filed under yesterday's date even as its mtime ticks past
    midnight."""
    for days_back in (0, 1):
        lt = time.localtime(now - days_back * 86400)
        day_dir = SESSIONS_DIR / f"{lt.tm_year:04d}" / f"{lt.tm_mon:02d}" / f"{lt.tm_mday:02d}"
        if not day_dir.is_dir():
            continue
        for path in day_dir.glob("*.jsonl"):
            try:
                if now - path.stat().st_mtime < SESSION_FRESH_SECONDS:
                    return True
            except OSError:
                continue  # file removed/rotated mid-check - not "active"
    return False


def main() -> None:
    QUOTA_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    now = time.time()
    if _codex_session_recently_active(now):
        print(f"skip: a Codex session file was modified under {SESSION_FRESH_SECONDS}s ago "
              f"- local rate_limits snapshot is already fresher than a poll would be")
        return
    is_watched = _quota_common.is_fresh(HEARTBEAT_FILE, HEARTBEAT_ACTIVE_WINDOW_SECONDS, now)
    threshold = WATCHED_POLL_INTERVAL_SECONDS if is_watched else IDLE_POLL_INTERVAL_SECONDS
    last_ts = _last_codex_log_ts()
    if last_ts is not None and (now - last_ts) < threshold:
        print(f"skip: last codex reading is under {threshold}s old "
              f"({'watched' if threshold == WATCHED_POLL_INTERVAL_SECONDS else 'idle'})")
        return

    d_rate_limits, d_usage, d_error = fetch_codex_state()

    d_record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "codex",
        "codex_rate_limits": d_rate_limits,
        "codex_usage": d_usage,
        "error": d_error,  # None on success; why the reading is missing otherwise
    }
    with QUOTA_LOG_FILE.open("a") as f:
        f.write(json.dumps(d_record) + "\n")


if __name__ == "__main__":
    main()
