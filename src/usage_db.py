"""Shared by the two gates (ingest_account_quota.py, ingest_session_usage.py)
and by the collectors' read-only queries: where the databases live, their
schema, how to open them, and the verified Claude price table.

Two SQLite databases per agent, under the runtime's data/<agent>/:
  account_quotas.db   account-wide quota readings (percent), and failed poll
                      attempts. Never a per-session cost or token count.
  sessions_usages.db  per-session and per-request tokens and USD. Never a
                      quota percent.
That split is the scope rule of USAGE_DATA_REFERENCE.md §1.

Only the gates write. Every row is insert-only and carries `row_hash`
(UNIQUE), so re-inserting the same row - a re-run migration, a re-sent push -
is a no-op. Rows that came from the old JSONL files, or that a collector
builds in that same shape, keep that JSON verbatim in `raw`: nothing captured
is ever reshaped or lost, and any old JSONL reader can be served by
`SELECT raw ... ORDER BY rowid`.

Every table starts with `ts` (Unix epoch seconds, when the row was true) and
`dt` (the same instant as an ISO 8601 UTC string), so a plain ORDER BY ts is
chronological.
"""
import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path

# The runtime tree (~/opt/agent-usage-tracker). Overridable for tests and for
# writing into another tree (the VM's central copy, later).
RUNTIME_DIR = Path(os.environ.get("AGENT_USAGE_TRACKER_RUNTIME") or Path.home() / "opt" / "agent-usage-tracker")
AGENTS = ("claude", "codex")

# Bumped when a schema change needs a migration; 0 means "not created yet".
SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 10_000

ACCOUNT_SCHEMA = """
CREATE TABLE account_quotas (
    ts INTEGER NOT NULL,              -- when the reading was true: observed_at (push), request time (poll)
    dt TEXT NOT NULL,
    source TEXT NOT NULL,             -- claude_statusline, claude_api, codex_app_server, codex_plan_limit_history
    five_hour_pct REAL,               -- NULL when the row carries no current reading (plan-limit history)
    five_hour_resets_ts INTEGER,
    seven_day_pct REAL,
    seven_day_resets_ts INTEGER,
    observed_by_session TEXT,         -- push rows: the session that was rendering (who saw it, not whose usage)
    written_ts INTEGER NOT NULL,      -- when the row was written (the old JSONL `ts`)
    raw TEXT NOT NULL,                -- the row in its JSONL shape, verbatim
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX account_quotas_ts ON account_quotas(ts);
CREATE INDEX account_quotas_written ON account_quotas(written_ts);
CREATE INDEX account_quotas_source_written ON account_quotas(source, written_ts);
CREATE INDEX account_quotas_session ON account_quotas(observed_by_session, source);
CREATE TABLE poll_errors (
    ts INTEGER NOT NULL,              -- when the attempt was made
    dt TEXT NOT NULL,
    source TEXT NOT NULL,
    stage TEXT,
    type TEXT,
    status INTEGER,
    retry_after_s INTEGER,
    raw TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX poll_errors_source_ts ON poll_errors(source, ts);
-- The freshest reading of the account, whatever wrote it.
CREATE VIEW latest AS
    SELECT ts, dt, source, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts
    FROM account_quotas
    WHERE five_hour_pct IS NOT NULL OR seven_day_pct IS NOT NULL
    ORDER BY ts DESC, rowid DESC
    LIMIT 1;
"""

