#!/usr/bin/env python3
"""Rebuilds data/codex-token-events.jsonl from scratch by scanning every
*.jsonl rollout under ~/.codex/sessions/ - the Codex analogue of
recompute_token_events.py.

Codex's local session rollout files were assumed (in an earlier pass
through AGENTS.md) not to carry token counts at all, on the theory that
only a per-thread `account/usage/read` RPC call could get that detail.
That assumption was wrong, confirmed empirically 2026-08-30: any session
with real turns logs an `event_msg` of `type: "token_count"` after
essentially every turn, carrying a full token-usage breakdown (info.*)
*and* a `rate_limits` snapshot (primary/secondary used_percent) at that
same instant - so this, like the Claude side, is fully recomputable from
local files and needs no API call.

One record per token_count event. Deliberately excludes turn content
(reasoning, tool calls, messages) - out of scope for usage/metering
analysis, same rationale as recompute_token_events.py.

Not scheduled, not incremental, no offset state - run this by hand
whenever you're about to analyze the data.

Usage: python3 recompute_codex_events.py
"""
import calendar
import json
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
EVENTS_LOG_FILE = DATA_DIR / "codex-token-events.jsonl"
SESSIONS_DIR = Path.home() / ".codex" / "sessions"


def iso_to_epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        # timegm, not mktime - rollout timestamps are UTC ("Z" suffix);
        # mktime would wrongly reinterpret them as local time (see
        # AGENTS.md's "Known gotchas" - the same bug bit the Claude side).
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def scan_file(path: Path):
    """Yields one dict per token_count event in this rollout file.

    A rollout is a sequential log, so we track the most recent
    session_meta (session id/cwd/git, logged once per file) and
    turn_context (model, logged once per turn) as we walk the file, and
    attach whichever was most recently seen to each token_count event -
    the same thing a real Codex client does to know "what turn is this".
    """
    d_session = {}
    d_turn = {}
    with path.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # last line of a file mid-write - skip

            entry_type = entry.get("type")
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue

            if entry_type == "session_meta":
                d_git = payload.get("git") or {}
                d_session = {
                    "session_id": payload.get("session_id"),
                    "cwd": payload.get("cwd"),
                    "originator": payload.get("originator"),
                    "cli_version": payload.get("cli_version"),
                    "git_repository_url": d_git.get("repository_url"),
                    "git_branch": d_git.get("branch"),
                    "git_commit_hash": d_git.get("commit_hash"),
                }
            elif entry_type == "turn_context":
                d_turn = {
                    "turn_id": payload.get("turn_id"),
                    "model": payload.get("model"),
                }
            elif entry_type == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                timestamp = entry.get("timestamp")
                yield {
                    "ts": iso_to_epoch(timestamp),
                    "iso": timestamp,
                    **d_session,
                    **d_turn,
                    "total_token_usage": info.get("total_token_usage"),
                    "last_token_usage": info.get("last_token_usage"),
                    "model_context_window": info.get("model_context_window"),
                    "rate_limits": payload.get("rate_limits"),
                    "session_file": str(path),
                }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    n_events = 0
    n_files = 0
    tmp = EVENTS_LOG_FILE.with_suffix(".tmp")
    with tmp.open("w") as out:
        for path in SESSIONS_DIR.rglob("*.jsonl"):
            n_files += 1
            for event in scan_file(path):
                out.write(json.dumps(event) + "\n")
                n_events += 1
    tmp.replace(EVENTS_LOG_FILE)

    print(f"Rebuilt {EVENTS_LOG_FILE} - {n_events} events from {n_files} session files.")


if __name__ == "__main__":
    main()
