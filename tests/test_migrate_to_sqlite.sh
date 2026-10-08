#!/bin/bash
# Tests for adhoc_quotas_analysis/migrate_to_sqlite.py, the one-time move of
# the JSONL files into the databases: every line lands verbatim, re-running
# adds nothing, the originals are archived only after everything verified,
# and a line that cannot be migrated stops it before archiving.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-migrate.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
MIGRATE="$REPO_ROOT/adhoc_quotas_analysis/migrate_to_sqlite.py"
q() {
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$1" "$2"
}
SID="5803ae81-15c2-4ea2-b13e-abc426f756a2"
make_runtime() {
    local rt="$1"
    mkdir -p "$rt/data/claude" "$rt/data/codex" "$rt/data/_unattributed" "$rt/logs"
    cat > "$rt/data/claude/account.jsonl" <<'J'
{"ts":100,"iso":"x","source":"claude_api","api":{"five_hour":{"utilization":5.0,"resets_at":null}},"api_headers":{},"error":null}
{"ts":110,"iso":"x","source":"claude_statusline","observed_at":105,"five_hour_pct":6,"seven_day_pct":1,"five_hour_resets_at":null,"seven_day_resets_at":null,"observed_by_session":"s1"}
{"ts":111,"iso":"x","source":"claude_statusline","observed_at":105,"five_hour_pct":6,"seven_day_pct":1,"five_hour_resets_at":null,"seven_day_resets_at":null,"observed_by_session":"s1"}
{"ts":110,"iso":"x","source":"claude_statusline","observed_at":105,"five_hour_pct":6,"seven_day_pct":1,"five_hour_resets_at":null,"seven_day_resets_at":null,"observed_by_session":"s1"}
J
    printf '%s\n' '{"ts":200,"iso":"x","source":"codex_app_server","codex_rate_limits":{"rateLimits":{"primary":{"usedPercent":3,"windowDurationMins":300,"resetsAt":900}}},"codex_usage":{},"error":null}' > "$rt/data/codex/account.jsonl"
    printf '%s\n' '{"ts":120,"iso":"x","source":"claude_api","api":null,"api_headers":null,"error":{"stage":"http","status":429}}' > "$rt/logs/claude-poll-errors.jsonl"
    printf '%s\n%s\n' '{"ts":130,"iso":"x","source":"claude_statusline","observed_at":105,"model_id":"m","session_cost_usd":0.1,"prompt_cache":null}' \
        '{"source":"claude_otel","observed_at":131,"received_at":132,"event":"api_request","time_unix_nano":1,"attributes":{"session.id":"'"$SID"'","request_id":"req_1","cost_usd":0.2},"resource":{}}' \
        > "$rt/data/claude/$SID.jsonl"
    printf '%s\n' '{"source":"claude_otel","observed_at":140,"received_at":141,"event":"api_error","time_unix_nano":2,"attributes":{},"resource":{}}' > "$rt/data/_unattributed/claude-otel.jsonl"
}

section "every line lands, verbatim; exact duplicate lines collapse; history is not deduplicated"
RT="$TMP/rt"; make_runtime "$RT"
th_run python3 "$MIGRATE" --runtime "$RT"
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "verified" "$TH_OUT" "OK: every distinct line is in a database."
assert_eq "claude: 3 distinct readings kept, the repeated render included" "3" "$(q "$RT/data/claude/account_quotas.db" "SELECT COUNT(*) FROM account_quotas")"
assert_eq "raw is the original line" "$(head -1 "$RT/data/claude/account.jsonl")" "$(q "$RT/data/claude/account_quotas.db" "SELECT raw FROM account_quotas ORDER BY rowid LIMIT 1")"
assert_eq "the failed attempt from the error log" "120	429" "$(q "$RT/data/claude/account_quotas.db" "SELECT ts, status FROM poll_errors")"
assert_eq "codex reading" "200	3.0" "$(q "$RT/data/codex/account_quotas.db" "SELECT ts, five_hour_pct FROM account_quotas")"
assert_eq "the session file's snapshot, under its file name's session" "$SID	0.1" "$(q "$RT/data/claude/sessions_usages.db" "SELECT session_id, session_cost_usd FROM session_snapshots")"
assert_eq "its telemetry event, and the unattributed one" "req_1	$SID
	" "$(q "$RT/data/claude/sessions_usages.db" "SELECT request_id, session_id FROM telemetry ORDER BY ts")"
assert_file_missing "the state file is not touched by history" "$RT/state/quota/claude"
assert_file_exists "nothing archived without --archive" "$RT/data/claude/account.jsonl"

section "re-running adds nothing"
th_run python3 "$MIGRATE" --runtime "$RT"
assert_match "zero inserted in the totals" "$(printf '%s\n' "$TH_OUT" | grep '^totals')" '^totals +9 +8 +0 +9 +0 +0$'

section "--archive moves the data/ files away, keeps the logs where they are"
th_run python3 "$MIGRATE" --runtime "$RT" --archive
assert_status "exits 0" 0 "$TH_STATUS"
assert_file_missing "account.jsonl moved" "$RT/data/claude/account.jsonl"
assert_eq "archived under data/_archive/pre-sqlite-*" "1" "$(find "$RT/data/_archive" -path '*pre-sqlite-*/data/claude/account.jsonl' | wc -l | tr -d ' ')"
assert_file_exists "the error log, copied aside beforehand in the real cutover, stays" "$RT/logs/claude-poll-errors.jsonl"

section "a line that cannot be migrated -> failure, nothing archived"
RT2="$TMP/rt2"; make_runtime "$RT2"
printf '%s\n' '{"ts":1,"source":"mystery"}' >> "$RT2/data/claude/account.jsonl"
th_run python3 "$MIGRATE" --runtime "$RT2" --archive
assert_status "exits 1" 1 "$TH_STATUS"
assert_contains "says why" "$TH_ERR" "nothing archived"
assert_file_exists "originals untouched" "$RT2/data/claude/account.jsonl"

harness_summary
