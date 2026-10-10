#!/usr/bin/env python3
"""Sends this machine's new Category 3 rows - the `slots` of
data/claude/aggregates.db - to the VM (H-Frank-1), into its own folder there
(design/TARGET_SPEC.md §6.4):
    ~/opt/agent-usage-tracker/central/<machine>/data/claude/aggregates.db
Run by the Mac's `aggregates` job right after it writes the slots
(aggregate_slots.py --send-to); the VM sends nothing (its slots are already
there). Categories 1 and 2 never go this way: quota readings have their own
2-min sync, and a machine's raw rows never leave it (until 2026-10-10, this
sent every row of every database; those copies are in central/_archive/).

Push only: the VM never pulls (the Mac sleeps, goes offline, sits behind
NAT). Each call sends the slots added since the last confirmed send (rowid
above the watermark "claude/slots" in state/push/watermarks.json), in chunks,
as JSON lines on the stdin of
    ssh <central> python3 ~/opt/agent-usage-tracker/src/receive_from_machine.py --machine <this machine>
which hands them to the same gate there (ingest_aggregates.py). A chunk's
watermark advances only after the VM confirmed it; a re-sent slot is a
no-op there. So an offline run, a crash or a dropped connection just means
the slots go on the next run. Reads only; aggregates.db is opened query_only.

Usage: python3 push_to_central.py [--central HOST]   (default: $AGENT_USAGE_TRACKER_CENTRAL or H-Frank-1)
"""
import argparse
import json
import os
import subprocess
import sys
import time

import ingest_aggregates
import usage_db

CENTRAL_DEFAULT = os.environ.get("AGENT_USAGE_TRACKER_CENTRAL") or "H-Frank-1"
WATERMARKS_FILE = usage_db.RUNTIME_DIR / "state" / "push" / "watermarks.json"
WATERMARK_KEY = "claude/slots"
CHUNK_ROWS = 20000
SEND_TIMEOUT_S = 240  # inside the aggregates job's 5 min
# On the VM, python3 is /usr/bin/python3; ssh runs a non-login shell.
REMOTE_COMMAND = "python3 ~/opt/agent-usage-tracker/src/receive_from_machine.py --machine {machine}"


def send(central: str, machine: str, payload: bytes) -> dict:
    """Runs the receiver on the central machine with the payload on stdin;
    returns its counts, or raises."""
    cmd = ["ssh", "-C", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", central, REMOTE_COMMAND.format(machine=machine)]
    result = subprocess.run(cmd, input=payload, capture_output=True, timeout=SEND_TIMEOUT_S)
    if result.returncode != 0:
        raise RuntimeError(f"receiver exited {result.returncode}: {result.stderr.decode(errors='replace')[-500:]}")
    return json.loads(result.stdout.decode().strip().splitlines()[-1])


def send_slots(central: str) -> str:
    """Sends every slot not sent yet; returns what to say. Raises (after
    saving the watermark of the chunks already confirmed) when the VM
    cannot be reached or refused a chunk."""
    machine = usage_db.machine_name()
    try:
        d_marks = json.loads(WATERMARKS_FILE.read_text())
    except (OSError, ValueError):
        d_marks = {}
    db = usage_db.open_aggregates("claude", readonly=True)
    n_sent = 0
    while db is not None:
        l_chunk = db.execute(f"SELECT rowid, {', '.join(ingest_aggregates.SLOT_COLUMNS)} FROM slots WHERE rowid > ? "
                             "ORDER BY rowid LIMIT ?", (d_marks.get(WATERMARK_KEY, 0), CHUNK_ROWS)).fetchall()
        if not l_chunk:
            break
        payload = "".join(json.dumps({"agent": "claude", "table": "slots", "row": dict(zip(ingest_aggregates.SLOT_COLUMNS, r[1:]))},
                                     separators=(",", ":")) + "\n" for r in l_chunk)
        send(central, machine, payload.encode())
        d_marks[WATERMARK_KEY] = l_chunk[-1][0]
        n_sent += len(l_chunk)
        # Saved after every confirmed chunk: a first send of the whole history resumes where it stopped.
        WATERMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = WATERMARKS_FILE.with_name(f"{WATERMARKS_FILE.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(d_marks))
        tmp.replace(WATERMARKS_FILE)
    return f"sent {n_sent} to {central}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--central", default=CENTRAL_DEFAULT)
    args = parser.parse_args()
    started = time.time()
    try:
        said = send_slots(args.central)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"not sent, retried next run: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"{usage_db.iso(int(started))} {said} in {time.time() - started:.1f} s", flush=True)


if __name__ == "__main__":
    main()
