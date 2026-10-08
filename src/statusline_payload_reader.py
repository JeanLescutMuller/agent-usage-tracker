#!/usr/bin/env python3
"""Collector for the Claude statusline payload: the JSON Claude Code hands
every statusline render, piped here unchanged by bin/ingest-claude-statusline.sh
(the path agent-statusline knows). That payload is the only free, per-render
source of Claude's quota percent (USAGE_DATA_SOURCES.md §3.1).

It splits the payload by scope and hands each part to its gate:
  - the quota reading -> ingest_account_quota.py (account_quotas.db, and
    state/quota/claude when newer), tagged observed_by_session;
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

Does nothing at all when the payload has no rate_limits (a session before its
first message). Always exits 0 and prints nothing: a failure here must never
reach the statusline.

Usage: <claude statusline payload JSON> | statusline_payload_reader.py
"""
import json
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


def main() -> None:
    d_payload = json.loads(sys.stdin.read())
    d_limits = d_payload.get("rate_limits") or {}
    if d_limits.get("five_hour") is None and d_limits.get("seven_day") is None:
        return
    when = observed_at(d_payload.get("transcript_path"))
    if when is None:
        return
    now = int(time.time())
    d_common = {"ts": now, "iso": usage_db.iso(now), "source": "claude_statusline", "observed_at": when}
    d_five, d_week = d_limits.get("five_hour") or {}, d_limits.get("seven_day") or {}
    # Percents unrounded, as Claude Code sends them; resets as text, as before.
    d_account = d_common | {
        "five_hour_pct": d_five.get("used_percentage") or 0,
        "seven_day_pct": d_week.get("used_percentage") or 0,
        "five_hour_resets_at": text(d_five.get("resets_at")),
        "seven_day_resets_at": text(d_week.get("resets_at")),
        "observed_by_session": text(d_payload.get("session_id")),
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


if __name__ == "__main__":
    try:
        main()
    except Exception:  # never let anything reach the statusline
        pass
    sys.exit(0)
