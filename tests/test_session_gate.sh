#!/bin/bash
# Tests for src/ingest_session_usage.py, the only writer of
# sessions_usages.db, and for the views readers use: usage_requests (every
# request once, Claude Code's own cost when telemetry has it) and usage_5m
# (totals per 5-minute slot, folder and interactive flag, with `final`).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SRC_DIR="$REPO_ROOT/src"
RT="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-sgate.XXXXXX")"
trap 'rm -rf "$RT"' EXIT
SDB="$RT/data/claude/sessions_usages.db"

ingest() { AGENT_USAGE_TRACKER_RUNTIME="$RT" python3 "$SRC_DIR/ingest_session_usage.py" --agent claude "$@" 2>/dev/null; }
q() {
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$SDB" "$1"
}
py() { AGENT_USAGE_TRACKER_RUNTIME="$RT" python3 -c "import sys; sys.path.insert(0, '$SRC_DIR'); import usage_db, ingest_session_usage as g
db = usage_db.open_sessions('claude')
$1"; }

section "snapshots: stored per session, repeats skipped, never a percent"
snap='{"ts":2000,"iso":"x","source":"claude_statusline","observed_at":1900,"model_id":"claude-opus-5-5","session_cost_usd":0.5,"prompt_cache":{"warm":true,"hit_ratio":0.9}}'
assert_contains "stored" "$(ingest --session-id s1 <<< "$snap")" '"inserted": 1'
assert_eq "ts = observed_at, typed columns" "1900	s1	claude-opus-5-5	0.5" "$(q "SELECT ts, session_id, model_id, session_cost_usd FROM session_snapshots")"
assert_contains "same cost, model and cache later -> redundant" "$(ingest --session-id s1 <<< "${snap/2000/2100}")" '"redundant": 1'
assert_contains "a cost change -> stored" "$(ingest --session-id s1 <<< "${snap/0.5/0.7}")" '"inserted": 1'
assert_contains "a percent key anywhere -> rejected" \
    "$(ingest --session-id s1 <<< '{"ts":1,"source":"claude_statusline","prompt_cache":{"five_hour_pct":3}}')" '"rejected": 1'
assert_contains "not a plain session id -> rejected" "$(ingest --session-id ../x <<< "$snap")" '"rejected": 1'

section "requests: inserted once per request_id"
req() { printf "{'ts': %s, 'dt': 'x', 'request_id': '%s', 'machine': 'm', 'session_id': '%s', 'folder': '%s', 'entrypoint': '%s', 'model': 'claude-opus-5', 'input_tokens': 1, 'output_tokens': 2, 'cache_read_tokens': 3, 'cache_write_5m_tokens': 4, 'cache_write_1h_tokens': 5, 'usd': %s}" "$@"; }
assert_eq "two new" "2" "$(py "print(g.add_requests(db, [$(req 1000 r1 s1 /dev/a cli 1.0), $(req 1010 r2 s1 /dev/a cli 2.0)]))")"
assert_eq "the same ids again: none" "0" "$(py "print(g.add_requests(db, [$(req 1000 r1 s1 /dev/a cli 1.0)]))")"
assert_contains "negative token count refused" "$(py "g.add_requests(db, [dict($(req 1 r9 s1 f cli 1), output_tokens=-1)])" 2>&1)" "ValueError"

section "usage_requests: Claude Code's own cost wins; telemetry-only requests join their session's folder"
tel() { printf '{"source":"claude_otel","observed_at":%s,"received_at":%s,"event":"api_request","attributes":{"session.id":"s1","request_id":"%s","model":"claude-opus-5","query_source":"%s","input_tokens":1,"output_tokens":1,"cache_read_tokens":0,"cache_creation_tokens":0,"cost_usd":%s}}' "$@"; }
printf '%s\n%s\n%s\n' "$(tel 1000 1001 r1 repl_main_thread 1.5)" "$(tel 1020 1021 t1 prompt_suggestion 0.25)" "$(tel 1020 1022 t1 prompt_suggestion 0.25)" | ingest >/dev/null
assert_eq "a re-sent event (other received_at) is stored, but counted once" "3	3" \
    "$(q "SELECT (SELECT COUNT(*) FROM telemetry), (SELECT COUNT(*) FROM usage_requests)")"
assert_eq "r1: telemetry's cost" "r1	1.5	telemetry	transcript" "$(q "SELECT request_id, usd, usd_from, seen_in FROM usage_requests WHERE request_id = 'r1'")"
assert_eq "r2: price table" "r2	2.0	price_table" "$(q "SELECT request_id, usd, usd_from FROM usage_requests WHERE request_id = 'r2'")"
assert_eq "t1: telemetry only, in its session's folder" "t1	/dev/a	cli	0.25	telemetry" \
    "$(q "SELECT request_id, folder, entrypoint, usd, seen_in FROM usage_requests WHERE request_id = 't1'")"

section "usage_5m: one row per slot, folder and interactive flag; final once scanned past"
py "g.add_requests(db, [$(req 1300 r3 s2 /opt/b sdk-py 4.0)])" >/dev/null
assert_eq "slots (1.5 from telemetry + 2.0 + 0.25)" "900	/dev/a	1	3	3.75
1200	/opt/b	0	1	4.0" "$(q "SELECT slot_ts, folder, interactive, requests, usd FROM usage_5m ORDER BY slot_ts")"
assert_eq "nothing final before any scan" "0	0" "$(q "SELECT final FROM usage_5m ORDER BY slot_ts" | paste -sd '\t' -)"
py "g.add_scan(db, 2000, 1250, 0)" >/dev/null
assert_eq "the slot ending at 1200 is final, the one ending at 1500 is not" "1	0" "$(q "SELECT final FROM usage_5m ORDER BY slot_ts" | paste -sd '\t' -)"
assert_eq "slot_dt is the slot start in UTC" "1970-01-01T00:15:00Z" "$(q "SELECT slot_dt FROM usage_5m ORDER BY slot_ts LIMIT 1")"

harness_summary
