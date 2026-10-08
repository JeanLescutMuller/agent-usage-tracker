#!/usr/bin/env python3
"""Collector: reads Claude Code's transcripts (~/.claude/projects/**/*.jsonl,
subagents included) and Codex's session files (~/.codex/sessions and
archived_sessions), and hands one row per API request (Claude) or per turn
(Codex) to ingest_session_usage.py (data/<agent>/sessions_usages.db, table
`requests`): when it happened, its session, execution folder (cwd),
entrypoint (cli = interactive), model, the token counts and its USD.

Run every 5 minutes by its LaunchAgent. Incremental: a byte offset per file
(state/transcript_reader/offsets.json) means a run with nothing new reads
nothing; a replaced or truncated file is read again from the start.

Insert-only, so a request is stored once, when complete. A request is
written as several transcript lines sharing one requestId, with
output_tokens growing from line to line, over up to 292 s (measured
2026-10-08). So:
  - the line with the largest output_tokens is the request's usage (keeping
    the first undercounts output about 2x);
  - a request is stored only once its first line is FINAL_AFTER_SECONDS
    (15 min) old; a younger one stays unread - the file's offset stops at
    its first line - and is picked up by a later run;
  - a request_id already stored is skipped (a resumed session copies earlier
    requests into its new transcript).
Each run then records a scan whose complete_through_ts is now - 15 min:
every request that started before it is in the database.

The requests only telemetry sees (prompt suggestions, compaction, web
search, titles: about 4.6% of USD) are added by the `usage_requests` view
from the telemetry table, not here. USD here is from usage_db.PRICES, which
matches Claude Code's own figure exactly (USAGE_DATA_SOURCES.md §3.4).

Codex (read_codex_file): one session file per thread. Each `token_count`
event carries that turn's usage (`last_token_usage`) and the thread's running
total; the row's request_id is "<thread id>:<running total>", so a repeated
event (same total) is skipped, and a file moved to archived_sessions is
read again without adding anything. Input is stored without the cached part
(`cached_input_tokens` goes to cache_read_tokens). The model is the latest
`turn_context` before the event; the entrypoint is session_meta's `source`
(cli, vscode, exec; a subagent's dict -> "subagent"). A turn is complete when
its event is written, so there is no 15-min wait. Totals per month and model
match ccusage exactly (USAGE_DATA_SOURCES.md §4.1).

Usage: python3 transcript_reader.py
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import ingest_session_usage
import usage_db

PROJECTS_DIR = Path(os.environ.get("AGENT_USAGE_TRACKER_TRANSCRIPTS") or Path.home() / ".claude" / "projects")
OFFSETS_FILE = usage_db.RUNTIME_DIR / "state" / "transcript_reader" / "offsets.json"
CODEX_DIRS = [Path(p) for p in (os.environ.get("AGENT_USAGE_TRACKER_CODEX_SESSIONS") or "").split(":") if p] or \
    [Path.home() / ".codex" / "sessions", Path.home() / ".codex" / "archived_sessions"]
CODEX_OFFSETS_FILE = usage_db.RUNTIME_DIR / "state" / "transcript_reader" / "codex_offsets.json"
FINAL_AFTER_SECONDS = 900


def _epoch(stamp) -> int | None:
    try:
        return int(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def read_file(path: str, offset: int, complete_before: int, d_found: dict) -> int:
    """Reads `path` from `offset`; adds its finished requests to d_found
    (request id -> row) and returns the offset the next run starts from: the
    first line of the earliest unfinished request, else the end of the last
    complete line."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1  # complete lines only; a half-written last line waits
    d_seen = {}  # request id -> [first_ts, first line offset, best usage line]
    pos = 0
    while pos < end:
        nl = data.index(b"\n", pos)
        line, line_start = data[pos:nl], offset + pos
        pos = nl + 1
        if b'"usage"' not in line:
            continue
        try:
            d_rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d_rec, dict) or d_rec.get("type") != "assistant":
            continue
        d_msg = d_rec.get("message")
        if not isinstance(d_msg, dict) or not isinstance(d_msg.get("usage"), dict):
            continue
        key = d_rec.get("requestId") or d_msg.get("id")
        ts = _epoch(d_rec.get("timestamp"))
        if not key or ts is None:
            continue
        out = d_msg["usage"].get("output_tokens") or 0
        l_entry = d_seen.get(key)
        if l_entry is None:
            d_seen[key] = [ts, line_start, d_rec]
        elif out > (l_entry[2]["message"]["usage"].get("output_tokens") or 0):
            l_entry[2] = d_rec
    next_offset = offset + end
    machine = usage_db.machine_name()
    for key, (ts, line_start, d_rec) in d_seen.items():
        if ts > complete_before:
            next_offset = min(next_offset, line_start)
            continue
        d_msg = d_rec["message"]
        d_usage = d_msg["usage"]
        cache_write = d_usage.get("cache_creation_input_tokens") or 0
        cache_write_1h = (d_usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0
        d_row = {
            "ts": ts, "dt": usage_db.iso(ts), "request_id": key, "machine": machine,
            "session_id": d_rec.get("sessionId"), "folder": d_rec.get("cwd"), "entrypoint": d_rec.get("entrypoint"),
            "model": d_msg.get("model"),
            "input_tokens": d_usage.get("input_tokens") or 0, "output_tokens": d_usage.get("output_tokens") or 0,
            "cache_read_tokens": d_usage.get("cache_read_input_tokens") or 0,
            "cache_write_5m_tokens": max(cache_write - cache_write_1h, 0), "cache_write_1h_tokens": cache_write_1h,
        }
        d_row["usd"] = usage_db.request_usd(d_row["model"], d_row["input_tokens"], d_row["output_tokens"],
                                            d_row["cache_read_tokens"], d_row["cache_write_5m_tokens"],
                                            d_row["cache_write_1h_tokens"])
        d_found.setdefault(key, d_row)
    return next_offset


def read_codex_file(path: str, offset: int, d_context: dict, l_found: list) -> int:
    """Reads a Codex session file from `offset`; appends one row per new
    token_count event to l_found. d_context (thread id, cwd, model,
    entrypoint) is updated in place and kept between runs, since a later
    run starts mid-file. Returns the next offset (end of the last complete
    line)."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    machine = usage_db.machine_name()
    for line in data[:end].splitlines():
        if b'"session_meta"' not in line and b'"turn_context"' not in line and b'"token_count"' not in line:
            continue
        try:
            d_rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d_rec, dict) or not isinstance(d_rec.get("payload"), dict):
            continue
        d_pay = d_rec["payload"]
        if d_rec.get("type") == "session_meta":
            source = d_pay.get("source")
            d_context.update(thread=d_pay.get("id"), cwd=d_pay.get("cwd"),
                             entrypoint=source if isinstance(source, str) else "subagent")
        elif d_rec.get("type") == "turn_context":
            d_context.update(model=d_pay.get("model") or d_context.get("model"), cwd=d_pay.get("cwd") or d_context.get("cwd"))
        elif d_pay.get("type") == "token_count" and isinstance(d_pay.get("info"), dict):
            d_total, d_last = d_pay["info"].get("total_token_usage"), d_pay["info"].get("last_token_usage")
            ts = _epoch(d_rec.get("timestamp"))
            if not isinstance(d_total, dict) or not isinstance(d_last, dict) or ts is None or not d_context.get("thread"):
                continue
            cached = d_last.get("cached_input_tokens") or 0
            d_row = {
                "ts": ts, "dt": usage_db.iso(ts), "request_id": f"{d_context['thread']}:{d_total.get('total_tokens')}",
                "machine": machine, "session_id": d_context["thread"], "folder": d_context.get("cwd"),
                "entrypoint": d_context.get("entrypoint"), "model": d_context.get("model"),
                "input_tokens": max((d_last.get("input_tokens") or 0) - cached, 0),
                "output_tokens": d_last.get("output_tokens") or 0, "cache_read_tokens": cached,
                "cache_write_5m_tokens": d_last.get("cache_write_input_tokens") or 0, "cache_write_1h_tokens": 0,
            }
            d_row["usd"] = usage_db.request_usd(d_row["model"], d_row["input_tokens"], d_row["output_tokens"],
                                                d_row["cache_read_tokens"], d_row["cache_write_5m_tokens"], 0)
            l_found.append(d_row)
    return offset + end


def read_codex(now: int) -> str:
    """All Codex session files, incrementally; stores their new turns."""
    try:
        d_offsets = json.loads(CODEX_OFFSETS_FILE.read_text())
    except (OSError, ValueError):
        d_offsets = {}
    l_found, d_new, n_read = [], {}, 0
    l_stack = [str(d) for d in CODEX_DIRS if d.is_dir()]
    while l_stack:
        for entry in os.scandir(l_stack.pop()):
            if entry.is_dir(follow_symlinks=False):
                l_stack.append(entry.path)
                continue
            if not entry.name.endswith(".jsonl"):
                continue
            st = entry.stat()
            ino, offset, d_context = d_offsets.get(entry.path, (st.st_ino, 0, {}))
            if ino != st.st_ino or st.st_size < offset:
                offset, d_context = 0, {}
            if st.st_size > offset:
                n_read += 1
                offset = read_codex_file(entry.path, offset, d_context, l_found)
            d_new[entry.path] = (st.st_ino, offset, d_context)
    if not l_found and not d_new:
        return "codex: no session files"
    db = usage_db.open_sessions("codex")
    db.execute("BEGIN IMMEDIATE")
    try:
        n_added = ingest_session_usage.add_requests(db, l_found)
        # Turns are complete when written: everything up to a minute ago is in.
        ingest_session_usage.add_scan(db, now, now - 60, n_added)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    CODEX_OFFSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CODEX_OFFSETS_FILE.with_name(f"{CODEX_OFFSETS_FILE.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d_new))
    tmp.replace(CODEX_OFFSETS_FILE)
    return f"codex: files read {n_read}, turns added {n_added}"


def main() -> None:
    started = time.time()
    now = int(started)
    complete_before = now - FINAL_AFTER_SECONDS
    try:
        d_offsets = json.loads(OFFSETS_FILE.read_text())
    except (OSError, ValueError):
        d_offsets = {}
    d_found, d_new_offsets, n_read, n_bytes = {}, {}, 0, 0
    l_stack = [str(PROJECTS_DIR)] if PROJECTS_DIR.is_dir() else []
    while l_stack:
        for entry in os.scandir(l_stack.pop()):
            if entry.is_dir(follow_symlinks=False):
                l_stack.append(entry.path)
                continue
            if not entry.name.endswith(".jsonl"):
                continue
            st = entry.stat()
            ino, offset = d_offsets.get(entry.path, (st.st_ino, 0))
            if ino != st.st_ino or st.st_size < offset:
                offset = 0  # replaced or truncated: start over (stored requests are skipped)
            if st.st_size > offset:
                n_read += 1
                n_bytes += st.st_size - offset
                offset = read_file(entry.path, offset, complete_before, d_found)
            d_new_offsets[entry.path] = (st.st_ino, offset)
    db = usage_db.open_sessions("claude")
    db.execute("BEGIN IMMEDIATE")
    try:
        n_added = ingest_session_usage.add_requests(db, sorted(d_found.values(), key=lambda d: d["ts"]))
        ingest_session_usage.add_scan(db, now, complete_before, n_added)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    # Offsets only after the commit: a crash in between re-reads, and the
    # re-read requests are skipped as already stored.
    OFFSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OFFSETS_FILE.with_name(f"{OFFSETS_FILE.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d_new_offsets))
    tmp.replace(OFFSETS_FILE)
    codex = read_codex(now)
    print(f"{usage_db.iso(now)} claude: files read {n_read}, {n_bytes / 1e6:.1f} MB, requests added {n_added}; {codex}; "
          f"{time.time() - started:.2f} s", flush=True)


if __name__ == "__main__":
    main()
