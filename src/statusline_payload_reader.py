#!/usr/bin/env python3
"""Collector for the Claude statusline payload: the JSON Claude Code hands
every statusline render, piped here unchanged by agent-statusline, which
runs this file directly (its path is the contract: README.md's "Contract
with agent-statusline"; install.sh writes its first line, this machine's
python with -S, and MACHINE_NAME below). That payload is the only free,
per-render source of Claude's quota percent (USAGE_DATA_SOURCES.md §3.1).

It splits the payload by scope and hands each part to its gate:
  - the quota reading -> ingest_account_quota.py (account_quotas.db),
    tagged observed_by_session and machine;
  - the session's cumulative cost, model and prompt_cache statistics ->
    ingest_session_usage.py (sessions_usages.db), only for a plain session id.
Never a percent in the session part, never a cost in the account part.

The payload carries no timestamp, and an idle session re-renders the same
frozen rate_limits for days. So a reading is dated by the transcript's last
`assistant` entry (the last API response, the only thing that updates
rate_limits), and without one nothing is written: no date beats a wrong one.
Not any entry: attachments, user messages and bookkeeping lines come later
without changing rate_limits. The date can be slightly too old (Claude Code
can refresh rate_limits from a quota_check request it does not log), never
too new, the safe direction for newest-wins. Only the transcript's last
256 KB is read: bookkeeping tails can run past any fixed line count.

Stores nothing when the payload has no rate_limits (a session before its
first message). Then, whatever it stored, prints the account's freshest
reading - the `latest` view: any source, any machine, the reset rule
applied - as one line of six fields separated by \x1c, the format agent-
statusline and auto-apply read since 2026-08:
    five_pct five_reset week_pct week_reset source observed_at
(percents rounded half up; a reset that has passed is empty), or nothing
when there is no reading. Always exits 0 and prints nothing else: a
failure here must never reach the statusline, which then shows its own
session's reading.

Usage: <claude statusline payload JSON> | statusline_payload_reader.py
"""
import json
import os
import sys
import time
from datetime import datetime

import ingest_account_quota
import ingest_session_usage
import usage_db

TAIL_BYTES = 262144


def observed_at(transcript_path) -> int | None:
    """Epoch of the transcript's last top-level `assistant` entry within its
    last TAIL_BYTES, or None. Only UTC timestamps ("Z" or "+00:00") are
    accepted. The window's cut first line simply fails to parse."""
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            data = f.read()
    except OSError:
        return None
    for line in reversed(data.splitlines()):
        if b'"type":"assistant"' not in line:
            continue
        try:
            d_entry = json.loads(line)
        except ValueError:
            continue
        stamp = d_entry.get("timestamp") if isinstance(d_entry, dict) and d_entry.get("type") == "assistant" else None
        if not isinstance(stamp, str):
            continue
        if not (stamp.endswith("Z") or stamp.endswith("+00:00")):
            return None
        try:
            return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def text(value):
    """jq's `text` of the old writer: null or "" -> None, anything else -> str."""
    return None if value is None or value == "" else str(value)


# This machine's name, written here by install.sh (rule "Machine name" in ~/AGENTS.md):
# usage_db.machine_name() honours JR_MACHINE_NAME first, so a render needs no scutil call (~13 ms).
MACHINE_NAME = "__MACHINE_NAME__"


def latest_line() -> str | None:
    """The six fields of the account's freshest reading, or None."""
    db = usage_db.open_account("claude", readonly=True)
    row = db and db.execute("SELECT five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts, source, ts "
                            "FROM latest").fetchone()
    if not row:
        return None
    five, five_reset, week, week_reset, source, ts = row
    return ingest_account_quota.FIELD_SEP.join([
        str(ingest_account_quota._round(five)), str(five_reset or ""),
        str(ingest_account_quota._round(week)), str(week_reset or ""), source, str(ts)])


def store(d_payload: dict) -> None:
    """The payload's quota reading and session part, each to its gate."""
    d_limits = d_payload.get("rate_limits") or {}
    if d_limits.get("five_hour") is None and d_limits.get("seven_day") is None:
        return
    when = observed_at(d_payload.get("transcript_path"))
    if when is None:
        return
    now = int(time.time())
    d_common = {"ts": now, "iso": usage_db.iso(now), "source": "claude_statusline", "observed_at": when}
    # The machine that took the reading (since 2026-10-10). The entry point
    # passes JR_MACHINE_NAME, written by install.sh, so no scutil call costs
    # ~13 ms on every render; without a name, the reading is still kept.
    try:
        machine = usage_db.machine_name()
    except (OSError, RuntimeError):
        machine = None
    d_five, d_week = d_limits.get("five_hour") or {}, d_limits.get("seven_day") or {}
    # Percents unrounded, as Claude Code sends them; resets as text, as before.
    d_account = d_common | {
        "five_hour_pct": d_five.get("used_percentage") or 0,
        "seven_day_pct": d_week.get("used_percentage") or 0,
        "five_hour_resets_at": text(d_five.get("resets_at")),
        "seven_day_resets_at": text(d_week.get("resets_at")),
        "observed_by_session": text(d_payload.get("session_id")),
        "machine": machine,
    }
    db = usage_db.open_account("claude")
    ingest_account_quota.add_row(db, "claude", d_account, json.dumps(d_account, separators=(",", ":")))
    session_id = d_payload.get("session_id")
    if ingest_session_usage.valid_session_id(session_id):
        cost = (d_payload.get("cost") or {}).get("total_cost_usd")
        try:
            cost = None if cost is None else float(cost)
        except (TypeError, ValueError):
            cost = None
        d_session = d_common | {
            "model_id": text((d_payload.get("model") or {}).get("id")),
            "session_cost_usd": cost,
            "prompt_cache": d_payload.get("prompt_cache"),
        }
        ingest_session_usage.add_snapshot(usage_db.open_sessions("claude"), session_id, d_session)


def main() -> None:
    if not MACHINE_NAME.startswith("__"):  # left as is in the repo; filled in when deployed
        os.environ.setdefault("JR_MACHINE_NAME", MACHINE_NAME)
    try:
        store(json.loads(sys.stdin.read()))
    except Exception:  # a bad payload or a store failure must not cost the display
        pass
    try:
        line = latest_line()
    except Exception:
        line = None
    if line:
        print(line, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # never let anything reach the statusline
        pass
    sys.exit(0)
