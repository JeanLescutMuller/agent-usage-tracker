#!/usr/bin/env python3
"""One-time, idempotent, hand-run migration (2026-10-10): one name per
machine. The Mac's network-derived hostname drifted on 2026-10-08
(chan-lescut-macbook-pro-1 -> Chan-Lescut-MacBook-Pro -> chan-lescut-macbook-pro),
so its rows carry three `machine` values and the VM holds two central
folders for it. usage_db.machine_name() now follows job-runner's rule
(LocalHostName in lower case), and the Mac has a fixed HostName.

On any machine, for every sessions_usages.db under data/ and central/:
  requests.machine IN (--from names) -> --to name.
On the central machine (the VM), for every central/<from>/ folder:
  every row of every table is inserted into the same database under
  central/<to>/ (INSERT OR IGNORE: row_hash / request_id make a row already
  there a no-op; scans has no key and is simply added), in one transaction
  per database - a push arriving meanwhile waits for the busy timeout or is
  retried by its next run. Every row of the old folder is then looked up in
  the new one (by its key; scans by count); only if none is missing is the
  old folder moved to central/_archive/<from>-merged-<stamp>/.

Before anything, a tar of data/ and central/ goes to
central/_archive/ (or data/_archive/ without central/).
Re-running finds no old folder and nothing to relabel: it changes nothing.

Usage:
  python3 adhoc_quotas_analysis/merge_machine_names.py --to chan-lescut-macbook-pro \\
      --from chan-lescut-macbook-pro-1 Chan-Lescut-MacBook-Pro [--runtime DIR]
"""
import argparse
import os
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--runtime", type=Path, default=Path.home() / "opt" / "agent-usage-tracker")
parser.add_argument("--to", required=True, help="the one name to keep")
parser.add_argument("--from", dest="l_from", nargs="+", required=True, help="the names it replaces")
args = parser.parse_args()

# The schema and connect() come from usage_db, which reads the runtime at import.
os.environ["AGENT_USAGE_TRACKER_RUNTIME"] = str(args.runtime)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usage_db  # noqa: E402

DATA, CENTRAL = args.runtime / "data", args.runtime / "central"
STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
# The key that identifies a row, per table; None: no key (compared by count).
D_KEYS = {"account_quotas": "row_hash", "poll_errors": "row_hash", "session_snapshots": "row_hash",
          "telemetry": "row_hash", "requests": "request_id", "scans": None}
ph = ",".join("?" * len(args.l_from))

# One entry per real folder: on a case-insensitive disk (macOS) Old-Name and
# old-name are the same folder, and may even be the target itself.
l_old_dirs = []
for name in args.l_from:
    d = CENTRAL / name
    target = CENTRAL / args.to
    if d.is_dir() and not (target.exists() and d.samefile(target)) and not any(d.samefile(o) for o in l_old_dirs):
        l_old_dirs.append(d)
l_relabel = sorted(set(DATA.glob("*/sessions_usages.db")) | set(CENTRAL.glob("*/data/*/sessions_usages.db")))
l_relabel = [p for p in l_relabel if "_archive" not in p.parts]

# 1. Backup, only when there is something to do.
todo = bool(l_old_dirs) or any(
    sqlite3.connect(p).execute(f"SELECT 1 FROM requests WHERE machine IN ({ph}) LIMIT 1", args.l_from).fetchone()
    for p in l_relabel)
if not todo:
    print("nothing to do: no old folder, no old name in requests")
    sys.exit(0)
archive = (CENTRAL if CENTRAL.is_dir() else DATA) / "_archive"
archive.mkdir(parents=True, exist_ok=True)
backup = archive / f"pre-merge-machine-names-{STAMP}.tar.gz"
with tarfile.open(backup, "w:gz") as tar:
    for d in (DATA, CENTRAL):
        if d.is_dir():
            tar.add(d, arcname=d.name, filter=lambda ti: None if "/_archive" in ti.name else ti)
print(f"backup: {backup}")

# 2. Merge every old central folder into the new one.
for old_dir in l_old_dirs:
    for old_db in sorted(old_dir.glob("data/*/*.db")):
        agent, kind = old_db.parent.name, old_db.stem
        new_db = CENTRAL / args.to / "data" / agent / old_db.name
        new_db.parent.mkdir(parents=True, exist_ok=True)
        schema = usage_db.ACCOUNT_SCHEMA if kind == "account_quotas" else usage_db.SESSIONS_SCHEMA
        db = usage_db.connect(new_db, schema)
        db.execute("ATTACH DATABASE ? AS old", (str(old_db),))
        db.execute("BEGIN IMMEDIATE")
        for (table,) in db.execute("SELECT name FROM old.sqlite_master WHERE type = 'table'").fetchall():
            cols = ",".join(r[1] for r in db.execute(f"PRAGMA old.table_info({table})"))
            n_before = db.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0]
            db.execute(f"INSERT OR IGNORE INTO main.{table} ({cols}) SELECT {cols} FROM old.{table} ORDER BY rowid")
            n_old = db.execute(f"SELECT count(*) FROM old.{table}").fetchone()[0]
            n_after = db.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0]
            key = D_KEYS.get(table)
            if key:
                n_missing = db.execute(f"SELECT count(*) FROM old.{table} WHERE {key} NOT IN (SELECT {key} FROM main.{table})").fetchone()[0]
            else:
                n_missing = n_old - (n_after - n_before)
            print(f"{old_dir.name}/{agent}/{kind}.{table}: old {n_old}, new before {n_before}, after {n_after}, missing {n_missing}")
            if n_missing:
                db.execute("ROLLBACK")
                sys.exit(f"rows missing after the merge of {old_db}: rolled back, nothing moved")
        db.execute("COMMIT")
        db.execute("DETACH DATABASE old")
        db.close()
    dest = archive / f"{old_dir.name}-merged-{STAMP}"
    old_dir.rename(dest)
    print(f"moved {old_dir} -> {dest}")

# 3. Relabel requests.machine everywhere (the merged folder included).
l_relabel = sorted(set(DATA.glob("*/sessions_usages.db")) | set(CENTRAL.glob("*/data/*/sessions_usages.db")))
for path in l_relabel:
    if "_archive" in path.parts:
        continue
    db = usage_db.connect(path, usage_db.SESSIONS_SCHEMA)
    with db:
        n = db.execute(f"UPDATE requests SET machine = ? WHERE machine IN ({ph})", [args.to, *args.l_from]).rowcount
    left = db.execute(f"SELECT count(*) FROM requests WHERE machine IN ({ph})", args.l_from).fetchone()[0]
    db.close()
    print(f"{path.relative_to(args.runtime)}: requests relabelled {n}, old names left {left}")
    if left:
        sys.exit("old names left after the relabel")
print("done")
