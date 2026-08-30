#!/usr/bin/env python3
"""Samples Codex CLI's rate-limit/usage state on a timer, same rationale as
poll_claude.py: this side has no history either, a missed reading is
permanently lost. Appends one record per run to data/utilization-log.jsonl
(the same shared file poll_claude.py writes, disambiguated by `source`) -
the full raw `account/rateLimits/read` and `account/usage/read` results,
unfiltered, or an `error` object saying which stage failed and why.

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
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
UTIL_LOG_FILE = DATA_DIR / "utilization-log.jsonl"

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


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    d_rate_limits, d_usage, d_error = fetch_codex_state()

    d_record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "codex",
        "codex_rate_limits": d_rate_limits,
        "codex_usage": d_usage,
        "error": d_error,  # None on success; why the reading is missing otherwise
    }
    with UTIL_LOG_FILE.open("a") as f:
        f.write(json.dumps(d_record) + "\n")


if __name__ == "__main__":
    main()
