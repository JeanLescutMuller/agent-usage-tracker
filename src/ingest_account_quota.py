#!/usr/bin/env python3
"""The gate of data/<agent>/account_quotas.db: the only code that writes it.

Collectors (statusline_payload_reader.py, the *_poller.py scripts) hand it a
row in the JSONL shape the old files used - {ts, iso, source, ...}. It checks
the row, extracts the typed columns (ts, percents, resets) and inserts it with
the JSON kept verbatim in `raw`. A row is not written when:
  - it is malformed, or carries per-session fields (scope rule,
    USAGE_DATA_REFERENCE.md §1): rejected with ValueError;
  - the same row is already stored (`row_hash`): a duplicate;
  - (statusline rows, dedup on) it repeats the same session's latest reading:
    same observed_at, percents and resets. A re-render adds nothing; a new
    API response always brings a new observed_at, so every real observation
    is kept, even when the percents did not move. Per session, not per
    account: two sessions seeing the same reading are two observations.
A failed poll attempt (`error` set) goes to the poll_errors table instead.

The account's freshest reading is the `latest` view (the reset rule
applied), read by statusline_payload_reader.py for agent-statusline, by
auto-apply and by the claude-quota poller; until 2026-10-10 this gate also
kept it in a file, state/quota/claude.

Command line (migration, and later the VM push): one JSONL row per stdin
line, inserted with the line itself as `raw`:
    ingest_account_quota.py --agent claude [--no-dedup] < rows.jsonl
prints {"inserted": n, "duplicate": n, "redundant": n, "rejected": n}.
    ingest_account_quota.py --agent claude --sync-from=MACHINE --after=ROWID
is the other side of the 2-min sync (design/TARGET_SPEC.md §3.3), run over
ssh by MACHINE's claude_quota_api_poller.py --sync-with: stdin holds
MACHINE's own new readings, one {"raw", "machine"} line each, stored here
(no dedup: the sender already deduplicated); then it prints this machine's
own readings with a rowid above ROWID, one {"rowid", "raw", "machine"} line
each (at most SYNC_LIMIT), and last {"inserted", "duplicate", "rejected",
"max_rowid"}: the rowid to ask from next time.
"""
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import usage_db

SOURCES = {
    "claude": {"claude_statusline", "claude_api"},
    "codex": {"codex_app_server", "codex_plan_limit_history"},
}
# Per-session fields never belong to an account row.
SESSION_KEYS = {"session_cost_usd", "prompt_cache", "session_id"}
FIELD_SEP = "\x1c"   # between the six fields statusline_payload_reader.py prints
MACHINE_RE = re.compile(r"[A-Za-z0-9-]+")
# Rows per direction per sync call: a backlog drains over successive runs.
SYNC_LIMIT = 20_000