SESSIONS_SCHEMA = """
CREATE TABLE session_snapshots (     -- the statusline's per-session cumulative cost and cache statistics
    ts INTEGER NOT NULL,              -- observed_at
    dt TEXT NOT NULL,
    session_id TEXT NOT NULL,
    model_id TEXT,
    session_cost_usd REAL,
    prompt_cache TEXT,                -- JSON object, as Claude Code sent it
    written_ts INTEGER NOT NULL,
    raw TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX session_snapshots_session ON session_snapshots(session_id);
CREATE INDEX session_snapshots_ts ON session_snapshots(ts);
CREATE TABLE telemetry (             -- Claude Code's own per-request events (OTLP), usage events only
    ts INTEGER NOT NULL,              -- event time
    dt TEXT NOT NULL,
    event TEXT NOT NULL,              -- api_request, api_error, api_refusal, api_retries_exhausted
    session_id TEXT,                  -- NULL when the event had no usable session id
    request_id TEXT,
    model TEXT,
    query_source TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    cost_usd REAL,                    -- Claude Code's own list-price figure
    received_ts INTEGER,
    raw TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX telemetry_request ON telemetry(request_id);
CREATE INDEX telemetry_session ON telemetry(session_id);
CREATE INDEX telemetry_ts ON telemetry(ts);
CREATE TABLE requests (              -- one row per API request found in a transcript; rebuildable from them
    ts INTEGER NOT NULL,              -- the request's first transcript line
    dt TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    machine TEXT NOT NULL,
    session_id TEXT,
    folder TEXT,                      -- the execution folder (cwd)
    entrypoint TEXT,                  -- cli (interactive), sdk-cli, sdk-py, ...
    model TEXT,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_5m_tokens INTEGER NOT NULL,
    cache_write_1h_tokens INTEGER NOT NULL,
    usd REAL                          -- list price from PRICES; NULL for an unknown model
);
CREATE INDEX requests_ts ON requests(ts);
CREATE INDEX requests_session ON requests(session_id);
CREATE TABLE scans (                 -- one row per transcript_reader.py run
    ts INTEGER NOT NULL,
    dt TEXT NOT NULL,
    complete_through_ts INTEGER NOT NULL,  -- every request that started before this is in `requests`
    requests_added INTEGER NOT NULL
);
CREATE INDEX scans_ts ON scans(ts);
-- One telemetry event per request id: a batch Claude Code re-sends is
-- stored again (its received_at differs), and must not count twice.
CREATE VIEW telemetry_requests AS
    SELECT * FROM telemetry
    WHERE rowid IN (SELECT MIN(rowid) FROM telemetry
                    WHERE event = 'api_request' AND request_id IS NOT NULL GROUP BY request_id);
-- Every request once: transcript requests, with Claude Code's own cost when
-- telemetry has it, plus the requests only telemetry knows (prompt
-- suggestions, compaction, web search, titles), placed in their session's
-- folder.
CREATE VIEW usage_requests AS
    SELECT r.ts, r.dt, r.request_id, r.machine, r.session_id, r.folder, r.entrypoint, r.model,
           r.input_tokens, r.output_tokens, r.cache_read_tokens,
           r.cache_write_5m_tokens + r.cache_write_1h_tokens AS cache_write_tokens,
           COALESCE(t.cost_usd, r.usd) AS usd,
           CASE WHEN t.cost_usd IS NOT NULL THEN 'telemetry' WHEN r.usd IS NOT NULL THEN 'price_table' END AS usd_from,
           'transcript' AS seen_in
    FROM requests r
    LEFT JOIN telemetry_requests t ON t.request_id = r.request_id
    UNION ALL
    SELECT t.ts, t.dt, t.request_id,
           (SELECT machine FROM requests WHERE session_id = t.session_id LIMIT 1),
           t.session_id,
           (SELECT folder FROM requests WHERE session_id = t.session_id LIMIT 1),
           (SELECT entrypoint FROM requests WHERE session_id = t.session_id LIMIT 1),
           t.model, t.input_tokens, t.output_tokens, t.cache_read_tokens, t.cache_creation_tokens,
           t.cost_usd, 'telemetry', 'telemetry'
    FROM telemetry_requests t
    WHERE NOT EXISTS (SELECT 1 FROM requests r WHERE r.request_id = t.request_id);
-- Totals per 5-minute slot, execution folder and interactive flag. `final`:
-- the slot ended before the last transcript scan's complete_through_ts, so
-- it will not change any more.
CREATE VIEW usage_5m AS
    SELECT (ts / 300) * 300 AS slot_ts,
           strftime('%Y-%m-%dT%H:%M:%SZ', (ts / 300) * 300, 'unixepoch') AS slot_dt,
           folder,
           entrypoint = 'cli' AS interactive,
           COUNT(*) AS requests,
           SUM(usd) AS usd,
           SUM(input_tokens) AS input_tokens,
           SUM(output_tokens) AS output_tokens,
           SUM(cache_read_tokens) AS cache_read_tokens,
           SUM(cache_write_tokens) AS cache_write_tokens,
           (ts / 300) * 300 + 300 <= COALESCE((SELECT MAX(complete_through_ts) FROM scans), 0) AS final
    FROM usage_requests
    GROUP BY slot_ts, folder, interactive;
"""

