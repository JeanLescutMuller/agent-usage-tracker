#!/usr/bin/env python3
"""The gate of data/<agent>/sessions_usages.db: the only code that writes it.

Three kinds of rows, from three collectors:
  - session snapshots (statusline_payload_reader.py): a session's cumulative
    cost, model and prompt-cache statistics at one observed_at. Skipped when
    they repeat the session's latest snapshot (dedup on), as before.
  - telemetry events (telemetry_receiver.py): Claude Code's own per-request
    records, including the requests transcripts never contain.
  - requests (transcript_reader.py): one row per API request found in a
    transcript, keyed by request_id.
Every row is insert-only and deduplicated (`row_hash` or `request_id`
UNIQUE). Nothing here may carry a quota percent, under any name (scope rule,
USAGE_DATA_REFERENCE.md §1): such a row is rejected with ValueError.

Command line (migration, and later the VM push): one JSONL row per stdin
line, in the old session-file shape, inserted with the line itself as `raw`:
    ingest_session_usage.py --agent claude [--session-id ID] [--no-dedup] < rows.jsonl
`claude_statusline` lines are snapshots of --session-id; `claude_otel`
lines are telemetry events. Prints {"inserted": n, "duplicate": n, ...}.
"""
import json
import re
import sys

import usage_db

# Session ids are plain tokens; "account" is reserved (it was the account
# file's name when session ids were file names).
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")


def valid_session_id(session_id) -> bool:
    return isinstance(session_id, str) and bool(SESSION_ID_RE.match(session_id)) and session_id != "account"


def _check_no_percent(obj, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if usage_db.PERCENT_KEY_RE.search(str(key)):
                raise ValueError(f"quota-percent-like key in session data: {path}{key}")
            _check_no_percent(value, f"{path}{key}.")
    elif isinstance(obj, list):
        for value in obj:
            _check_no_percent(value, path)


def add_snapshot(db, session_id: str, d_row: dict, raw: str | None = None, dedup: bool = True) -> str:
    """One statusline session row {ts, iso, source, observed_at, model_id,
    session_cost_usd, prompt_cache}. Returns "inserted", "duplicate" or
    "redundant" (same model, cost and cache statistics as the session's
    latest snapshot)."""
    if not valid_session_id(session_id):
        raise ValueError(f"not a plain session id: {session_id!r}")
    if not isinstance(d_row, dict) or not isinstance(d_row.get("ts"), (int, float)):
        raise ValueError("a row is a JSON object with a numeric ts")
    _check_no_percent(d_row)
    cost = d_row.get("session_cost_usd")
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float))):
        raise ValueError(f"session_cost_usd is not a number: {cost!r}")
    raw = raw if raw is not None else json.dumps(d_row, separators=(",", ":"))
    prompt_cache = d_row.get("prompt_cache")
    if dedup:
        last = db.execute("SELECT model_id, session_cost_usd, prompt_cache FROM session_snapshots "
                          "WHERE session_id = ? ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
        if last is not None and (last[0], last[1], json.loads(last[2]) if last[2] else None) == \
                (d_row.get("model_id"), cost, prompt_cache):
            return "redundant"
    ts = int(d_row.get("observed_at") or d_row["ts"])
    cur = db.execute(
        "INSERT OR IGNORE INTO session_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
        (ts, usage_db.iso(ts), session_id, d_row.get("model_id"), cost,
         None if prompt_cache is None else json.dumps(prompt_cache), int(d_row["ts"]), raw,
         usage_db.row_hash(raw, session_id)))
    return "inserted" if cur.rowcount else "duplicate"


def add_telemetry(db, d_row: dict, raw: str | None = None) -> str:
    """One telemetry row {source: claude_otel, observed_at, received_at,
    event, time_unix_nano, attributes, resource}."""
    if not isinstance(d_row, dict) or d_row.get("source") != "claude_otel" or not isinstance(d_row.get("attributes"), dict):
        raise ValueError("a telemetry row is a claude_otel object with attributes")
    _check_no_percent(d_row)
    raw = raw if raw is not None else json.dumps(d_row)
    d_attr = d_row["attributes"]
    session_id = d_attr.get("session.id")
    ts = int(d_row["observed_at"])

    def num(key, kind):
        value = d_attr.get(key)
        return kind(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    cur = db.execute(
        "INSERT OR IGNORE INTO telemetry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts, usage_db.iso(ts), d_row.get("event"), session_id if valid_session_id(session_id) else None,
         d_attr.get("request_id"), d_attr.get("model"), d_attr.get("query_source"),
         num("input_tokens", int), num("output_tokens", int), num("cache_read_tokens", int),
         num("cache_creation_tokens", int), num("cost_usd", float), d_row.get("received_at"), raw,
         usage_db.row_hash(raw)))
    return "inserted" if cur.rowcount else "duplicate"


REQUEST_COLUMNS = ("ts", "dt", "request_id", "machine", "session_id", "folder", "entrypoint", "model",
                   "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_5m_tokens",
                   "cache_write_1h_tokens", "usd")


def add_requests(db, l_requests: list[dict]) -> int:
    """Request rows (dicts with REQUEST_COLUMNS). A request_id already stored
    is skipped: a resumed session copies earlier requests into its new
    transcript. Returns how many were inserted."""
    n = 0
    for d_req in l_requests:
        if not d_req.get("request_id"):
            raise ValueError("a request needs a request_id")
        for key in REQUEST_COLUMNS[8:13]:
            if not isinstance(d_req.get(key), int) or d_req[key] < 0:
                raise ValueError(f"{key} must be a non-negative integer: {d_req.get(key)!r}")
        cur = db.execute(f"INSERT OR IGNORE INTO requests ({','.join(REQUEST_COLUMNS)}) "
                         f"VALUES ({','.join('?' * len(REQUEST_COLUMNS))})",
                         [d_req[k] for k in REQUEST_COLUMNS])
        n += cur.rowcount
    return n


def add_scan(db, ts: int, complete_through_ts: int, requests_added: int) -> None:
    db.execute("INSERT INTO scans VALUES (?,?,?,?)", (ts, usage_db.iso(ts), complete_through_ts, requests_added))


def main() -> None:
    import argparse  # here, not at the top: the statusline path never needs it
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--agent", required=True, choices=usage_db.AGENTS)
    parser.add_argument("--session-id", help="the session the snapshot lines belong to")
    parser.add_argument("--no-dedup", action="store_true", help="keep repeated snapshots (history as is)")
    args = parser.parse_args()
    db = usage_db.open_sessions(args.agent)
    d_counts = {"inserted": 0, "duplicate": 0, "redundant": 0, "rejected": 0}
    db.execute("BEGIN IMMEDIATE")
    for n, line in enumerate(sys.stdin, 1):
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        try:
            d_row = json.loads(raw)
            if d_row.get("source") == "claude_otel":
                result = add_telemetry(db, d_row, raw)
            elif d_row.get("source") == "claude_statusline":
                result = add_snapshot(db, args.session_id, d_row, raw, not args.no_dedup)
            else:
                raise ValueError(f"unknown session row source {d_row.get('source')!r}")
            d_counts[result] += 1
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            d_counts["rejected"] += 1
            print(f"line {n} rejected: {type(exc).__name__}: {exc}", file=sys.stderr)
        if n % 20000 == 0:
            db.execute("COMMIT")
            db.execute("BEGIN IMMEDIATE")
    db.execute("COMMIT")
    print(json.dumps(d_counts))


if __name__ == "__main__":
    main()
