#!/usr/bin/env python3
"""Samples Anthropic's utilization API and correlates it with local token
usage, so the relationship between reported utilization % and actual
per-model token consumption can be checked empirically instead of guessed.

Two independent, append-only logs:

  data/utilization-log.jsonl - one record per run (every 5 min, via
    LaunchAgent), the FULL raw GET /api/oauth/usage response plus its HTTP
    response headers. This side MUST be polled - the endpoint has no
    history, a missed tick is a permanently lost reading.

  data/token-events.jsonl - one record per individual assistant message
    with token usage, parsed from every *.jsonl transcript Claude Code
    itself writes under ~/.claude/projects/. Every field on the entry and
    on message.usage is kept verbatim (not reduced to a few named
    counters) - model, effort, session/cwd/sidechain identity, the
    cache_creation 5m/1h split, thinking tokens, service tier, speed,
    stop reason, request/message ids, etc. Deliberately excludes message
    *content* (tool inputs/outputs, text) - not needed for usage/metering
    analysis, would multiply storage, and would needlessly duplicate
    conversation content (potentially sensitive) into a second, less
    protected file.

    This side doesn't strictly need 5-min polling (the source transcripts
    are already durable, timestamped records) - EXCEPT Claude Code prunes
    transcripts older than `cleanupPeriodDays` (default 30, unset on this
    machine) on every startup. So this poller is this data's only chance
    to survive past that window; incremental scanning on the same 5-min
    timer as the API side is just the simplest way to keep up before that
    pruning catches up to it.

Byte-offset state (data/offsets.json) makes incremental scans cheap - only
bytes written since the last run are re-read. Run with --backfill once
after a fresh deploy (or after this schema changes) to capture full
history for every transcript still on disk, ignoring stored offsets.
"""
import calendar
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
UTIL_LOG_FILE = DATA_DIR / "utilization-log.jsonl"
EVENTS_LOG_FILE = DATA_DIR / "token-events.jsonl"
OFFSETS_FILE = DATA_DIR / "offsets.json"
TRANSCRIPTS_DIR = Path.home() / ".claude" / "projects"

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"

# Response headers worth keeping - request-id lets a specific reading be
# cross-referenced/reported to Anthropic support if a number ever looks
# wrong; date is the server's own clock, useful to sanity-check local
# clock skew; org/workspace id distinguish readings if this ever runs
# under more than one account.
HEADERS_TO_KEEP = (
    "date", "request-id", "anthropic-organization-id",
    "anthropic-workspace-id", "server-timing",
)


def fetch_token() -> str | None:
    """Same macOS Keychain read statusline-usage-fetch.sh uses - Claude
    Code itself writes the OAuth token here on login, under this exact
    service name."""
    try:
        d_raw = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        d_creds = json.loads(d_raw.stdout)
        return d_creds.get("claudeAiOauth", {}).get("accessToken") or None
    except Exception:
        return None


def fetch_usage(token: str) -> tuple[dict | None, dict | None]:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            d_headers = {k: v for k, v in resp.headers.items() if k.lower() in HEADERS_TO_KEEP}
            return body, d_headers
    except (urllib.error.URLError, json.JSONDecodeError):
        return None, None


def load_offsets() -> dict:
    if OFFSETS_FILE.exists():
        try:
            return json.loads(OFFSETS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_offsets(d_offsets: dict) -> None:
    tmp = OFFSETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d_offsets))
    tmp.replace(OFFSETS_FILE)


def entry_to_event(entry: dict, path: Path) -> dict | None:
    """Builds one fully-detailed event record from a raw transcript line -
    every field on the entry and on message.usage kept verbatim, nothing
    picked-and-chosen, so a usage sub-field Anthropic adds later shows up
    automatically instead of silently being dropped."""
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


def scan_new_token_events(d_offsets: dict, backfill: bool) -> list[dict]:
    """Reads bytes appended to each transcript since its stored offset
    (or from byte 0 for every file, ignoring stored offsets, if
    backfill=True), yields one fully-detailed event per usage-bearing
    assistant message. Mutates d_offsets in place with new read positions
    (only up to the last complete line - a partially-written last line is
    left for the next run)."""
    l_events = []
    l_paths = list(TRANSCRIPTS_DIR.rglob("*.jsonl"))

    for path in l_paths:
        key = str(path)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue

        offset = 0 if backfill else d_offsets.get(key, 0)
        if offset > size:
            offset = 0  # file was rotated/truncated - start over defensively
        if offset == size:
            d_offsets.setdefault(key, size)
            continue  # nothing new

        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()

        # Keep only complete lines; remember how many bytes that was so a
        # message still being written mid-line gets picked up next run.
        last_newline = chunk.rfind(b"\n")
        if last_newline == -1:
            continue  # no complete line yet
        d_offsets[key] = offset + last_newline + 1

        for line in chunk[:last_newline].split(b"\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = entry_to_event(entry, path)
            if event:
                l_events.append(event)

    # Drop offsets for transcripts that no longer exist, so the state file
    # doesn't grow forever across a machine's lifetime.
    l_seen = {str(p) for p in l_paths}
    for stale_key in [k for k in d_offsets if k not in l_seen]:
        del d_offsets[stale_key]

    return l_events


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backfill = "--backfill" in sys.argv

    d_offsets = load_offsets()
    l_events = scan_new_token_events(d_offsets, backfill)
    save_offsets(d_offsets)

    if l_events:
        with EVENTS_LOG_FILE.open("a") as f:
            for event in l_events:
                f.write(json.dumps(event) + "\n")

    token = fetch_token()
    d_api, d_api_headers = fetch_usage(token) if token else (None, None)

    record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "api": d_api,
        "api_headers": d_api_headers,
    }
    with UTIL_LOG_FILE.open("a") as f:
        f.write(json.dumps(record) + "\n")

    if backfill:
        print(f"Backfilled {len(l_events)} token events from {len(d_offsets)} transcripts.")


if __name__ == "__main__":
    main()
