#!/bin/bash
# Tests for src/ingest_account_quota.py, the only writer of
# account_quotas.db: what it stores, what it refuses, its idempotency, the
# `latest` view, and the state/quota/claude file it keeps for
# agent-statusline, auto-apply and jobs/claude-quota.sh.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SRC_DIR="$REPO_ROOT/src"
RT="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-gate.XXXXXX")"
trap 'rm -rf "$RT"' EXIT
ADB="$RT/data/claude/account_quotas.db"
STATE="$RT/state/quota/claude"
SEP=$'\034'

# ingest <agent> [flags] <<< rows - the gate's command line.
ingest() { AGENT_USAGE_TRACKER_RUNTIME="$RT" python3 "$SRC_DIR/ingest_account_quota.py" --agent "$@" 2>/dev/null; }
q() {
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$1" "$2"
}
state_field() { cut -d "$SEP" -f "$1" "$STATE"; }

section "statusline rows: stored with typed columns, raw kept verbatim"
row='{"ts":2000,"iso":"x","source":"claude_statusline","observed_at":1900,"five_hour_pct":42.5,"seven_day_pct":10,"five_hour_resets_at":"1767279600","seven_day_resets_at":null,"observed_by_session":"s1"}'
assert_eq "inserted" '{"inserted": 1, "duplicate": 0, "redundant": 0, "rejected": 0}' "$(ingest claude <<< "$row")"
assert_eq "ts = observed_at, written_ts = the old ts" "1900	2000" "$(q "$ADB" "SELECT ts, written_ts FROM account_quotas")"
assert_eq "raw is the line, byte for byte" "$row" "$(q "$ADB" "SELECT raw FROM account_quotas")"
assert_eq "percents and resets" "42.5	1767279600	10.0	" \
    "$(q "$ADB" "SELECT five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts FROM account_quotas")"

section "the same line again is a duplicate; a re-render of the same reading is redundant"
assert_contains "same line -> nothing stored" "$(ingest claude <<< "$row")" '"inserted": 0'
assert_contains "same line without dedup -> duplicate (row_hash)" "$(ingest claude --no-dedup <<< "$row")" '"duplicate": 1'
rerender="${row/\"ts\":2000/\"ts\":2050}"
assert_contains "same session, same reading, later write -> redundant" "$(ingest claude <<< "$rerender")" '"redundant": 1'
assert_contains "--no-dedup keeps it (history as is)" "$(ingest claude --no-dedup <<< "$rerender")" '"inserted": 1'
other="${rerender/\"s1\"/\"s2\"}"
assert_contains "another session's observation is kept" "$(ingest claude <<< "$other")" '"inserted": 1'

section "per-session fields are refused (scope rule)"
assert_contains "session_cost_usd -> rejected" \
    "$(ingest claude <<< '{"ts":1,"source":"claude_statusline","session_cost_usd":1.5}')" '"rejected": 1'
assert_contains "unknown source -> rejected" "$(ingest claude <<< '{"ts":1,"source":"nope"}')" '"rejected": 1'
assert_contains "non-numeric percent -> rejected" \
    "$(ingest claude <<< '{"ts":1,"source":"claude_statusline","observed_at":1,"five_hour_pct":"high"}')" '"rejected": 1'

section "poll rows: API readings parsed, failed attempts to poll_errors"
api='{"ts":3000,"iso":"x","source":"claude_api","api":{"five_hour":{"utilization":7.0,"resets_at":"2026-01-01T15:00:00.123+00:00"},"seven_day":{"utilization":3.0,"resets_at":null}},"api_headers":{},"error":null}'
err='{"ts":3100,"iso":"x","source":"claude_api","api":null,"api_headers":null,"error":{"stage":"http","type":"HTTPError","status":429,"retry_after_s":1820}}'
assert_contains "two rows" "$(printf '%s\n%s\n' "$api" "$err" | ingest claude)" '"inserted": 2'
assert_eq "reading: ts = poll time, ISO reset -> epoch" "3000	7.0	1767279600	3.0" \
    "$(q "$ADB" "SELECT ts, five_hour_pct, five_hour_resets_ts, seven_day_pct FROM account_quotas WHERE source = 'claude_api'")"
