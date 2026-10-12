#!/usr/bin/env python3
"""On the VM: receives the Category 3 rows another machine's
push_to_central.py sends - its `slots`, JSON lines on stdin, over ssh - and
hands them to the gate of that machine's own file (doc/design.md §6.4):
    ~/opt/agent-usage-tracker/data/claude/aggregates/<machine>.db
One file per machine (P14), so the copies never conflict, and deleting one
resets only that machine's slots. A slot already stored is a no-op, so a
re-sent chunk changes nothing. Until 2026-10-10 this received every row of
every database; those copies are in data/_archive/.

Line shape (built by push_to_central.py): {"agent": "claude", "table": "slots", "row": {...}}
Prints {"inserted": n, "duplicate": n} and exits 0 once the whole chunk is
stored; a bad line rejects the chunk (exit 1, nothing stored), so the
sender keeps its watermark and the problem shows as a failed job instead of
a slot silently missing.

Usage: ... | python3 receive_from_machine.py --machine <usage_db.machine_name() of the sender>
"""
import argparse
import json
import re
import sys

import ingest_aggregates
import usage_db

parser = argparse.ArgumentParser()
parser.add_argument("--machine", required=True)
args = parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", args.machine):
    sys.exit(f"not a host name: {args.machine!r}")
if args.machine == usage_db.machine_name():
    sys.exit(f"{args.machine} is this machine: its slots are written here by its own aggregates job")

l_slots = []
for n, line in enumerate(sys.stdin, 1):
    if not line.strip():
        continue
    try:
        d_line = json.loads(line)
        if d_line.get("agent") != "claude" or d_line.get("table") != "slots":
            raise ValueError(f"only claude slots are received, not {d_line.get('agent')!r} {d_line.get('table')!r}")
        if d_line["row"].get("machine") != args.machine:
            raise ValueError(f"a slot of {d_line['row'].get('machine')!r} sent as {args.machine!r}")
        l_slots.append(d_line["row"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        sys.exit(f"line {n} rejected, chunk not stored: {type(exc).__name__}: {exc}")
try:
    n_new = ingest_aggregates.add_slots(usage_db.open_aggregates("claude", machine=args.machine), l_slots) if l_slots else 0
except ValueError as exc:
    sys.exit(f"chunk not stored: {exc}")
print(json.dumps({"inserted": n_new, "duplicate": len(l_slots) - n_new}))
