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

Claude readings also refresh state/quota/claude - six fields separated by
\\x1c: five_pct five_reset week_pct week_reset source observed_at - when
newer than what it holds. agent-statusline, auto-apply and smart-orchestrator
read that file (README.md's "Contract with agent-statusline").

Command line (migration, and later the VM push): one JSONL row per stdin
line, inserted with the line itself as `raw`:
    ingest_account_quota.py --agent claude [--no-dedup] < rows.jsonl
prints {"inserted": n, "duplicate": n, "redundant": n, "rejected": n}.
"""
import json
import math
import os
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
STATE_FILE = usage_db.RUNTIME_DIR / "state" / "quota" / "claude"
FIELD_SEP = "\x1c"


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
                 seven_day_pct=None, seven_day_resets_ts=None, observed_by_session=None, written_ts=written_ts)
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


def write_state_if_newer(d_col: dict) -> bool:
    """Refreshes state/quota/claude when this reading is newer than the one
    it holds (compared on when the reading was true, so write order does not
    matter). Atomic replace; no lock: a same-instant race can at worst keep a
    slightly older reading until the next write."""
    try:
        existing = int(STATE_FILE.read_text().rstrip("\n").split(FIELD_SEP)[5])
    except (OSError, ValueError, IndexError):
        existing = None
    if existing is not None and d_col["ts"] <= existing:
        return False
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = FIELD_SEP.join([
        str(_round(d_col["five_hour_pct"])), str(d_col["five_hour_resets_ts"] or ""),
        str(_round(d_col["seven_day_pct"])), str(d_col["seven_day_resets_ts"] or ""),
        d_col["source"], str(d_col["ts"]),
    ])
    tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp.{os.getpid()}")
    tmp.write_text(line + "\n")
    tmp.replace(STATE_FILE)
    return True


def add_row(db, agent: str, d_row: dict, raw: str | None = None, dedup: bool = True, update_state: bool = True) -> str:
    """Inserts one row; returns "inserted", "duplicate" or "redundant".
    Raises ValueError for a row that must not be stored."""
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
            "INSERT OR IGNORE INTO poll_errors VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, usage_db.iso(ts), source, d_error.get("stage"), d_error.get("type"), d_error.get("status"),
             d_error.get("retry_after_s"), raw, h))
        return "inserted" if cur.rowcount else "duplicate"
    d_col = columns(agent, d_row)
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
    cur = db.execute(
        "INSERT OR IGNORE INTO account_quotas VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (d_col["ts"], usage_db.iso(d_col["ts"]), d_col["source"], d_col["five_hour_pct"], d_col["five_hour_resets_ts"],
         d_col["seven_day_pct"], d_col["seven_day_resets_ts"], d_col["observed_by_session"], d_col["written_ts"], raw, h))
    if not cur.rowcount:
        return "duplicate"
    if update_state and agent == "claude" and (d_col["five_hour_pct"] is not None or d_col["seven_day_pct"] is not None):
        write_state_if_newer(d_col)
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


def main() -> None:
    import argparse  # here, not at the top: the statusline path never needs it
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--agent", required=True, choices=usage_db.AGENTS)
    parser.add_argument("--no-dedup", action="store_true", help="keep repeated statusline readings (history as is)")
    parser.add_argument("--no-state", action="store_true", help="do not touch state/quota/claude")
    args = parser.parse_args()
    db = usage_db.open_account(args.agent)
    d_counts = {"inserted": 0, "duplicate": 0, "redundant": 0, "rejected": 0}
    db.execute("BEGIN IMMEDIATE")
    for n, line in enumerate(sys.stdin, 1):
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        try:
            d_counts[add_row(db, args.agent, json.loads(raw), raw, not args.no_dedup, not args.no_state)] += 1
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
