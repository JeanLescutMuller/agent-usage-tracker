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

  pause JOB
           (step 1.1) unloads that job's trigger on this machine (launchd
           bootout / systemctl --user stop of its timer); install.sh loads it
           again. Idempotent.
  cat1-shared --peer HOST
           (step 1.2, on the Mac) the Claude readings present in both this
           machine's and HOST's account_quotas.db (copied by the old --peer
           exchange): their row_hash, one per line, into
           data/_archive/cat1_shared.txt. Their machine is unknown.
  sync-init
           (step 1.5, on the Mac, BEFORE cat1-seed-vm) state/sync/claude_quotas.json:
           sent = seeded_through = the push watermark of claude/account_quotas
           (every Mac reading up to it is in the VM's central copy, which
           cat1-seed-vm copies), received = 0 (all the VM's own readings
           come here). Does nothing when the file exists.
  cat1-machine --shared FILE
           (step 1.3, every machine; again right after the deploy, to label
           the readings the old code wrote meanwhile; when it labels one
           above seeded_through that a sync already passed over, it moves
           the sync's `sent` back so the next sync sends it) for data/claude/ and
           every central/<machine>/data/claude/ account_quotas.db: a copy
           into data/_archive/ before the first change, the `machine`
           column, filled where NULL - with this machine's name (or the
           central folder's) except the shared readings - the `latest` view
           with `machine`, user_version 2.
  cat1-seed-vm --shared FILE [--from-machine NAME]
           (step 1.4, on the VM) every reading of
           central/NAME/data/claude/account_quotas.db into
           data/claude/account_quotas.db (INSERT OR IGNORE; machine = NAME,
           shared readings NULL); then checks that none is missing.

Usage:
  python3 adhoc_quotas_analysis/migrate_to_target.py STEP [options] [--runtime DIR]
"""
import argparse
import json
import os
import shutil
import subprocess
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("step", choices=["backup", "pause", "cat1-shared", "sync-init", "cat1-machine", "cat1-seed-vm"])
parser.add_argument("job", nargs="?", help="pause: the job (e.g. claude-quota)")
parser.add_argument("--runtime", type=Path, default=Path.home() / "opt" / "agent-usage-tracker")
parser.add_argument("--peer", help="cat1-shared: the other machine (ssh alias)")
parser.add_argument("--shared", type=Path, help="cat1-machine, cat1-seed-vm: the file cat1-shared wrote")
parser.add_argument("--from-machine", default="chan-lescut-macbook-pro", help="cat1-seed-vm: the central folder to copy")
args = parser.parse_args()

# The schema and machine_name() come from usage_db, which reads the runtime at import.
os.environ["AGENT_USAGE_TRACKER_RUNTIME"] = str(args.runtime)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usage_db  # noqa: E402

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


ACCOUNT_COLUMNS = ("ts, dt, source, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts, "
                   "observed_by_session, written_ts, raw, row_hash")
# The latest view as usage_db.ACCOUNT_SCHEMA creates it on a new database.
LATEST_VIEW = usage_db.ACCOUNT_SCHEMA[usage_db.ACCOUNT_SCHEMA.index("CREATE VIEW latest"):].strip().rstrip(";")


def open_rw(path: Path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.execute("PRAGMA busy_timeout = 10000")
    return db


def read_shared() -> set:
    if not args.shared or not args.shared.is_file():
        sys.exit("--shared FILE: the file cat1-shared wrote (copy it to this machine first)")
    return {line.strip() for line in args.shared.read_text().splitlines() if line.strip()}


def by_machine(db) -> str:
    return ", ".join(f"{m or 'NULL'} {n}" for m, n in db.execute(
        "SELECT machine, COUNT(*) FROM account_quotas GROUP BY machine ORDER BY machine"))


def pause() -> None:
    label = f"com.jeanlescut.agent-usage-tracker.{args.job}"
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        result = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
    else:
        result = subprocess.run(["systemctl", "--user", "stop", f"{label}.timer"], capture_output=True, text=True)
    say(f"{label}: unloaded (exit {result.returncode}{': ' + result.stderr.strip() if result.returncode else ''}); "
        "install.sh loads it again")


def cat1_shared() -> None:
    local = open_rw(args.runtime / "data" / "claude" / "account_quotas.db")
    local.execute("PRAGMA query_only = ON")
    remote = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", args.peer, "python3 -c \"import sqlite3, os; "
         "db = sqlite3.connect(os.path.expanduser('~/opt/agent-usage-tracker/data/claude/account_quotas.db')); "
         "db.execute('PRAGMA query_only = ON'); "
         "[print('HASH', h) for (h,) in db.execute('SELECT row_hash FROM account_quotas')]\""],
        capture_output=True, text=True, timeout=120, check=True).stdout
    s_peer = {line.split()[1] for line in remote.splitlines() if line.startswith("HASH ")}
    s_local = {h for (h,) in local.execute("SELECT row_hash FROM account_quotas")}
    s_both = s_local & s_peer
    out = args.runtime / "data" / "_archive" / "cat1_shared.txt"
    out.write_text("".join(f"{h}\n" for h in sorted(s_both)))
    by_source = {}
    for h in s_both:
        source = local.execute("SELECT source FROM account_quotas WHERE row_hash = ?", (h,)).fetchone()[0]
        by_source[source] = by_source.get(source, 0) + 1
    say(f"here {len(s_local)}, {args.peer} {len(s_peer)}, in both {len(s_both)} {by_source} -> {out}")


def sync_init() -> None:
    state = args.runtime / "state" / "sync" / "claude_quotas.json"
    if state.exists():
        say(f"{state} exists, unchanged: {state.read_text().strip()}")
        return
    d_marks = json.loads((args.runtime / "state" / "push" / "watermarks.json").read_text())
    d_state = {"sent": d_marks["claude/account_quotas"], "received": 0, "last_ok_ts": 0,
               "seeded_through": d_marks["claude/account_quotas"]}
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(d_state))
    say(f"{state}: {d_state}")


def cat1_machine() -> None:
    s_shared = read_shared()
    me = usage_db.machine_name()
    l_dbs = [(args.runtime / "data" / "claude" / "account_quotas.db", me)] + [
        (p, p.parents[2].name) for p in sorted((args.runtime / "central").glob("*/data/claude/account_quotas.db"))
        if "_archive" not in p.parts]
    for path, machine in l_dbs:
        rel = path.relative_to(args.runtime)
        db = open_rw(path)
        if "machine" not in [r[1] for r in db.execute("PRAGMA table_info(account_quotas)")]:
            copy_path = ARCHIVE / f"{str(rel).replace('/', '__')}.pre-machine-{STAMP}.db"
            copy = sqlite3.connect(copy_path)
            db.backup(copy)
            copy.close()
            say(f"{rel}: copied to {copy_path.name}")
            db.execute("ALTER TABLE account_quotas ADD COLUMN machine TEXT")
        before = by_machine(db)
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TEMP TABLE IF NOT EXISTS shared (row_hash TEXT PRIMARY KEY)")
        db.executemany("INSERT OR IGNORE INTO temp.shared VALUES (?)", [(h,) for h in s_shared])
        where = "machine IS NULL AND row_hash NOT IN (SELECT row_hash FROM temp.shared)"
        l_rowids = [r[0] for r in db.execute(f"SELECT rowid FROM account_quotas WHERE {where}")]
        n = db.execute(f"UPDATE account_quotas SET machine = ? WHERE {where}", (machine,)).rowcount
        db.execute("DROP VIEW IF EXISTS latest")
        db.execute(LATEST_VIEW)
        db.execute(f"PRAGMA user_version = {usage_db.SCHEMA_VERSION}")
        db.execute("COMMIT")
        say(f"{rel}: {n} rows labelled {machine}; before: {before}; after: {by_machine(db)}")
        # A reading the old code wrote after the seeded point, labelled only now: a sync that already ran
        # skipped it (machine was NULL), so `sent` goes back below it (re-sending a row is a no-op there).
        state = args.runtime / "state" / "sync" / "claude_quotas.json"
        if machine == me and state.exists():
            d_state = json.loads(state.read_text())
            l_late = [r for r in l_rowids if r > d_state.get("seeded_through", d_state["sent"])]
            if l_late and min(l_late) <= d_state["sent"]:
                say(f"sync: sent {d_state['sent']} -> {min(l_late) - 1}, so the {len(l_late)} rows just labelled go")
                d_state["sent"] = min(l_late) - 1
                state.write_text(json.dumps(d_state))


def cat1_seed_vm() -> None:
    s_shared = read_shared()
    source = args.runtime / "central" / args.from_machine / "data" / "claude" / "account_quotas.db"
    db = open_rw(args.runtime / "data" / "claude" / "account_quotas.db")
    db.execute("ATTACH DATABASE ? AS src", (str(source),))
    before = by_machine(db)
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TEMP TABLE IF NOT EXISTS shared (row_hash TEXT PRIMARY KEY)")
    db.executemany("INSERT OR IGNORE INTO temp.shared VALUES (?)", [(h,) for h in s_shared])
    n = db.execute(f"INSERT OR IGNORE INTO main.account_quotas ({ACCOUNT_COLUMNS}, machine) "
                   f"SELECT {ACCOUNT_COLUMNS}, CASE WHEN row_hash IN (SELECT row_hash FROM temp.shared) THEN NULL ELSE ? END "
                   "FROM src.account_quotas ORDER BY rowid", (args.from_machine,)).rowcount
    db.execute("COMMIT")
    missing = db.execute("SELECT COUNT(*) FROM src.account_quotas s WHERE NOT EXISTS "
                         "(SELECT 1 FROM main.account_quotas m WHERE m.row_hash = s.row_hash)").fetchone()[0]
    say(f"{n} readings copied from {source.relative_to(args.runtime)}; before: {before}; after: {by_machine(db)}; missing: {missing}")
    if missing:
        sys.exit(f"{missing} readings missing after the copy")


{"backup": backup, "pause": pause, "cat1-shared": cat1_shared, "sync-init": sync_init,
 "cat1-machine": cat1_machine, "cat1-seed-vm": cat1_seed_vm}[args.step]()
