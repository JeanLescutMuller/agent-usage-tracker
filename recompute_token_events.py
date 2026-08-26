#!/usr/bin/env python3
"""Rebuilds data/token-events.jsonl from scratch by scanning every *.jsonl
transcript under ~/.claude/projects/ - one record per individual assistant
message with token usage, full fidelity (every field on the entry and on
message.usage kept verbatim, not reduced to a few named counters: model,
effort, session/cwd/sidechain identity, the cache_creation 5m/1h split,
thinking tokens, service tier, speed, stop reason, request/message ids,
etc). Deliberately excludes message *content* (tool inputs/outputs, text) -
out of scope for usage/metering analysis, and would needlessly duplicate
conversation content (potentially sensitive) into a second, less protected
file.

Not scheduled, not incremental, no offset state - run this by hand whenever
you're about to analyze the data. It's cheap (~2s for ~14k events as of
2026-08-26) and the source transcripts are durable for cleanupPeriodDays
(365 on this machine) - there's nothing here that needs preserving between
runs, so a full rebuild each time is simpler than tracking what's already
been seen.

Usage: python3 recompute_token_events.py
"""
import calendar
import json
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
EVENTS_LOG_FILE = DATA_DIR / "token-events.jsonl"
TRANSCRIPTS_DIR = Path.home() / ".claude" / "projects"


def entry_to_event(entry: dict, path: Path) -> dict | None:
    message = entry.get("message")
    if entry.get("type") != "assistant" or not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not usage:
        return None

    timestamp = entry.get("timestamp")  # e.g. "2026-08-26T09:27:28.965Z" (UTC)
    ts = None
    if timestamp:
        try:
            # timegm, not mktime - the parsed struct is UTC (trailing "Z"),
            # mktime would wrongly reinterpret it as local time.
            ts = calendar.timegm(time.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            ts = None

    return {
        "ts": ts,
        "iso": timestamp,
        "model": message.get("model"),
        "effort": entry.get("effort"),
        "session_id": entry.get("sessionId") or entry.get("session_id"),
        "is_sidechain": entry.get("isSidechain"),
        "cwd": entry.get("cwd"),
        "git_branch": entry.get("gitBranch"),
        "cc_version": entry.get("version"),
        "entrypoint": entry.get("entrypoint"),
        "user_type": entry.get("userType"),
        "uuid": entry.get("uuid"),
        "parent_uuid": entry.get("parentUuid"),
        "request_id": entry.get("requestId"),
        "message_id": message.get("id"),
        "stop_reason": message.get("stop_reason"),
        "stop_sequence": message.get("stop_sequence"),
        "stop_details": message.get("stop_details"),
        "diagnostics": message.get("diagnostics"),
        "usage": usage,  # raw, unmodified - input/output/cache tokens,
                          # cache_creation 5m/1h split, output_tokens_details
                          # (thinking_tokens), server_tool_use, service_tier,
                          # speed, inference_geo, iterations[] - whatever
                          # Anthropic puts here, verbatim.
        "transcript_file": str(path),
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    n_events = 0
    n_files = 0
    tmp = EVENTS_LOG_FILE.with_suffix(".tmp")
    with tmp.open("w") as out:
        for path in TRANSCRIPTS_DIR.rglob("*.jsonl"):
            n_files += 1
            with path.open("rb") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # last line of a file mid-write - skip
                    event = entry_to_event(entry, path)
                    if event:
                        out.write(json.dumps(event) + "\n")
                        n_events += 1
    tmp.replace(EVENTS_LOG_FILE)

    print(f"Rebuilt {EVENTS_LOG_FILE} - {n_events} events from {n_files} transcripts.")


if __name__ == "__main__":
    main()