assert_eq "failed attempt with its stage, status and Retry-After" "3100	http	429	1820" \
    "$(q "$ADB" "SELECT ts, stage, status, retry_after_s FROM poll_errors")"

section "latest view: the freshest reading by when it was true"
assert_eq "the API reading at 3000 beats the push readings at 1900" "3000	claude_api" "$(q "$ADB" "SELECT ts, source FROM latest")"
assert_contains "an older reading written later" \
    "$(ingest claude <<< '{"ts":9000,"source":"claude_statusline","observed_at":2500,"five_hour_pct":1,"seven_day_pct":1}')" '"inserted": 1'
assert_eq "...does not become latest" "3000" "$(q "$ADB" "SELECT ts FROM latest")"

section "codex readings: primary/secondary mapped by window length"
CDB="$RT/data/codex/account_quotas.db"
codex='{"ts":5000,"iso":"x","source":"codex_app_server","codex_rate_limits":{"rateLimits":{"primary":{"usedPercent":19,"windowDurationMins":300,"resetsAt":5100},"secondary":{"usedPercent":3,"windowDurationMins":10080,"resetsAt":9000}}},"codex_usage":{},"error":null}'
plan='{"ts":5001,"iso":"x","source":"codex_plan_limit_history","plan_limit_history":{"windows":[]},"error":null}'
assert_contains "both stored" "$(printf '%s\n%s\n' "$codex" "$plan" | ingest codex)" '"inserted": 2'
assert_eq "5h and 7d columns" "19.0	5100	3.0	9000" \
    "$(q "$CDB" "SELECT five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts FROM account_quotas WHERE source = 'codex_app_server'")"
assert_eq "plan history has no current reading, so latest skips it" "codex_app_server" "$(q "$CDB" "SELECT source FROM latest")"
assert_file_missing "codex never touches the Claude state file" "$RT/state/quota/codex"

section "state/quota/claude: written newest-wins, six fields"
rm -f "$STATE"
py() { AGENT_USAGE_TRACKER_RUNTIME="$RT" python3 -c "import sys; sys.path.insert(0, '$SRC_DIR'); import ingest_account_quota as g; $1"; }
col() { printf "{'ts': %s, 'source': '%s', 'five_hour_pct': %s, 'five_hour_resets_ts': %s, 'seven_day_pct': %s, 'seven_day_resets_ts': %s}" "$@"; }
assert_eq "first write writes" "True" "$(py "print(g.write_state_if_newer($(col 500 claude_api 42.4 1700000000 55.5 1700100000)))")"
assert_eq "fields: rounded percents, epoch resets, source, observed_at" "42${SEP}1700000000${SEP}56${SEP}1700100000${SEP}claude_api${SEP}500" "$(cat "$STATE")"
assert_eq "an older reading does not overwrite" "False" "$(py "print(g.write_state_if_newer($(col 400 claude_statusline 1 None 2 None)))")"
assert_eq "...source unchanged" "claude_api" "$(state_field 5)"
assert_eq "a newer one does" "True" "$(py "print(g.write_state_if_newer($(col 600 claude_statusline 1 None 2 None)))")"
assert_eq "...empty resets become empty fields, not 'None'" "1${SEP}${SEP}2${SEP}${SEP}claude_statusline${SEP}600" "$(cat "$STATE")"
assert_eq "half rounds away from zero, like the old jq writer" "3" "$(py "print(g._round(2.5))")"

section "the command line is idempotent"
before="$(q "$ADB" "SELECT COUNT(*) FROM account_quotas")"
printf '%s\n%s\n%s\n' "$row" "$api" "$err" | ingest claude >/dev/null
assert_eq "re-sending stored rows adds nothing" "$before" "$(q "$ADB" "SELECT COUNT(*) FROM account_quotas")"
assert_eq "...nor failed attempts" "1" "$(q "$ADB" "SELECT COUNT(*) FROM poll_errors")"

harness_summary
