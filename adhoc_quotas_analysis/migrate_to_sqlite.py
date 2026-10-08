#!/usr/bin/env python3
"""One-time, idempotent, hand-run migration (2026-10-08): the old JSONL files
-> the SQLite databases, through the gates (../src/ingest_*.py), every line
kept verbatim in `raw`. Run on 2026-10-08; the result is recorded in
../USAGE_DATA_REFERENCE.md §6. The cutover it was part of, in order:
  1. tar data/, logs/, state/ into data/_archive/pre-sqlite-<stamp>.tar.gz;
  2. copy logs/*-poll-errors.jsonl to data/_archive/pre-sqlite-<stamp>/logs/
     (uninstall.sh deletes logs/);
  3. uninstall.sh && install.sh (the writers switch to the databases; the old
     files are frozen from then on);
  4. this script with --logs <that copy>, then again with --archive.

  data/<agent>/account.jsonl           -> data/<agent>/account_quotas.db  (account_quotas)
  <logs>/<agent>-poll-errors.jsonl     -> data/<agent>/account_quotas.db  (poll_errors)
  data/claude/<session-id>.jsonl       -> data/claude/sessions_usages.db  (session_snapshots, telemetry)
  data/_unattributed/claude-otel.jsonl -> data/claude/sessions_usages.db  (telemetry, session_id NULL)

History is kept as is: no "redundant reading" dedup, only identical lines
collapse (row_hash). Then every distinct line is looked up by its row_hash:
the run reports, per file, lines / distinct / inserted / already there /
missing, and fails if anything is missing or rejected. Only then, and only
with --archive, are the old files moved to data/_archive/pre-sqlite-<stamp>/.
Re-running inserts nothing new.

Usage:
  python3 adhoc_quotas_analysis/migrate_to_sqlite.py [--runtime DIR] [--logs DIR] [--archive]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--runtime", type=Path, default=Path.home() / "opt" / "agent-usage-tracker")
parser.add_argument("--logs", type=Path, help="where the *-poll-errors.jsonl files are (default: <runtime>/logs)")
parser.add_argument("--archive", action="store_true", help="move the old files away once everything verified")
args = parser.parse_args()

# The gates read the runtime from the environment at import time.
os.environ["AGENT_USAGE_TRACKER_RUNTIME"] = str(args.runtime)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ingest_account_quota  # noqa: E402
import ingest_session_usage  # noqa: E402
import usage_db  # noqa: E402

DATA = args.runtime / "data"
LOGS = args.logs or args.runtime / "logs"

# (file, agent, kind, session id): kind is account, session or telemetry.
l_inputs = []
for agent in usage_db.AGENTS:
    l_inputs.append((DATA / agent / "account.jsonl", agent, "account", None))
    l_inputs.append((LOGS / f"{agent}-poll-errors.jsonl", agent, "account", None))
for path in sorted((DATA / "claude").glob("*.jsonl")):
    if path.name != "account.jsonl":
        l_inputs.append((path, "claude", "session", path.stem))
l_inputs.append((DATA / "_unattributed" / "claude-otel.jsonl", "claude", "session", None))
l_inputs = [t for t in l_inputs if t[0].is_file()]

l_report, failed = [], False
for path, agent, kind, session_id in l_inputs:
    db = usage_db.open_account(agent) if kind == "account" else usage_db.open_sessions(agent)
    l_lines = [line.rstrip("\n") for line in path.open(encoding="utf-8")]
    l_lines = [line for line in l_lines if line.strip()]
    d_counts = {"inserted": 0, "duplicate": 0, "redundant": 0}
    l_rejected, l_hashes = [], []
    db.execute("BEGIN IMMEDIATE")
    for n, raw in enumerate(l_lines, 1):
        try:
            d_row = json.loads(raw)
            if kind == "account":
                result = ingest_account_quota.add_row(db, agent, d_row, raw, dedup=False, update_state=False)
                l_hashes.append(("poll_errors" if d_row.get("error") is not None else "account_quotas", usage_db.row_hash(raw)))
            elif d_row.get("source") == "claude_otel":
                result = ingest_session_usage.add_telemetry(db, d_row, raw)
                l_hashes.append(("telemetry", usage_db.row_hash(raw)))
            else:
                result = ingest_session_usage.add_snapshot(db, session_id, d_row, raw, dedup=False)
                l_hashes.append(("session_snapshots", usage_db.row_hash(raw, session_id)))
            d_counts[result] += 1
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            l_rejected.append(f"{path.name}:{n}: {type(exc).__name__}: {exc}")
        if n % 20000 == 0:
            db.execute("COMMIT")
            db.execute("BEGIN IMMEDIATE")
    db.execute("COMMIT")
    # Verification: every line accepted above is findable by its identity.
    missing = sum(1 for table, h in set(l_hashes)
                  if db.execute(f"SELECT 1 FROM {table} WHERE row_hash = ?", (h,)).fetchone() is None)
    failed |= bool(missing or l_rejected)
    l_report.append((str(path.relative_to(args.runtime) if path.is_relative_to(args.runtime) else path),
                     len(l_lines), len(set(l_lines)), d_counts["inserted"], d_counts["duplicate"], missing, len(l_rejected)))
    for message in l_rejected[:5]:
        print("  rejected", message, file=sys.stderr)
    db.close()

print(f"{'file':52} {'lines':>8} {'distinct':>8} {'inserted':>8} {'there':>8} {'missing':>8} {'rejected':>8}")
for row in l_report:
    print(f"{row[0]:52} " + " ".join(f"{v:>8}" for v in row[1:]))
print("totals" + " " * 47 + " ".join(f"{sum(r[i] for r in l_report):>8}" for i in range(1, 7)))

if failed:
    print("\nFAILED: some lines are missing or were rejected; nothing archived.", file=sys.stderr)
    sys.exit(1)
print("\nOK: every distinct line is in a database.")
if args.archive:
    archive = DATA / "_archive" / f"pre-sqlite-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    # Only files under data/: the error logs were already copied aside
    # before uninstall.sh (which deletes logs/), see step 2 above.
    l_moved = [path for path, *_ in l_inputs if path.is_relative_to(DATA) and not path.is_relative_to(DATA / "_archive")]
    for path in l_moved:
        target = archive / path.relative_to(args.runtime)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
    print(f"archived {len(l_moved)} files to {archive}")