def _epoch(value) -> int | None:
    """An epoch (int or numeric string) or an ISO 8601 time -> epoch seconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    if text.lstrip("-").isdigit():
        return int(text)
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"percent is not a number: {value!r}")
    return float(value)


def columns(agent: str, d_row: dict) -> dict:
    """The typed columns of one reading, by source. Rows from before
    2026-08-30 have no `source`: they are Claude poller rows."""
    source = d_row.get("source") or ("claude_api" if agent == "claude" else None)
    if source not in SOURCES[agent]:
        raise ValueError(f"unknown {agent} source {source!r}")
    written_ts = int(d_row["ts"])
    d_col = dict(ts=written_ts, source=source, five_hour_pct=None, five_hour_resets_ts=None,
                 seven_day_pct=None, seven_day_resets_ts=None, observed_by_session=None, written_ts=written_ts,
                 machine=d_row.get("machine"))
    if source == "claude_statusline":
        # Dated by the transcript's last assistant entry, not by the write.
        d_col.update(ts=int(d_row.get("observed_at") or written_ts),
                     five_hour_pct=_number(d_row.get("five_hour_pct")),
                     five_hour_resets_ts=_epoch(d_row.get("five_hour_resets_at")),
                     seven_day_pct=_number(d_row.get("seven_day_pct")),
                     seven_day_resets_ts=_epoch(d_row.get("seven_day_resets_at")),
                     observed_by_session=d_row.get("observed_by_session"))
    elif source == "claude_api":
        d_api = d_row.get("api") or {}
        for key, name in (("five_hour", "five_hour"), ("seven_day", "seven_day")):
            d_window = d_api.get(key)
            if isinstance(d_window, dict):
                d_col[f"{name}_pct"] = _number(d_window.get("utilization"))
                d_col[f"{name}_resets_ts"] = _epoch(d_window.get("resets_at"))
    elif source == "codex_app_server":
        d_limits = ((d_row.get("codex_rate_limits") or {}).get("rateLimits")) or {}
        for key in ("primary", "secondary"):
            d_window = d_limits.get(key)
            if not isinstance(d_window, dict):
                continue
            name = {300: "five_hour", 10080: "seven_day"}.get(d_window.get("windowDurationMins"))
            if name:
                d_col[f"{name}_pct"] = _number(d_window.get("usedPercent"))
                d_col[f"{name}_resets_ts"] = _epoch(d_window.get("resetsAt"))
    # codex_plan_limit_history: finished windows only, no current reading.
    return d_col


def _round(pct: float | None) -> int:
    # Half away from zero, like jq's round (the old bash writer).
    return int(math.floor((pct or 0) + 0.5))


def add_row(db, agent: str, d_row: dict, raw: str | None = None, dedup: bool = True,
            machine: str | None = None) -> str:
    """Inserts one row; returns "inserted", "duplicate" or "redundant".
    Raises ValueError for a row that must not be stored. `machine`: the
    machine that took the reading, when its raw does not say (rows from
    before 2026-10-10, sent by the sync); otherwise raw's "machine"."""
    if not isinstance(d_row, dict) or not isinstance(d_row.get("ts"), (int, float)):
        raise ValueError("a row is a JSON object with a numeric ts")
    if SESSION_KEYS & set(d_row):
        raise ValueError(f"per-session field in an account row: {sorted(SESSION_KEYS & set(d_row))}")
    raw = raw if raw is not None else json.dumps(d_row)
    h = usage_db.row_hash(raw)
    d_error = d_row.get("error")
    if d_error is not None:
        source = d_row.get("source") or ("claude_api" if agent == "claude" else None)
        if source not in SOURCES[agent]:
            raise ValueError(f"unknown {agent} source {source!r}")
        ts = int(d_row["ts"])
        cur = db.execute(
            "INSERT OR IGNORE INTO poll_errors (ts, dt, source, stage, type, status, retry_after_s, raw, row_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, usage_db.iso(ts), source, d_error.get("stage"), d_error.get("type"), d_error.get("status"),
             d_error.get("retry_after_s"), raw, h))
        return "inserted" if cur.rowcount else "duplicate"
    d_col = columns(agent, d_row)
    d_col["machine"] = machine or d_col["machine"]
    if d_col["machine"] is not None and not MACHINE_RE.fullmatch(str(d_col["machine"])):
        raise ValueError(f"not a machine name: {d_col['machine']!r}")
    l_reading = [d_col[k] for k in ("ts", "five_hour_pct", "five_hour_resets_ts", "seven_day_pct", "seven_day_resets_ts")]
    # No transaction of its own: the caller's, if any, or autocommit. Two
    # renders of the same session racing between the SELECT and the INSERT
    # can at worst store one repeated reading.
    if dedup and d_col["source"] == "claude_statusline" and d_col["observed_by_session"]:
        last = db.execute(
            "SELECT ts, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts FROM account_quotas "
            "WHERE observed_by_session = ? AND source = 'claude_statusline' ORDER BY rowid DESC LIMIT 1",
            (d_col["observed_by_session"],)).fetchone()
        if last is not None and list(last) == l_reading:
            return "redundant"
    # Columns named, not positional, and `machine` only when known: a Codex
    # database (no machine column until it is migrated) keeps working.
    d_values = {"ts": d_col["ts"], "dt": usage_db.iso(d_col["ts"]), **{k: d_col[k] for k in (
        "source", "five_hour_pct", "five_hour_resets_ts", "seven_day_pct", "seven_day_resets_ts",
        "observed_by_session", "written_ts")}, "raw": raw, "row_hash": h}
    if d_col["machine"] is not None:
        d_values["machine"] = d_col["machine"]
    cur = db.execute(f"INSERT OR IGNORE INTO account_quotas ({', '.join(d_values)}) VALUES ({','.join('?' * len(d_values))})",
                     list(d_values.values()))
    if not cur.rowcount:
        return "duplicate"
    return "inserted"


