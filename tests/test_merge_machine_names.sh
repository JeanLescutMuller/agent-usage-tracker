#!/bin/bash
# Tests for adhoc_quotas_analysis/merge_machine_names.py, the one-time fix of
# the Mac's drifted name: an old central folder is merged into the new one
# without losing a row, requests are relabelled everywhere, the old folder is
# archived, and a re-run changes nothing.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-names.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
MERGE="$REPO_ROOT/adhoc_quotas_analysis/merge_machine_names.py"
q() {
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$1" "$2"
}
# Rows through the gates, into <runtime> (data/ when $2 is empty, else central/$2/).
fill() {
    AGENT_USAGE_TRACKER_RUNTIME="$1" python3 - "$2" "$3" "$4" "$5" <<PY
import sys; sys.path.insert(0, "$REPO_ROOT/src")
import ingest_account_quota as a, ingest_session_usage as s, usage_db
_, machine, req, ts, pct = sys.argv
db = usage_db.open_account("claude")
a.add_row(db, "claude", {"ts": int(ts), "source": "claude_api", "api": {"five_hour": {"utilization": float(pct)}}}, update_state=False)
db.commit()
db = usage_db.open_sessions("claude")
s.add_requests(db, [dict(ts=int(ts), dt="x", request_id=req, machine=machine, session_id="s1", folder="/d", entrypoint="cli",
                         model="m", input_tokens=1, output_tokens=1, cache_read_tokens=0, cache_write_5m_tokens=0,
                         cache_write_1h_tokens=0, usd=None)])
s.add_scan(db, int(ts), int(ts), 1)
db.commit()
PY
}
RT="$TMP/rt"
fill "$RT/central/old-name" old-name req_1 100 5
fill "$RT/central/old-name" Old-Name req_2 110 6
fill "$RT/central/new-name" new-name req_3 200 7
fill "$RT/central/new-name" new-name req_2 110 6      # a row both folders hold
fill "$RT" old-name req_9 300 8                       # this machine's own data/

section "the old folder merges into the new one, nothing lost"
th_run python3 "$MERGE" --runtime "$RT" --to new-name --from old-name Old-Name
assert_status "exits 0" 0 "$TH_STATUS"
NEW="$RT/central/new-name/data/claude"
assert_eq "account readings: 3 distinct" "3" "$(q "$NEW/account_quotas.db" "SELECT COUNT(*) FROM account_quotas")"
assert_eq "requests: 3 distinct, one name" "req_1 new-name|req_2 new-name|req_3 new-name" \
    "$(q "$NEW/sessions_usages.db" "SELECT request_id || ' ' || machine FROM requests ORDER BY request_id" | paste -sd'|' -)"
assert_eq "scans: added (no key)" "4" "$(q "$NEW/sessions_usages.db" "SELECT COUNT(*) FROM scans")"
assert_file_missing "old folder gone from central/" "$RT/central/old-name"
assert_eq "old folder archived" "1" "$(ls -d "$RT"/central/_archive/old-name-merged-* | wc -l | tr -d ' ')"
assert_eq "a backup tar was made" "1" "$(ls "$RT"/central/_archive/pre-merge-machine-names-*.tar.gz | wc -l | tr -d ' ')"
assert_eq "this machine's data/ relabelled too" "new-name" "$(q "$RT/data/claude/sessions_usages.db" "SELECT machine FROM requests")"

section "a re-run changes nothing"
th_run python3 "$MERGE" --runtime "$RT" --to new-name --from old-name Old-Name
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "says so" "$TH_OUT" "nothing to do"
assert_eq "still one backup" "1" "$(ls "$RT"/central/_archive/pre-merge-machine-names-*.tar.gz | wc -l | tr -d ' ')"

harness_summary
