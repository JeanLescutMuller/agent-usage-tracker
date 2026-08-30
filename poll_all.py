#!/usr/bin/env python3
"""The LaunchAgent entry point: runs every poller as its own subprocess (not
a plain import) so a crash or hang in one can't stop the others - launchd
would treat them as independent jobs if they were registered separately,
this keeps that isolation while still using a single scheduled job. Each
poller remains fully runnable standalone by hand for debugging.

Usage: python3 poll_all.py
"""
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
POLLERS = ("poll_claude.py", "poll_codex.py")


def main() -> None:
    for name in POLLERS:
        result = subprocess.run([sys.executable, str(SCRIPT_DIR / name)])
        if result.returncode != 0:
            # The poller itself already logs structured errors for expected
            # failures (network, auth, etc). A non-zero exit here means it
            # crashed before it could even write that - surface it on
            # stderr so it lands in poll.err, but keep going: one poller's
            # unexpected crash shouldn't skip the others.
            print(f"{name} exited with code {result.returncode}", file=sys.stderr)


if __name__ == "__main__":
    main()
