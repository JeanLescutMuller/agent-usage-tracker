#!/usr/bin/env python3
"""Copies this machine's new database rows to the central store on the VM
(H-Frank-1), every 5 minutes, so every machine's usage ends up in one place:
    ~/opt/agent-usage-tracker/central/<machine>/data/<agent>/{account_quotas,sessions_usages}.db

Push only: the VM never pulls (the Mac sleeps, goes offline, sits behind
NAT). Each run sends, per table, the rows added since the last confirmed
push (rowid above a watermark kept in state/push/watermarks.json), in
chunks, as JSON lines on the stdin of
    ssh <central> python3 ~/opt/agent-usage-tracker/src/receive_from_machine.py --machine <this machine>
which hands every row to the same two gates (ingest_*.py) there. A chunk's
watermark advances only after the VM confirmed it; a re-sent row is a
no-op on the VM (the gates' row_hash / request_id). So an offline run, a
crash or a dropped connection just means the rows go on the next run.

On the central machine itself (its name, usage_db.machine_name(), is the central's), the
receiver runs locally, without ssh.

Every table is insert-only, so "rows above the last rowid" is exactly the
new rows. Reads only; this machine's databases are opened query_only.

Usage: python3 push_to_central.py [--central HOST]   (default: $AGENT_USAGE_TRACKER_CENTRAL or H-Frank-1)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import usage_db

CENTRAL_DEFAULT = os.environ.get("AGENT_USAGE_TRACKER_CENTRAL") or "H-Frank-1"
WATERMARKS_FILE = usage_db.RUNTIME_DIR / "state" / "push" / "watermarks.json"
CHUNK_ROWS = 20000
# On the VM, python3 is /usr/bin/python3; ssh runs a non-login shell.
REMOTE_COMMAND = "python3 ~/opt/agent-usage-tracker/src/receive_from_machine.py --machine {machine}"
LOCAL_RECEIVER = Path(__file__).resolve().parent / "receive_from_machine.py"

# (database, table, columns sent). Rows with `raw` are re-derived by the
# gate on the other side; requests and scans have no raw, so their columns go.
TABLES = (
    ("account_quotas", "account_quotas", "raw"),
    ("account_quotas", "poll_errors", "raw"),
    ("sessions_usages", "session_snapshots", "session_id, raw"),
    ("sessions_usages", "telemetry", "raw"),
    ("sessions_usages", "requests", "ts, dt, request_id, machine, session_id, folder, entrypoint, model, input_tokens, "
                                    "output_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, usd"),
    ("sessions_usages", "scans", "ts, complete_through_ts, requests_added"),
)


def chunk_lines(db, agent: str, table: str, columns: str, after: int):
    """[(rowid, json line)] of up to CHUNK_ROWS rows above `after`."""
    l_names = [c.strip() for c in columns.split(",")]
    l_out = []
    for row in db.execute(f"SELECT rowid, {columns} FROM {table} WHERE rowid > ? ORDER BY rowid LIMIT ?", (after, CHUNK_ROWS)):
        d_line = {"agent": agent, "table": table}
        if l_names == ["raw"]:
            d_line["raw"] = row[1]
        elif l_names == ["session_id", "raw"]:
            d_line.update(session_id=row[1], raw=row[2])
        else:
            d_line["row"] = dict(zip(l_names, row[1:]))
        l_out.append((row[0], json.dumps(d_line, separators=(",", ":"))))
    return l_out


def send(central: str, machine: str, payload: bytes) -> dict:
    """Runs the receiver on the central machine (or locally on it) with the
    payload on stdin; returns its counts, or raises."""
    if central == machine:
        cmd = [sys.executable, str(LOCAL_RECEIVER), "--machine", machine]
    else:
        cmd = ["ssh", "-C", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", central, REMOTE_COMMAND.format(machine=machine)]
    result = subprocess.run(cmd, input=payload, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"receiver exited {result.returncode}: {result.stderr.decode(errors='replace')[-500:]}")
    return json.loads(result.stdout.decode().strip().splitlines()[-1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--central", default=CENTRAL_DEFAULT)
    args = parser.parse_args()
    machine = usage_db.machine_name()
    central = args.central.split(".")[0] if args.central.split(".")[0] == machine else args.central
    try:
        d_marks = json.loads(WATERMARKS_FILE.read_text())
    except (OSError, ValueError):
        d_marks = {}
    started, n_sent, l_problems = time.time(), 0, []
    for agent in usage_db.AGENTS:
        for db_name, table, columns in TABLES:
            db = (usage_db.open_account if db_name == "account_quotas" else usage_db.open_sessions)(agent, readonly=True)
            if db is None:
                continue
            key = f"{agent}/{table}"
            while True:
                l_chunk = chunk_lines(db, agent, table, columns, d_marks.get(key, 0))
                if not l_chunk:
                    break
                try:
                    d_counts = send(central, machine, ("\n".join(line for _, line in l_chunk) + "\n").encode())
                except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                    print(f"{key}: not pushed, retried next run: {exc}", file=sys.stderr)
                    db.close()
                    WATERMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
                    WATERMARKS_FILE.write_text(json.dumps(d_marks))
                    sys.exit(1)
                if d_counts.get("rejected"):
                    l_problems.append(f"{key}: {d_counts['rejected']} rows rejected by the central gate")
                d_marks[key] = l_chunk[-1][0]
                n_sent += len(l_chunk)
                # Saved after every confirmed chunk: a first push of the
                # whole history resumes where it stopped.
                WATERMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = WATERMARKS_FILE.with_name(f"{WATERMARKS_FILE.name}.tmp.{os.getpid()}")
                tmp.write_text(json.dumps(d_marks))
                tmp.replace(WATERMARKS_FILE)
            db.close()
    for problem in l_problems:
        print(problem, file=sys.stderr)
    print(f"{usage_db.iso(int(started))} pushed {n_sent} rows from {machine} to {central} in {time.time() - started:.1f} s", flush=True)


if __name__ == "__main__":
    main()
