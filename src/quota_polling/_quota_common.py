"""Shared helpers for poll_claude.py and poll_codex.py: tail-reading a
growing JSONL quota log, checking a heartbeat file's freshness, and writing
a provider's small "latest known quota" state file. Not a standalone entry
point - both pollers stay independently runnable (see poll_all.py's
docstring for why each is still spawned as its own subprocess), they just
import this module from their own directory, same as any other local import
for a script run directly with `python3 poll_x.py`.
"""
import json
import os
from pathlib import Path

CHUNK_BYTES = 16384

# $'\034' (ASCII file separator) - the state file's format is shared with
# ../../bin/ingest-claude-statusline.sh (the other writer) and with
# agent-statusline, which reads it for display, so it has to match exactly.
FIELD_SEP = "\x1c"


def tail_json_rows(log_file: Path) -> list[dict]:
    """Parsed rows from the tail of a JSONL log, newest last. Reads at most
    CHUNK_BYTES from the end - this runs every tick, forever, and the log
    only grows - and skips any line that fails to parse (at worst the first
    line of a non-full-file chunk, if the read boundary split a record)
    rather than raising."""
    if not log_file.exists():
        return []
    with log_file.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        chunk = min(size, CHUNK_BYTES)
        f.seek(size - chunk)
        data = f.read(chunk)
    rows = []
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def is_fresh(path: Path, window_seconds: float, now: float) -> bool:
    """True if `path`'s mtime is within window_seconds of now - the shared
    shape behind both pollers' heartbeat-driven speedup. A missing file just
    means False, which degrades gracefully to whichever idle cadence the
    caller falls back to."""
    try:
        return (now - path.stat().st_mtime) < window_seconds
    except OSError:
        return False


def write_state_if_newer(
    state_file: Path, five_pct, five_reset, week_pct, week_reset,
    source: str, observed_at: int,
) -> bool:
    """Mirrors ../../bin/ingest-claude-statusline.sh's write_quota_if_newer -
    same six FIELD_SEP-delimited fields, same rule (only overwrite if
    observed_at beats whatever's already there), so this poller and that
    ingest script can both write the same state file
    and whichever has the genuinely freshest reading wins regardless of
    write order. No locking, for the same reason the bash side skips it: a
    same-instant race could rarely clobber a fresher value with a
    slightly-less-fresh one, atomic replace still prevents any torn file,
    and the next write (poll tick or render) self-corrects. Returns True if
    it wrote, False if a fresher reading was already present.
    """
    existing_observed_at = None
    if state_file.exists():
        try:
            fields = state_file.read_text().rstrip("\n").split(FIELD_SEP)
            if len(fields) >= 6 and fields[5]:
                existing_observed_at = int(fields[5])
        except (OSError, ValueError):
            existing_observed_at = None
    if existing_observed_at is not None and observed_at <= existing_observed_at:
        return False
    state_file.parent.mkdir(parents=True, exist_ok=True)
    row = FIELD_SEP.join([
        str(five_pct), str(five_reset or ""), str(week_pct), str(week_reset or ""),
        source, str(observed_at),
    ])
    tmp = state_file.with_name(f"{state_file.name}.tmp.{os.getpid()}")
    tmp.write_text(row + "\n")
    tmp.replace(state_file)
    return True
