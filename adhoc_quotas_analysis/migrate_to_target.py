#!/usr/bin/env python3
"""One-time, idempotent, hand-run migration (2026-10): from the design of
2026-10-10 to design/TARGET_SPEC.md, in the phases of design/MIGRATION_PLAN.md.
One subcommand per step of that plan; each is safe to re-run, archives what
it changes first, and prints counts. Every run is also appended to
<runtime>/data/_archive/migrate_to_target.log (data/_archive survives
deploys; the ~/dev checkout on the VM does not, it is rsynced over).

Subcommands (more are added as the phases are built):
  backup   (step 0.1) a consistent copy of every database under data/ and
           central/ (SQLite's backup API: the databases are written while
           this runs, which a plain tar would catch half-written), plus the
           other files of data/, state/ and central/, into
           data/_archive/pre-target-<stamp>.tar.gz. _archive folders and
           WAL side files (-wal, -shm) are left out. Each copied database
           passes `PRAGMA integrity_check`; its row counts are printed.

Usage:
  python3 adhoc_quotas_analysis/migrate_to_target.py backup [--runtime DIR]
"""
import argparse
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("step", choices=["backup"])
parser.add_argument("--runtime", type=Path, default=Path.home() / "opt" / "agent-usage-tracker")
args = parser.parse_args()

RUNTIME = args.runtime
ARCHIVE = RUNTIME / "data" / "_archive"
STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
ARCHIVE.mkdir(parents=True, exist_ok=True)
LOG = open(ARCHIVE / "migrate_to_target.log", "a")


def say(text: str) -> None:
    """To the terminal and to the log, with the time."""
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {args.step}: {text}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def counts(db) -> str:
    """'table rows, ...' for every table of an open database."""
    l_tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return ", ".join(f"{t} {db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}" for t in l_tables)


def backup() -> None:
    out = ARCHIVE / f"pre-target-{STAMP}.tar.gz"
    n_db = n_files = 0
    # The copy is staged on the same disk as the archive, then removed.
    with tempfile.TemporaryDirectory(dir=ARCHIVE, prefix=".staging-") as staging:
        for top in ("data", "state", "central"):
            for path in sorted((RUNTIME / top).rglob("*")) if (RUNTIME / top).is_dir() else []:
                rel = path.relative_to(RUNTIME)
                if "_archive" in rel.parts or not path.is_file() or path.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                dst = Path(staging) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix != ".db":
                    shutil.copy2(path, dst)
                    n_files += 1
                    continue
                # Read-only source (query_only, never mode=ro: AGENTS.md), one-step backup.
                src = sqlite3.connect(path, timeout=10)
                src.execute("PRAGMA query_only = ON")
                copy = sqlite3.connect(dst)
                src.backup(copy)
                src.close()
                check = copy.execute("PRAGMA integrity_check").fetchone()[0]
                say(f"{rel}: {check}; {counts(copy)}")
                copy.close()
                if check != "ok":
                    sys.exit(f"{rel}: the copy fails its integrity check, nothing archived")
                n_db += 1
        with tarfile.open(out, "w:gz") as tar:
            for top in ("data", "state", "central"):
                if (Path(staging) / top).exists():
                    tar.add(Path(staging) / top, arcname=top)
    say(f"{out}: {n_db} databases, {n_files} other files, {out.stat().st_size / 1e6:.1f} MB")


{"backup": backup}[args.step]()
