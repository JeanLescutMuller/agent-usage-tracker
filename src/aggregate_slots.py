#!/usr/bin/env python3
"""Category 3 (doc/design.md §6.2): the final 5-min slots, into
data/claude/aggregates/<this machine>.db. The `aggregates` job's command,
every 5 min:
  1. transcript_reader.py: new transcript lines -> data/claude/intermediary.db
     (each request once) + a scan "complete through now - 15 min";
  2. every slot that is final and not written yet: the transcript requests
     (LEFT JOIN telemetry on request_id: Claude Code's own cost_usd), plus
     the requests only telemetry knows (prompt suggestions, summaries, web
     search, titles, compaction), placed by their event time, with the
     folder and entrypoint of the same session's transcript requests and
     their cache writes as cache_write_unknown_tokens (telemetry gives no
     5m / 1h split); summed per (slot, machine, folder, entrypoint, model);
  3. handed to ingest_aggregates.py, the only writer of the aggregates files;
  4. with --send-to=HOST (the Mac): every slot not sent yet goes to the
     file of the same name on HOST (push_to_central.py). A failed send is retried
     by the next run (exit 1 meanwhile: the job shows it).

A request belongs to the slot of its first transcript line. A slot is
final once it ended at or before the latest scan's complete_through_ts:
every request that started in it is then in intermediary.db, and telemetry
arrives within seconds (none later than 556 s in 8,090 events, measured
2026-10-10). A slot is written once and never changes; the next run starts
after the last slot written on this machine, so a slot with no request is
simply looked at again (and stays empty).

No $ computed here: `usd` is Claude Code's own figure where telemetry has
one; `tokens_without_usd` is everything it does not cover.

Usage: python3 aggregate_slots.py [--send-to HOST]   (prints the reader's line, then its own)
"""
import argparse
import subprocess
import sys
import time

import ingest_aggregates
import push_to_central
import transcript_reader
import usage_db

SLOT_S = 300

# Every request of the slots in [:start, :end), one row each: transcript
# requests, then telemetry-only requests. `s` is the attached sessions_usages.db.
REQUESTS_SQL = """
SELECT (r.ts / 300) * 300, COALESCE(r.folder, ''), COALESCE(r.entrypoint, ''), COALESCE(r.model, ''),
       r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_5m_tokens, r.cache_write_1h_tokens, 0,
       t.cost_usd
FROM requests r LEFT JOIN s.telemetry_requests t ON t.request_id = r.request_id
WHERE r.ts >= :start AND r.ts < :end
UNION ALL
SELECT (t.ts / 300) * 300,
       COALESCE((SELECT folder FROM requests WHERE session_id = t.session_id AND folder IS NOT NULL LIMIT 1), ''),
       COALESCE((SELECT entrypoint FROM requests WHERE session_id = t.session_id AND entrypoint IS NOT NULL LIMIT 1), ''),
       COALESCE(t.model, ''), COALESCE(t.input_tokens, 0), COALESCE(t.output_tokens, 0), COALESCE(t.cache_read_tokens, 0),
       0, 0, COALESCE(t.cache_creation_tokens, 0), t.cost_usd
FROM s.telemetry_requests t
WHERE t.ts >= :start AND t.ts < :end AND NOT EXISTS (SELECT 1 FROM requests r WHERE r.request_id = t.request_id)
"""
# Without a sessions database (no telemetry yet on this machine): transcript requests only.
REQUESTS_ONLY_SQL = """
SELECT (ts / 300) * 300, COALESCE(folder, ''), COALESCE(entrypoint, ''), COALESCE(model, ''),
       input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, 0, NULL
FROM requests WHERE ts >= :start AND ts < :end
"""


def final_slots(now: int) -> tuple[list[dict], int, int]:
    """The final slots not written yet: ([slot row], first slot start, end of
    the last final slot)."""
    me = usage_db.machine_name()
    inter = usage_db.open_intermediary("claude", readonly=True)
    agg = usage_db.open_aggregates("claude")
    start = agg.execute("SELECT COALESCE(MAX(ts) + 300, 0) FROM slots WHERE machine = ?", (me,)).fetchone()[0]
    complete = inter.execute("SELECT MAX(complete_through_ts) FROM scans").fetchone()[0] if inter else None
    if not complete:
        return [], start, start
    end = complete // SLOT_S * SLOT_S  # slots starting before it ended by complete_through_ts
    if end <= start:
        return [], start, end
    sessions = usage_db.db_path("claude", "sessions_usages")
    if sessions.exists():
        inter.execute("ATTACH DATABASE ? AS s", (str(sessions),))
    d_slots = {}
    for slot, folder, entrypoint, model, i, o, cr, w5, w1, wu, usd in inter.execute(
            REQUESTS_SQL if sessions.exists() else REQUESTS_ONLY_SQL, {"start": start, "end": end}):
        d = d_slots.setdefault((slot, folder, entrypoint, model), dict(
            ts=slot, dt=usage_db.iso(slot), machine=me, folder=folder, entrypoint=entrypoint, model=model,
            requests=0, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_5m_tokens=0,
            cache_write_1h_tokens=0, cache_write_unknown_tokens=0, usd=None, tokens_without_usd=0, written_ts=now))
        d["requests"] += 1
        for key, value in (("input_tokens", i), ("output_tokens", o), ("cache_read_tokens", cr),
                           ("cache_write_5m_tokens", w5), ("cache_write_1h_tokens", w1), ("cache_write_unknown_tokens", wu)):
            d[key] += value
        if usd is None:
            d["tokens_without_usd"] += i + o + cr + w5 + w1 + wu
        else:
            d["usd"] = (d["usd"] or 0.0) + usd
    return sorted(d_slots.values(), key=lambda d: (d["ts"], d["folder"], d["entrypoint"], d["model"])), start, end


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-to", metavar="HOST", help="then send the slots not sent yet to HOST (the Mac: H-Frank-1)")
    args = parser.parse_args(argv)
    transcript_reader.main()
    now = int(time.time())
    l_slots, start, end = final_slots(now)
    n = ingest_aggregates.add_slots(usage_db.open_aggregates("claude"), l_slots) if l_slots else 0
    said = f"slots: {n} written ({usage_db.iso(start)} → {usage_db.iso(end)}), {sum(d['requests'] for d in l_slots)} requests"
    if args.send_to:
        try:
            said += f" · {push_to_central.send_slots(args.send_to)}"
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            print(f"not sent, retried next run: {exc}", file=sys.stderr, flush=True)
            print(f"{said} · {args.send_to} not reached", flush=True)
            sys.exit(1)
    print(said, flush=True)


if __name__ == "__main__":
    main()
