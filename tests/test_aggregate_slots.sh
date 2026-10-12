#!/bin/bash
# Tests for src/aggregate_slots.py and its gate src/ingest_aggregates.py
# (Category 3, doc/design.md §6): final slots only, transcript
# requests with telemetry's cost, telemetry-only requests in their session's
# folder with cache writes of unknown duration, tokens_without_usd, each slot
# written once, the gate's checks. Fixture rows only; no transcript is read.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-slots.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
RT="$TMP/rt"
ADB="$RT/data/claude/aggregates/m1.db"
export AGENT_USAGE_TRACKER_RUNTIME="$RT" JR_MACHINE_NAME=m1
S1=11111111-1111-1111-1111-111111111111 S2=22222222-2222-2222-2222-222222222222 SX=99999999-9999-9999-9999-999999999999

py() { python3 -c "import sys; sys.path.insert(0, '$REPO_ROOT/src'); import usage_db, ingest_session_usage as s, ingest_aggregates as g, aggregate_slots as a; $1"; }
q() { python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$ADB" "$1"; }
# req <ts> <id> <session> <folder> <entrypoint> <model> <in> <out> <cache read> <write 5m> <write 1h>: an intermediary request
req() { py "db = usage_db.open_intermediary('claude'); db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', ($1, usage_db.iso($1), '$2', '$3', '$4', '$5', '$6', $7, $8, $9, ${10}, ${11}))"; }
# tel <ts> <request id> <session> <model> <in> <out> <cache read> <cache creation> <cost or None>: a telemetry event
tel() { py "db = usage_db.open_sessions('claude'); s.add_telemetry(db, {'source': 'claude_otel', 'observed_at': $1, 'received_at': $1, 'event': 'api_request', 'attributes': {'session.id': '$3', 'request_id': '$2', 'model': '$4', 'input_tokens': $5, 'output_tokens': $6, 'cache_read_tokens': $7, 'cache_creation_tokens': $8, **({} if '$9' == 'None' else {'cost_usd': $9})}})"; }
scan() { py "db = usage_db.open_intermediary('claude'); db.execute('INSERT INTO scans VALUES (?,?,?,0)', ($1, usage_db.iso($1), $2))"; }
# run <now>: one run, without reading transcripts
run() { py "import time; a.transcript_reader.main = lambda: ''; time.time = lambda: $1; a.main()"; }
COLS="ts, folder, entrypoint, model, requests, input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, cache_write_unknown_tokens, ROUND(usd, 4), tokens_without_usd"

req 1000 r1 $S1 /a cli opus 1 10 100 5 7
req 1100 r2 $S1 /a cli opus 2 20 200 0 0
req 1300 r3 $S2 /b sdk-py sonnet 3 30 300 0 0
req 2000 r4 $S1 /a cli opus 4 40 400 0 0
tel 1010 r1 $S1 opus 1 10 100 12 0.5
tel 1010 r1 $S1 opus 1 10 100 12 0.5     # a batch Claude Code sent twice: counted once
tel 1310 r3 $S2 sonnet 3 30 300 0 0.25
tel 1150 t1 $S1 haiku 4 40 400 30 0.1    # telemetry only (a title): the folder of its session
tel 1250 t2 $SX haiku 5 50 500 0 None    # telemetry only, a session without transcript, no cost
scan 1950 1900

section "the final slots: those ended by the scan's complete_through"
assert_contains "three slots in two final 5-min slots (900, 1200); 1500 is empty, 1800 not final" "$(run 2000)" "slots: 4 written"
assert_eq "per (slot, folder, entrypoint, model)" "900	/a	cli	haiku	1	4	40	400	0	0	30	0.1	0
900	/a	cli	opus	2	3	30	300	5	7	0	0.5	222
1200			haiku	1	5	50	500	0	0	0		555
1200	/b	sdk-py	sonnet	1	3	30	300	0	0	0	0.25	0" "$(q "SELECT $COLS FROM slots ORDER BY ts, model")"
assert_eq "machine, dt, written_ts" "m1	1970-01-01T00:15:00Z	2000" "$(q "SELECT DISTINCT machine, dt, written_ts FROM slots WHERE ts = 900")"

section "each slot written once; later slots when they become final"
assert_contains "nothing new: nothing written" "$(run 2100)" "slots: 0 written"
scan 2500 2400
assert_contains "the 1800 slot once final" "$(run 2500)" "slots: 1 written"
assert_eq "...with r4" "1800	/a	cli	opus	1	4	40	400	0	0	0		444" "$(q "SELECT $COLS FROM slots WHERE ts = 1800")"
assert_eq "no slot twice" "5" "$(q "SELECT COUNT(*) FROM slots")"

section "the gate"
good="dict(ts=600, dt=usage_db.iso(600), machine='m1', folder='', entrypoint='', model='x', requests=1, input_tokens=1, output_tokens=1, cache_read_tokens=0, cache_write_5m_tokens=0, cache_write_1h_tokens=0, cache_write_unknown_tokens=0, usd=None, tokens_without_usd=2, written_ts=1)"
assert_eq "a valid slot" "1" "$(py "print(g.add_slots(usage_db.open_aggregates('claude'), [$good]))")"
assert_eq "the same slot again: a no-op" "0" "$(py "print(g.add_slots(usage_db.open_aggregates('claude'), [$good]))")"
assert_contains "rejected: a slot of another machine (one file per machine)" "$(py "g.add_slots(usage_db.open_aggregates('claude'), [$good | dict(machine='m2')])" 2>&1)" "a slot of 'm2' in the file of 'm1'"
assert_eq "...while m2's own file takes it" "1" "$(py "print(g.add_slots(usage_db.open_aggregates('claude', machine='m2'), [$good | dict(machine='m2')]))")"
for bad in "ts=601" "dt='x'" "machine='a b'" "requests=-1" "usd='1'" "five_hour_pct=3"; do
    assert_contains "rejected: $bad" "$(py "g.add_slots(usage_db.open_aggregates('claude'), [$good | dict($bad)])" 2>&1)" "ValueError"
done
assert_eq "a bad row rejects its whole batch" "6" \
    "$(py "
try: g.add_slots(usage_db.open_aggregates('claude'), [$good | dict(ts=3000, dt=usage_db.iso(3000)), $good | dict(ts=3001)])
except ValueError: pass" ; q "SELECT COUNT(*) FROM slots")"

section "end to end: transcript files -> transcript_reader -> intermediary/ -> slots, with telemetry's cost"
E2E="$TMP/e2e"
mkdir -p "$E2E/projects/-Users-x-dev-app"
# One request streamed over three lines (the largest output counts), one in a later slot, and a resumed copy of the first.
{
    printf '%s\n' '{"type":"assistant","timestamp":"2026-10-08T12:00:00.000Z","requestId":"req_a","sessionId":"'$S1'","cwd":"/Users/x/dev/app","entrypoint":"cli","message":{"model":"claude-opus-5-5","usage":{"input_tokens":2,"output_tokens":10,"cache_read_input_tokens":100,"cache_creation_input_tokens":30,"cache_creation":{"ephemeral_5m_input_tokens":10,"ephemeral_1h_input_tokens":20}}}}'
    printf '%s\n' '{"type":"assistant","timestamp":"2026-10-08T12:00:02.000Z","requestId":"req_a","sessionId":"'$S1'","cwd":"/Users/x/dev/app","entrypoint":"cli","message":{"model":"claude-opus-5-5","usage":{"input_tokens":2,"output_tokens":500,"cache_read_input_tokens":100,"cache_creation_input_tokens":30,"cache_creation":{"ephemeral_5m_input_tokens":10,"ephemeral_1h_input_tokens":20}}}}'
    printf '%s\n' '{"type":"assistant","timestamp":"2026-10-08T12:06:00.000Z","requestId":"req_b","sessionId":"'$S1'","cwd":"/Users/x/dev/app","entrypoint":"cli","message":{"model":"claude-opus-5-5","usage":{"input_tokens":1,"output_tokens":7,"cache_read_input_tokens":50,"cache_creation_input_tokens":0}}}'
} > "$E2E/projects/-Users-x-dev-app/s1.jsonl"
mkdir -p "$E2E/projects/-Users-x-dev-app2" && head -2 "$E2E/projects/-Users-x-dev-app/s1.jsonl" > "$E2E/projects/-Users-x-dev-app2/resumed.jsonl"
e2e() {   # e2e <epoch>: one aggregates run at that time, on the fixture transcripts
    AGENT_USAGE_TRACKER_RUNTIME="$E2E/rt" AGENT_USAGE_TRACKER_TRANSCRIPTS="$E2E/projects" AGENT_USAGE_TRACKER_CODEX_SESSIONS="$E2E/none" \
        python3 -c "import sys, time; sys.path.insert(0, '$REPO_ROOT/src'); time.time = lambda: $1; import aggregate_slots as a; a.main()"
}
AGENT_USAGE_TRACKER_RUNTIME="$E2E/rt" python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/src'); import usage_db, ingest_session_usage as s
s.add_telemetry(usage_db.open_sessions('claude'), {'source': 'claude_otel', 'observed_at': 1791460805, 'received_at': 1791460806, 'event': 'api_request',
    'attributes': {'session.id': '$S1', 'request_id': 'req_a', 'model': 'claude-opus-5-5', 'cost_usd': 0.0123}})"
T0=1791460800   # 2026-10-08T12:00:00Z
out="$(e2e $((T0 + 900 + 400)))"   # 12:21:40: complete through 12:06:40, so the 12:00 slot is final, 12:05 not yet
assert_contains "the reader read both requests (req_b started before 12:06:40), the resumed copy skipped" "$out" "requests added 2"
assert_contains "...but wrote only the final slot, 12:00" "$out" "slots: 1 written"
ADB_E2E="$E2E/rt/data/claude/aggregates/m1.db"
assert_eq "12:00: req_a once (resumed copy skipped), its largest output, 5m / 1h split, telemetry's cost" \
    "1791460800	/Users/x/dev/app	cli	claude-opus-5-5	1	2	500	100	10	20	0	0.0123	0" \
    "$(python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute("SELECT ts, folder, entrypoint, model, requests, input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, cache_write_unknown_tokens, usd, tokens_without_usd FROM slots"):
    print("\t".join("" if v is None else str(v) for v in r))' "$ADB_E2E")"
e2e $((T0 + 3600)) >/dev/null
assert_eq "an hour later: req_b's slot, without telemetry: no usd, all its tokens without" "1791461100		58" \
    "$(python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute("SELECT ts, usd, tokens_without_usd FROM slots WHERE ts > 1791460800"):
    print("\t".join("" if v is None else str(v) for v in r))' "$ADB_E2E")"

harness_summary
