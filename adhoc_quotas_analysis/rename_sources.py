#!/usr/bin/env python3
"""One-time, idempotent migration (2026-10-04): rename the pollers' generic
`source` values to say what they read.

    source "claude"  ->  "claude_api"         poll_claude.py, GET /api/oauth/usage
    source "codex"   ->  "codex_app_server"   poll_codex.py, `codex app-server` JSON-RPC

in data/claude/account.jsonl, logs/claude-poll-errors.jsonl,
data/codex/account.jsonl and logs/codex-poll-errors.jsonl, and the latest-
reading file state/quota/claude's source field to the same names as the
rows: "API"/"P" -> "claude_api", "statusline"/"X" -> "claude_statusline".
`claude_statusline` and `codex_plan_limit_history` rows are untouched.

Only the `source` value changes: the first `"source": "<old>"` in a line
(the top-level key - every writer puts it before any payload) is replaced
in the text, so each row keeps its exact bytes otherwise, and every changed
line is re-parsed to check that `source` is the only field that differs.
Lines that don't parse are left as they are.

Mechanics, per file that still holds an old value:
1. Copy it to data/_archive/<name>.<UTC stamp>.pre-rename and check the
   copy's size. Nothing is deleted; keep the archive.
2. Write the renamed lines to a temp file and atomically replace the file.
3. Print the counts: lines in = lines out, renamed = old values found.

Writers must be paused while it runs (the poll LaunchAgent booted out, the
deployed ingest script made non-executable), and the new code deployed
first, so nothing writes an old value afterwards. It refuses to replace a
file that changed size while it worked.

Idempotent: once no old value is left, a run is a no-op.

Usage: python3 rename_sources.py [--runtime-dir ~/opt/agent-usage-tracker]
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

D_RENAMES = {"claude": "claude_api", "codex": "codex_app_server"}
D_STATE_RENAMES = {"API": "claude_api", "P": "claude_api", "statusline": "claude_statusline", "X": "claude_statusline"}
STATE_SEP = "\x1c"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, default=Path.home() / "opt" / "agent-usage-tracker")
    runtime_dir = parser.parse_args().runtime_dir.expanduser()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive_dir = runtime_dir / "data" / "_archive"
    ok = True

    def replace(path: Path, data: bytes) -> None:
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_bytes(data)
        tmp.replace(path)

    def archive(path: Path, raw: bytes) -> None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / f"{path.parent.name}-{path.name}.{stamp}.pre-rename"
        shutil.copyfile(path, dest)
        if dest.stat().st_size != len(raw):
            sys.exit(f"archive {dest} is {dest.stat().st_size} bytes, expected {len(raw)} - stopping")
        if path.stat().st_size != len(raw):
            sys.exit(f"{path} changed while migrating - pause the writers and re-run")

    # One pattern per old value: `"source": "claude"` or `"source":"claude"`,
    # closing quote included, so "claude_statusline" never matches.
    d_patterns = {old: re.compile(rb'"source":( ?)"' + old.encode() + rb'"') for old in D_RENAMES}

    for rel in ("data/claude/account.jsonl", "logs/claude-poll-errors.jsonl",
                "data/codex/account.jsonl", "logs/codex-poll-errors.jsonl"):
        path = runtime_dir / rel
        if not path.exists():
            print(f"{rel}: absent, nothing to do")
            continue
        raw = path.read_bytes()
        l_lines = raw.splitlines(keepends=True)
        l_out, n_renamed, n_bad = [], 0, 0
        for line in l_lines:
            # Only a line whose top-level `source` is an old value is
            # touched: a payload can carry its own nested "source" key.
            try:
                d_old = json.loads(line)
            except ValueError:
                d_old = None
            old = d_old.get("source") if isinstance(d_old, dict) else None
            if old not in D_RENAMES:
                l_out.append(line)
                continue
            new = D_RENAMES[old].encode()
            new_line = d_patterns[old].sub(lambda m: b'"source":' + m.group(1) + b'"' + new + b'"', line, count=1)
            # The text edit must have renamed the top-level key and nothing
            # else (it would not if a nested match came first).
            try:
                if json.loads(new_line) != dict(d_old, source=D_RENAMES[old]):
                    n_bad += 1
            except ValueError:
                n_bad += 1
            n_renamed += 1
            l_out.append(new_line)
        if n_bad:
            ok = False
            print(f"{rel}: {n_bad} line(s) would change more than `source` - left untouched")
            continue
        if not n_renamed:
            print(f"{rel}: no old source value, nothing to do")
            continue
        archive(path, raw)
        replace(path, b"".join(l_out))
        reconciles = len(l_out) == len(l_lines)
        ok = ok and reconciles
        print(f"{rel}: renamed {n_renamed} of {len(l_lines)} lines - {'reconciles' if reconciles else 'DOES NOT RECONCILE'}")

    # The latest-reading file: one line, six fields, source is the fifth.
    state = runtime_dir / "state" / "quota" / "claude"
    if state.exists():
        l_fields = state.read_text().rstrip("\n").split(STATE_SEP)
        if len(l_fields) == 6 and l_fields[4] in D_STATE_RENAMES:
            old = l_fields[4]
            l_fields[4] = D_STATE_RENAMES[old]
            replace(state, (STATE_SEP.join(l_fields) + "\n").encode())
            print(f"state/quota/claude: source {old} -> {l_fields[4]}")
        else:
            print("state/quota/claude: no old source value, nothing to do")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
