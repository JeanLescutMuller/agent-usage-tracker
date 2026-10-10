#!/usr/bin/env python3
"""The gate of data/<agent>/aggregates.db (Category 3, design/TARGET_SPEC.md
§6.3): the only code that writes it, on this machine and, on the VM, into
another machine's copy (central/<machine>/data/<agent>/aggregates.db,
through receive_from_machine.py).

It checks each `slots` row - every column there, of the right type, ts a
multiple of 300, counts not negative, a valid machine name, no extra key
(so never a quota percent: USAGE_DATA_REFERENCE.md §1) - and inserts it
with INSERT OR IGNORE: a slot is written once, when final, and never
changes; the same slot sent twice is a no-op.
"""
import re

import usage_db

SLOT_COLUMNS = ("ts", "dt", "machine", "folder", "entrypoint", "model", "requests", "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "cache_write_unknown_tokens",
                "usd", "tokens_without_usd", "written_ts")
COUNT_COLUMNS = ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_5m_tokens",
                 "cache_write_1h_tokens", "cache_write_unknown_tokens", "tokens_without_usd")
MACHINE_RE = re.compile(r"[A-Za-z0-9-]+")


def check(d_slot: dict) -> list:
    """The row's values in SLOT_COLUMNS order; ValueError when it must not be stored."""
    if not isinstance(d_slot, dict) or set(d_slot) != set(SLOT_COLUMNS):
        raise ValueError(f"a slot has exactly the columns {SLOT_COLUMNS}")
    for key in ("ts", "written_ts") + COUNT_COLUMNS:
        if isinstance(d_slot[key], bool) or not isinstance(d_slot[key], int) or d_slot[key] < 0:
            raise ValueError(f"{key} is not a count: {d_slot[key]!r}")
    if d_slot["ts"] % 300:
        raise ValueError(f"ts is not a 5-min slot start: {d_slot['ts']}")
    if d_slot["dt"] != usage_db.iso(d_slot["ts"]):
        raise ValueError(f"dt {d_slot['dt']!r} is not ts {d_slot['ts']}")
    if not isinstance(d_slot["machine"], str) or not MACHINE_RE.fullmatch(d_slot["machine"]):
        raise ValueError(f"not a machine name: {d_slot['machine']!r}")
    for key in ("folder", "entrypoint", "model"):
        if not isinstance(d_slot[key], str):
            raise ValueError(f"{key} is not text: {d_slot[key]!r}")
    if d_slot["usd"] is not None and (isinstance(d_slot["usd"], bool) or not isinstance(d_slot["usd"], (int, float))):
        raise ValueError(f"usd is not a number: {d_slot['usd']!r}")
    return [d_slot[k] for k in SLOT_COLUMNS]


def add_slots(db, l_slots: list[dict]) -> int:
    """Inserts the slots in one transaction; returns how many were new.
    One bad row rejects the whole batch (ValueError): a slot is never
    written twice, so a partial batch could not be completed later."""
    l_values = [check(d) for d in l_slots]
    db.execute("BEGIN IMMEDIATE")
    try:
        n = sum(db.execute(f"INSERT OR IGNORE INTO slots ({', '.join(SLOT_COLUMNS)}) "
                           f"VALUES ({','.join('?' * len(SLOT_COLUMNS))})", values).rowcount for values in l_values)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return n