# Claude list prices, $ per million tokens: (input, output, cache-read ratio
# to input). Cache writes cost 1.25x input (5 min) and 2x input (1 h) for
# every model. Each row reproduces Claude Code's own cost_usd and ccusage's
# monthly totals exactly (USAGE_DATA_SOURCES.md §3.4, verified 2026-10-07).
PRICES = {
    "claude-opus-5": (5, 25, .1),
    "claude-opus-5-5": (4, 20, .05),
    "claude-sonnet-5": (2, 10, .1),
    "claude-sonnet-5-5": (2, 10, .1),
    "claude-haiku-4-5-20251001": (1, 5, .1),
    # Codex: no dollar figure exists upstream for a subscription. This is the
    # price ccusage 20.0.26 applies, which reproduces its monthly totals
    # exactly (fitted 2026-10-08). Other Codex models have no verified price:
    # their usd stays NULL. adhoc_quotas_analysis/CONCLUSIONS.md uses $5/$30
    # for gpt-5.6-sol instead; see TODO.md.
    "gpt-5.6-sol": (4, 20, .1),
}

# Key names that mean "a quota percent": never allowed in sessions_usages.db
# (the check USAGE_DATA_REFERENCE.md §8 runs on the data).
PERCENT_KEY_RE = re.compile(r"pct|percent|utiliz|basis_points", re.IGNORECASE)


def db_path(agent: str, name: str) -> Path:
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}")
    return RUNTIME_DIR / "data" / agent / f"{name}.db"


def connect(path: Path, schema: str, readonly: bool = False) -> sqlite3.Connection:
    """Opens a database in autocommit mode (callers group writes with
    `with db:` or BEGIN IMMEDIATE), creating it with `schema` on first use.
    WAL lets readers (the statusline, every 5 min the pollers) run while a
    writer commits; the busy timeout absorbs concurrent writers (every open
    Claude session's statusline). A read-only open of a missing database
    returns None rather than creating it.

    Read-only means `PRAGMA query_only`, not a `mode=ro` URI: macOS's system
    SQLite intermittently fails a mode=ro open with "unable to open database
    file" when no other connection holds the WAL's -shm file at that instant
    (seen 2026-10-08), which happens all the time here since every statusline
    render opens and closes one. Readers outside this repo should open the
    same way."""
    if readonly:
        if not path.exists():
            return None
        db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        db.execute("PRAGMA query_only = ON")
        return db
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    db.execute("PRAGMA synchronous = NORMAL")
    if db.execute("PRAGMA user_version").fetchone()[0] == 0:
        # BEGIN IMMEDIATE takes the write lock first, so two processes
        # creating the same new database can't both run the schema.
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                # One statement at a time: executescript() would commit the
                # open transaction first.
                for statement in [s for s in schema.split(";\n") if s.strip()]:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("PRAGMA journal_mode = WAL")
    return db


def open_account(agent: str, readonly: bool = False):
    return connect(db_path(agent, "account_quotas"), ACCOUNT_SCHEMA, readonly)


def open_sessions(agent: str, readonly: bool = False):
    return connect(db_path(agent, "sessions_usages"), SESSIONS_SCHEMA, readonly)


def row_hash(raw: str, key: str = "") -> str:
    """Identity of a row for deduplication. `key` adds context the raw text
    doesn't carry (a session snapshot's session id lives in no field)."""
    return hashlib.sha1(f"{key}\n{raw}".encode()).hexdigest()


def iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def machine_name() -> str:
    """This machine's name, by job-runner's rule so that every project agrees
    (~/opt/job-runner/lib.sh, JR_MACHINE_NAME): on macOS the LocalHostName in
    lower case (chan-lescut-macbook-pro), elsewhere the short hostname
    (H-Frank-1). Not the Mac's network-derived hostname: with no HostName set
    it drifted from chan-lescut-macbook-pro-1 to Chan-Lescut-MacBook-Pro to
    chan-lescut-macbook-pro on 2026-10-08 (USAGE_DATA_REFERENCE.md §11)."""
    # Imports here, not at the top: the statusline path never needs them.
    import socket
    import subprocess
    import sys
    if os.environ.get("JR_MACHINE_NAME"):
        return os.environ["JR_MACHINE_NAME"]
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                                  timeout=5, check=True).stdout.strip().lower()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return socket.gethostname().split(".")[0]


def request_usd(model: str | None, inp: int, out: int, cache_read: int, cache_write_5m: int, cache_write_1h: int):
    """List-price USD of one request, or None for a model not in PRICES."""
    if model not in PRICES:
        return None
    pin, pout, read_ratio = PRICES[model]
    return (inp * pin + out * pout + cache_read * pin * read_ratio
            + cache_write_5m * pin * 1.25 + cache_write_1h * pin * 2) / 1e6