def last_attempt(db, sources: tuple[str, ...] | None = None) -> dict | None:
    """The newest poll attempt or reading, by write time, across readings and
    failed attempts: {"ts": ..., "error": {...} or None}. `sources` limits it
    to those sources; None takes any row, push rows included. Used by the
    pollers' cadence and backoff checks."""
    if db is None:
        return None
    where, l_args = "", []
    if sources:
        where = f"WHERE source IN ({','.join('?' * len(sources))})"
        l_args = list(sources)
    reading = db.execute(f"SELECT MAX(written_ts) FROM account_quotas {where}", l_args).fetchone()[0]
    error = db.execute(f"SELECT ts, raw FROM poll_errors {where} ORDER BY ts DESC LIMIT 1", l_args).fetchone()
    if error is not None and (reading is None or error[0] >= reading):
        return {"ts": error[0], "error": json.loads(error[1]).get("error")}
    return None if reading is None else {"ts": reading, "error": None}


def own_rows_after(db, machine: str, after: int) -> tuple[list, int]:
    """This machine's own readings with a rowid above `after`: [(rowid, raw)]
    (at most SYNC_LIMIT), and the rowid to ask from next time. One read
    transaction, so the rows and that rowid come from the same snapshot:
    every reading of `machine` up to it is in the list; a row written
    meanwhile gets a larger rowid and is sent next time."""
    db.execute("BEGIN")
    try:
        l_rows = db.execute("SELECT rowid, raw FROM account_quotas WHERE rowid > ? AND machine = ? ORDER BY rowid LIMIT ?",
                            (after, machine, SYNC_LIMIT)).fetchall()
        last = l_rows[-1][0] if len(l_rows) == SYNC_LIMIT else \
            max(after, db.execute("SELECT COALESCE(MAX(rowid), 0) FROM account_quotas").fetchone()[0])
    finally:
        db.execute("COMMIT")
    return l_rows, last


def add_synced(db, agent: str, l_lines: list[str]) -> dict:
    """Stores the {"raw", "machine"} lines another machine sent (sync), in
    one transaction; returns the counts. A row already here is a duplicate."""
    d_counts = {"inserted": 0, "duplicate": 0, "rejected": 0}
    db.execute("BEGIN IMMEDIATE")
    try:
        for line in l_lines:
            try:
                d_line = json.loads(line)
                result = add_row(db, agent, json.loads(d_line["raw"]), d_line["raw"], dedup=False, machine=d_line.get("machine"))
                d_counts["inserted" if result == "inserted" else "duplicate"] += 1
            except (ValueError, KeyError, TypeError) as exc:
                d_counts["rejected"] += 1
                print(f"synced row rejected: {type(exc).__name__}: {exc}", file=sys.stderr)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return d_counts


def main() -> None:
    import argparse  # here, not at the top: the statusline path never needs it
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--agent", required=True, choices=usage_db.AGENTS)
    parser.add_argument("--no-dedup", action="store_true", help="keep repeated statusline readings (history as is)")
    parser.add_argument("--sync-from", metavar="MACHINE", help="the other side of MACHINE's sync (see the docstring)")
    parser.add_argument("--after", type=int, default=0, metavar="ROWID", help="with --sync-from: send own rows above it")
    args = parser.parse_args()
    if args.sync_from:
        db = usage_db.open_account(args.agent)
        me = usage_db.machine_name()
        d_counts = add_synced(db, args.agent, [line for line in sys.stdin if line.strip()])
        l_rows, last = own_rows_after(db, me, args.after)
        for rowid, raw in l_rows:
            print(json.dumps({"rowid": rowid, "raw": raw, "machine": me}, separators=(",", ":")))
        print(json.dumps(d_counts | {"max_rowid": last}))
        return
    db = usage_db.open_account(args.agent)
    d_counts = {"inserted": 0, "duplicate": 0, "redundant": 0, "rejected": 0}
    db.execute("BEGIN IMMEDIATE")
    for n, line in enumerate(sys.stdin, 1):
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        try:
            d_counts[add_row(db, args.agent, json.loads(raw), raw, not args.no_dedup)] += 1
        except (ValueError, KeyError, TypeError) as exc:
            d_counts["rejected"] += 1
            print(f"line {n} rejected: {type(exc).__name__}: {exc}", file=sys.stderr)
        if n % 20000 == 0:  # bounded transactions: other writers wait at most a moment
            db.execute("COMMIT")
            db.execute("BEGIN IMMEDIATE")
    db.execute("COMMIT")
    print(json.dumps(d_counts))


if __name__ == "__main__":
    main()
