#!/usr/bin/env python3
"""One-time, idempotent migration (2026-10-04): move failed-poll rows out of
the account-scope data files into the pollers' error logs.

    data/claude/account.jsonl  --(rows with a non-null `error`)-->  logs/claude-poll-errors.jsonl
    data/codex/account.jsonl   --(rows with a non-null `error`)-->  logs/codex-poll-errors.jsonl

Since 2026-10-04 the pollers write a failed attempt to logs/ instead of
data/ (USAGE_DATA_REFERENCE.md §6), so a reader of data/ never sees an error
row. This moves the ones written before that. Every other line - readings,
push rows, lines that don't parse - stays in account.jsonl, byte-identical
and in its original order; moved rows are byte-identical too.

Mechanics, per account file that still holds error rows:
1. Copy it to data/_archive/<agent>-account.jsonl.<UTC stamp> and check the
   copy's size. Nothing is deleted; KEEP the archive (the 2026-09-30
   migration's archive was removed by hand and is gone).
2. Write the kept lines to a temp file and atomically replace account.jsonl;
   write the moved lines, followed by whatever the error log already held,
   to a temp file and atomically replace the error log.
3. Print the counts and check they reconcile: archived = kept + moved.

Writers must be paused while it runs (the poll LaunchAgent booted out, the
deployed ingest script made non-executable so the statusline skips it), or
a row appended between the copy and the replace is lost - the ingest script
re-writes its reading on the next render, but a poll would be gone. It
refuses to run if account.jsonl changes size while it works.

Idempotent: once no account file holds an error row, a run is a no-op.

Usage: python3 split_errors.py [--runtime-dir ~/opt/agent-usage-tracker]
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, default=Path.home() / "opt" / "agent-usage-tracker")
    runtime_dir = parser.parse_args().runtime_dir.expanduser()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    ok = True

    def is_error(line: bytes) -> bool:
        # A row is a failed poll when it parses and its `error` is set; a
        # line that doesn't parse is kept as-is, never guessed at.
        try:
            d_row = json.loads(line)
        except ValueError:
            return False
        return isinstance(d_row, dict) and d_row.get("error") is not None

    def replace(path: Path, data: bytes) -> None:
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_bytes(data)
        tmp.replace(path)

    for agent in ("claude", "codex"):
        account = runtime_dir / "data" / agent / "account.jsonl"
        error_log = runtime_dir / "logs" / f"{agent}-poll-errors.jsonl"
        if not account.exists():
            print(f"{agent}: no account.jsonl, nothing to do")
            continue
        raw = account.read_bytes()
        l_lines = raw.splitlines(keepends=True)
        l_moved = [line for line in l_lines if is_error(line)]
        if not l_moved:
            print(f"{agent}: no error rows, nothing to do")
            continue
        l_kept = [line for line in l_lines if not is_error(line)]

        # 1. Archive, and check the copy before touching the original.
        archive = runtime_dir / "data" / "_archive" / f"{agent}-account.jsonl.{stamp}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(account, archive)
        if archive.stat().st_size != len(raw):
            sys.exit(f"{agent}: archive {archive} is {archive.stat().st_size} bytes, expected {len(raw)} - stopping")

        # 2. Replace, unless a writer appended meanwhile (writers not paused).
        if account.stat().st_size != len(raw):
            sys.exit(f"{agent}: account.jsonl grew while migrating - pause the writers and re-run")
        existing_errors = error_log.read_bytes() if error_log.exists() else b""
        error_log.parent.mkdir(parents=True, exist_ok=True)
        replace(error_log, b"".join(l_moved) + existing_errors)
        replace(account, b"".join(l_kept))

        # 3. Reconcile.
        n_archived, n_kept, n_moved = len(l_lines), len(l_kept), len(l_moved)
        reconciles = n_archived == n_kept + n_moved and account.stat().st_size + sum(map(len, l_moved)) == len(raw)
        ok = ok and reconciles
        print(f"{agent}: archived {n_archived} lines to {archive}; kept {n_kept}, moved {n_moved} "
              f"to {error_log} - {'reconciles' if reconciles else 'DOES NOT RECONCILE'}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
