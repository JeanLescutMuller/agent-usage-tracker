#!/usr/bin/env python3
"""One-time migration: splits the old combined data/quota-log.jsonl into
per-provider files, data/claude-quota-history.jsonl (source claude +
claude_statusline) and data/codex-quota-history.jsonl (source codex).
Preserves row order and content exactly - no reformatting, no reordering,
just partitioning by `source` (rows before 2026-08-30 have no `source` key
at all; they're all pre-Codex, so they default to "claude" - see
AGENTS.md's "Data files" section).

Idempotent and safe to run standalone or from install.sh: no-ops if the
old combined file is missing, or if either new file already exists (so a
half-migrated or already-migrated data/ is never silently re-split or
duplicated). Pass --dry-run to report row counts per destination without
writing anything - useful to sanity-check against a real
~/opt/agent-statusline/data/ before trusting this against irreplaceable
history.

Usage: python3 split_quota_log.py <data_dir> [--dry-run]
"""
import json
import sys
from pathlib import Path


def split(data_dir: Path, dry_run: bool = False) -> None:
    old_file = data_dir / "quota-log.jsonl"
    claude_file = data_dir / "claude-quota-history.jsonl"
    codex_file = data_dir / "codex-quota-history.jsonl"

    if not old_file.exists():
        print(f"skip: {old_file} does not exist")
        return
    if claude_file.exists() or codex_file.exists():
        print(f"skip: {claude_file.name} or {codex_file.name} already exists - already migrated")
        return

    l_claude, l_codex = [], []
    with old_file.open() as f:
        for line in f:
            if not line.strip():
                continue
            d_row = json.loads(line)
            l_target = l_codex if d_row.get("source", "claude") == "codex" else l_claude
            l_target.append(line)

    print(f"{old_file}: {len(l_claude)} claude rows, {len(l_codex)} codex rows")
    if dry_run:
        return

    with claude_file.open("w") as f:
        f.writelines(l_claude)
    with codex_file.open("w") as f:
        f.writelines(l_codex)
    old_file.unlink()
    print(f"wrote {claude_file.name} and {codex_file.name}, removed {old_file.name}")


if __name__ == "__main__":
    l_args = sys.argv[1:]
    dry_run = "--dry-run" in l_args
    l_args = [a for a in l_args if a != "--dry-run"]
    if len(l_args) != 1:
        print("usage: split_quota_log.py <data_dir> [--dry-run]", file=sys.stderr)
        sys.exit(1)
    split(Path(l_args[0]), dry_run=dry_run)
