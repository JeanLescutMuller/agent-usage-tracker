#!/bin/bash
# Tests for src/push_to_central.py and src/receive_from_machine.py: new rows
# reach central/<machine>/ through the gates, a second run sends nothing, and
# a failed connection leaves the watermark where it was. Runs the central
# side locally (central = this host); a fake `ssh` stands in for a dead link.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SRC_DIR="$REPO_ROOT/src"
RT="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-push.XXXXXX")"
trap 'rm -rf "$RT"' EXIT
HOST="$(python3 -c "import sys; sys.path.insert(0, \"$SRC_DIR\"); import usage_db; print(usage_db.machine_name())")"
CENTRAL="$RT/central/$HOST/data"
export AGENT_USAGE_TRACKER_RUNTIME="$RT"

q() {
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$1" "$2"
}
push() { python3 "$SRC_DIR/push_to_central.py" --central "${1:-$HOST}"; }

# A few rows of every kind on "this machine", through the gates.
printf '%s\n%s\n%s\n' \
    '{"ts":100,"iso":"x","source":"claude_statusline","observed_at":90,"five_hour_pct":6,"seven_day_pct":1,"five_hour_resets_at":null,"seven_day_resets_at":null,"observed_by_session":"s1"}' \
    '{"ts":110,"iso":"x","source":"claude_api","api":{"five_hour":{"utilization":7.0,"resets_at":null}},"api_headers":{},"error":null}' \
    '{"ts":120,"iso":"x","source":"claude_api","api":null,"api_headers":null,"error":{"stage":"http","status":429}}' \
    | python3 "$SRC_DIR/ingest_account_quota.py" --agent claude >/dev/null
printf '%s\n' '{"ts":130,"iso":"x","source":"claude_statusline","observed_at":90,"model_id":"m","session_cost_usd":0.1,"prompt_cache":null}' \
    | python3 "$SRC_DIR/ingest_session_usage.py" --agent claude --session-id s1 >/dev/null
printf '%s\n' '{"source":"claude_otel","observed_at":131,"received_at":132,"event":"api_request","time_unix_nano":1,"attributes":{"session.id":"s1","request_id":"req_t","cost_usd":0.2},"resource":{}}' \
    | python3 "$SRC_DIR/ingest_session_usage.py" --agent claude >/dev/null
python3 -c "
import sys; sys.path.insert(0, '$SRC_DIR')
import usage_db, ingest_session_usage as g
db = usage_db.open_sessions('claude')
g.add_requests(db, [dict(ts=140, dt='x', request_id='req_1', machine='$HOST', session_id='s1', folder='/dev/a', entrypoint='cli', model='claude-opus-5',
    input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_write_5m_tokens=4, cache_write_1h_tokens=5, usd=0.5)])
g.add_scan(db, 150, 100, 1)"

section "first push: every row reaches central/<machine>/, through the gates"
th_run push
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "reports the rows" "$TH_OUT" "pushed 7 rows from $HOST to $HOST"
assert_eq "readings, raw identical" "$(q "$RT/data/claude/account_quotas.db" "SELECT raw FROM account_quotas ORDER BY rowid")" \
    "$(q "$CENTRAL/claude/account_quotas.db" "SELECT raw FROM account_quotas ORDER BY rowid")"
assert_eq "failed attempt" "1" "$(q "$CENTRAL/claude/account_quotas.db" "SELECT COUNT(*) FROM poll_errors")"
assert_eq "snapshot with its session" "s1	0.1" "$(q "$CENTRAL/claude/sessions_usages.db" "SELECT session_id, session_cost_usd FROM session_snapshots")"
assert_eq "telemetry" "req_t" "$(q "$CENTRAL/claude/sessions_usages.db" "SELECT request_id FROM telemetry")"
assert_eq "request with its folder and machine" "req_1	/dev/a	$HOST	0.5" \
    "$(q "$CENTRAL/claude/sessions_usages.db" "SELECT request_id, folder, machine, usd FROM requests")"
assert_eq "scan" "150	100" "$(q "$CENTRAL/claude/sessions_usages.db" "SELECT ts, complete_through_ts FROM scans")"
assert_eq "the central views work on the copy" "/dev/a	2" "$(q "$CENTRAL/claude/sessions_usages.db" "SELECT folder, requests FROM usage_5m")"
assert_file_missing "the central copy never writes a state file" "$RT/central/$HOST/state/quota/claude"

section "second push: nothing new, nothing sent"
assert_contains "zero rows" "$(push)" "pushed 0 rows"

section "a new row: only it is sent"
printf '%s\n' '{"ts":200,"iso":"x","source":"claude_api","api":{"five_hour":{"utilization":9.0,"resets_at":null}},"api_headers":{},"error":null}' \
    | python3 "$SRC_DIR/ingest_account_quota.py" --agent claude >/dev/null
assert_contains "one row" "$(push)" "pushed 1 rows"
assert_eq "three readings centrally" "3" "$(q "$CENTRAL/claude/account_quotas.db" "SELECT COUNT(*) FROM account_quotas")"

section "a dead link: error, watermark unchanged, sent on the next run"
printf '%s\n' '{"ts":300,"iso":"x","source":"claude_api","api":{"five_hour":{"utilization":10.0,"resets_at":null}},"api_headers":{},"error":null}' \
    | python3 "$SRC_DIR/ingest_account_quota.py" --agent claude >/dev/null
mkdir -p "$RT/fakebin"; printf '#!/bin/sh\necho "ssh: connect to host: Network is unreachable" >&2\nexit 255\n' > "$RT/fakebin/ssh"; chmod +x "$RT/fakebin/ssh"
before="$(cat "$RT/state/push/watermarks.json")"
th_run env PATH="$RT/fakebin:$PATH" python3 "$SRC_DIR/push_to_central.py" --central some-other-host
assert_status "exits 1" 1 "$TH_STATUS"
assert_contains "says it will retry" "$TH_ERR" "retried next run"
assert_eq "watermark unchanged" "$before" "$(cat "$RT/state/push/watermarks.json")"
assert_contains "the next working run sends it" "$(push)" "pushed 1 rows"

section "the receiver refuses a machine name that is not a host name"
th_run python3 "$SRC_DIR/receive_from_machine.py" --machine "../escape" < /dev/null
assert_status "exits non-zero" 1 "$TH_STATUS"
assert_file_missing "nothing created outside central/" "$RT/escape"

harness_summary
