#!/usr/bin/env python3
"""Samples Anthropic's utilization API and correlates it with local token
usage, so the relationship between reported utilization % and actual
per-model token consumption can be checked empirically instead of guessed.

Run on a timer (see install.sh - LaunchAgent, every 5 min). Each run:
  1. Fetches GET /api/oauth/usage (same endpoint Claude Code's own /usage
     command calls - see ~/dev/bootstrap-home/files/statusline-usage-fetch.sh
     for the sibling implementation this was modeled on).
  2. Scans every *.jsonl transcript under ~/.claude/projects/ (session logs
     Claude Code itself writes) for new "assistant" messages since the last
     run, summing token usage per model.
  3. Appends one JSON line to data/utilization-log.jsonl combining both, so
     later analysis can plot utilization deltas against token deltas per
     model over time.

Byte-offset state (data/offsets.json) makes step 2 cheap - only the bytes
written since the last run are re-read, not the whole transcript history.
"""
import json
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
LOG_FILE = DATA_DIR / "utilization-log.jsonl"
OFFSETS_FILE = DATA_DIR / "offsets.json"
TRANSCRIPTS_DIR = Path.home() / ".claude" / "projects"

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"


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


def fetch_usage(token: str) -> dict | None:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError):
        return None


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


def scan_new_token_usage(d_offsets: dict) -> dict:
    """Reads only the bytes appended to each transcript since its stored
    offset, accumulates usage per model. Mutates d_offsets in place with
    the new read positions (only up to the last complete line - a
    partially-written last line is left for the next run to pick up)."""
    d_by_model: dict[str, dict] = {}
    l_paths = list(TRANSCRIPTS_DIR.rglob("*.jsonl"))

    for path in l_paths:
        key = str(path)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue

        offset = d_offsets.get(key, 0)
        if offset > size:
            offset = 0  # file was rotated/truncated - start over defensively
        if offset == size:
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
            message = entry.get("message")
            if entry.get("type") != "assistant" or not isinstance(message, dict):
                continue
            usage = message.get("usage")
            model = message.get("model")
            if not usage or not model:
                continue
            bucket = d_by_model.setdefault(model, {
                "messages": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            })
            bucket["messages"] += 1
            bucket["input_tokens"] += usage.get("input_tokens", 0)
            bucket["output_tokens"] += usage.get("output_tokens", 0)
            bucket["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0)
            bucket["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0)

    # Drop offsets for transcripts that no longer exist, so the state file
    # doesn't grow forever across a machine's lifetime.
    l_seen = {str(p) for p in l_paths}
    for stale_key in [k for k in d_offsets if k not in l_seen]:
        del d_offsets[stale_key]

    return d_by_model


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # On the very first-ever run there's no prior offset, so every existing
    # transcript's entire history would otherwise be counted as one "delta"
    # - not a real 5-minute slice. Establish a baseline instead: fast-forward
    # offsets to end-of-file without counting anything, so every record from
    # here on reflects genuine incremental usage.
    is_baseline_run = not OFFSETS_FILE.exists()

    d_offsets = load_offsets()
    d_token_deltas = scan_new_token_usage(d_offsets)
    save_offsets(d_offsets)
    if is_baseline_run:
        d_token_deltas = {}

    token = fetch_token()
    d_api = fetch_usage(token) if token else None

    record = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "api": d_api,
        "token_deltas": d_token_deltas,
        "baseline": is_baseline_run,
    }
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
