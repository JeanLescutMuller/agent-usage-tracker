#!/bin/bash
# Tests for src/transcript_reader.py: one row per API request from fixture
# transcripts - the largest-output line wins, a request is stored only once
# 15 min old, a resumed copy is not counted twice, and a run with nothing
# new reads nothing. The clock is faked (NOW); no real transcript is read.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-transcripts.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
RT="$TMP/rt"
PROJ="$TMP/projects"
SDB="$RT/data/claude/sessions_usages.db"
mkdir -p "$PROJ/-Users-x-dev-app" "$PROJ/-Users-x-opt-job"

q() {
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$SDB" "$1"
}
# read_at <epoch> - one transcript_reader run with the clock at <epoch>.
read_at() {
    AGENT_USAGE_TRACKER_RUNTIME="$RT" AGENT_USAGE_TRACKER_TRANSCRIPTS="$PROJ" python3 -c "
import sys, time
sys.path.insert(0, '$REPO_ROOT/src')
time.time = lambda: $1
import transcript_reader
transcript_reader.main()
"
}
# line <iso time> <request id> <output tokens> [session] [cwd] [entrypoint]
line() {
    printf '{"type":"assistant","timestamp":"%s","requestId":"%s","sessionId":"%s","cwd":"%s","entrypoint":"%s","message":{"id":"msg_%s","model":"claude-opus-5","usage":{"input_tokens":2,"output_tokens":%s,"cache_read_input_tokens":1000000,"cache_creation_input_tokens":300,"cache_creation":{"ephemeral_5m_input_tokens":100,"ephemeral_1h_input_tokens":200}}}}\n' \
        "$1" "$2" "${4:-s1}" "${5:-/Users/x/dev/app}" "${6:-cli}" "$2" "$3"
}
T0=1791460800  # 2026-10-08T12:00:00Z
APP="$PROJ/-Users-x-dev-app/s1.jsonl"
{
    line 2026-10-08T12:00:00.000Z req_a 10
    printf '%s\n' '{"type":"user","timestamp":"2026-10-08T12:00:01.000Z","message":{"content":"hi"}}'
    line 2026-10-08T12:00:02.000Z req_a 500
    line 2026-10-08T12:00:03.000Z req_a 40
    line 2026-10-08T12:10:00.000Z req_b 7
} > "$APP"

section "first run: only requests at least 15 min old are stored"
read_at $((T0 + 900 + 60)) >/dev/null
assert_eq "req_a stored (started 16 min ago), req_b not yet (6 min)" "req_a" "$(q "SELECT request_id FROM requests")"
assert_eq "the largest output wins, not the first or last line" "500" "$(q "SELECT output_tokens FROM requests")"
assert_eq "ts = first line, folder, entrypoint, session" "$T0	2026-10-08T12:00:00Z	/Users/x/dev/app	cli	s1" \
    "$(q "SELECT ts, dt, folder, entrypoint, session_id FROM requests")"
assert_eq "cache writes split 5m / 1h" "100	200" "$(q "SELECT cache_write_5m_tokens, cache_write_1h_tokens FROM requests")"
# opus-5: 2x5 + 500x25 + 1e6x5x0.1 + 100x6.25 + 200x10 = 515135 / 1e6
assert_eq "list-price USD" "0.515135" "$(q "SELECT ROUND(usd, 6) FROM requests")"
assert_eq "the scan says everything before now - 15 min is in" "$((T0 + 60))" "$(q "SELECT complete_through_ts FROM scans")"

section "a later run picks up the request that was too young"
read_at $((T0 + 1600)) >/dev/null
assert_eq "req_b stored now" "req_a req_b" "$(q "SELECT request_id FROM requests ORDER BY ts" | paste -sd ' ' -)"

section "nothing new -> nothing read"
assert_contains "no file read" "$(read_at $((T0 + 1700)))" "files read 0"

section "a resumed session copying old requests does not count them twice"
JOB="$PROJ/-Users-x-opt-job/s2.jsonl"
{
    line 2026-10-08T12:00:00.000Z req_a 500 s2 /Users/x/opt/job sdk-py
    line 2026-10-08T12:20:00.000Z req_c 3 s2 /Users/x/opt/job sdk-py
} > "$JOB"
read_at $((T0 + 3000)) >/dev/null
assert_eq "req_a kept once, req_c added" "3" "$(q "SELECT COUNT(*) FROM requests")"
assert_eq "req_c's folder and entrypoint" "/Users/x/opt/job	sdk-py" "$(q "SELECT folder, entrypoint FROM requests WHERE request_id = 'req_c'")"

section "an appended line is read from where the last run stopped"
line 2026-10-08T12:30:00.000Z req_d 9 >> "$APP"
assert_contains "one file read" "$(read_at $((T0 + 3600)))" "files read 1"
assert_eq "req_d added" "4" "$(q "SELECT COUNT(*) FROM requests")"

section "a half-written last line waits for the next run"
printf '%s' "$(line 2026-10-08T12:31:00.000Z req_e 1)" | head -c 60 >> "$APP"
read_at $((T0 + 4000)) >/dev/null
assert_eq "nothing added from a partial line" "4" "$(q "SELECT COUNT(*) FROM requests")"

section "an unknown model is stored with usd NULL"
line 2026-10-08T12:40:00.000Z req_f 1 > "$PROJ/-Users-x-dev-app/s3.jsonl"
sed -i '' 's/claude-opus-5/claude-future-9/' "$PROJ/-Users-x-dev-app/s3.jsonl"
read_at $((T0 + 5000)) >/dev/null
assert_eq "usd NULL" "claude-future-9	" "$(q "SELECT model, usd FROM requests WHERE request_id = 'req_f'")"

harness_summary
