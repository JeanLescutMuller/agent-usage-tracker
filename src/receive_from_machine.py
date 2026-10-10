#!/usr/bin/env python3
"""Collector on the central machine (the VM): receives the rows another
machine's push_to_central.py sends - JSON lines on stdin, over ssh - and
hands each one to the same gates that wrote it there, into that machine's
own copy:
    ~/opt/agent-usage-tracker/central/<machine>/data/<agent>/{account_quotas,sessions_usages}.db
One folder per machine, so the copies never conflict, and every central
database is gated like a local one. History is kept as sent (no
"redundant reading" dedup); a row already stored is a no-op, so a re-sent
chunk changes nothing. state/quota/claude is never touched here.

Line shapes (built by push_to_central.py):
  {"agent", "table": "account_quotas" | "poll_errors", "raw"}
  {"agent", "table": "session_snapshots", "session_id", "raw"}
  {"agent", "table": "telemetry", "raw"}
  {"agent", "table": "requests" | "scans", "row": {...}}

Prints {"inserted": n, "duplicate": n, "rejected": n} and exits 0 once
every line was handled (a rejected row is counted and reported on stderr,
not retried forever).

Usage: ... | python3 receive_from_machine.py --machine <usage_db.machine_name() of the sender>
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--machine", required=True)
args = parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", args.machine):
    sys.exit(f"not a host name: {args.machine!r}")

# The gates write under AGENT_USAGE_TRACKER_RUNTIME, read when usage_db is
# imported: point it at this machine's central copy first.
base = Path(os.environ.get("AGENT_USAGE_TRACKER_RUNTIME") or Path.home() / "opt" / "agent-usage-tracker")
os.environ["AGENT_USAGE_TRACKER_RUNTIME"] = str(base / "central" / args.machine)
import ingest_account_quota  # noqa: E402
import ingest_session_usage  # noqa: E402
import usage_db  # noqa: E402

d_dbs, d_counts = {}, {"inserted": 0, "duplicate": 0, "rejected": 0}


def database(agent: str, kind: str):
    """One open connection, in one transaction, per (agent, database)."""
    if (agent, kind) not in d_dbs:
        db = usage_db.open_account(agent) if kind == "account" else usage_db.open_sessions(agent)
        db.execute("BEGIN IMMEDIATE")
        d_dbs[(agent, kind)] = db
    return d_dbs[(agent, kind)]


for n, line in enumerate(sys.stdin, 1):
    if not line.strip():
        continue
    try:
        d_line = json.loads(line)
        agent, table = d_line["agent"], d_line["table"]
        if agent not in usage_db.AGENTS:
            raise ValueError(f"unknown agent {agent!r}")
        if table in ("account_quotas", "poll_errors"):
            raw = d_line["raw"]
            result = ingest_account_quota.add_row(database(agent, "account"), agent, json.loads(raw), raw,
                                                  dedup=False, update_state=False)
        elif table == "session_snapshots":
            raw = d_line["raw"]
            result = ingest_session_usage.add_snapshot(database(agent, "sessions"), d_line["session_id"],
                                                       json.loads(raw), raw, dedup=False)
        elif table == "telemetry":
            raw = d_line["raw"]
            result = ingest_session_usage.add_telemetry(database(agent, "sessions"), json.loads(raw), raw)
        elif table == "requests":
            result = "inserted" if ingest_session_usage.add_requests(database(agent, "sessions"), [d_line["row"]]) else "duplicate"
        elif table == "scans":
            d_row = d_line["row"]
            result = ingest_session_usage.add_scan(database(agent, "sessions"), d_row["ts"], d_row["complete_through_ts"],
                                                   d_row["requests_added"])
        else:
            raise ValueError(f"unknown table {table!r}")
        d_counts[result] += 1  # dedup off: "inserted" or "duplicate"
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        d_counts["rejected"] += 1
        print(f"line {n} rejected: {type(exc).__name__}: {exc}", file=sys.stderr)

for db in d_dbs.values():
    db.execute("COMMIT")
    db.close()
print(json.dumps(d_counts))
