#!/bin/bash
# End-to-end tests for bin/ingest-claude-statusline.sh (the path
# agent-statusline runs) and src/statusline_payload_reader.py behind it: the
# free path that takes the Claude statusline's raw stdin payload and (1)
# stores a claude_statusline reading in account_quotas.db plus a session
# snapshot in sessions_usages.db whenever the transcript gives a precise
# timestamp (the last assistant entry's), deduplicated per session, and (2)
# refreshes the "latest known quota" state file (source "claude_statusline")
# with that same timestamp - and writes nothing at all without one. It runs
# in the foreground: when the script returns, the reading is stored.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

INGEST="$REPO_ROOT/bin/ingest-claude-statusline.sh"
SEP=$'\034'
TH_HOME="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-ingesthome.XXXXXX")"
trap 'rm -rf "$TH_HOME"' EXIT
DATA_DIR="$TH_HOME/opt/agent-usage-tracker/data/claude"
ADB="$DATA_DIR/account_quotas.db"
SDB="$DATA_DIR/sessions_usages.db"
STATE="$TH_HOME/opt/agent-usage-tracker/state/quota/claude"
TRANSCRIPT="$TH_HOME/transcript.jsonl"

# q <db> <sql> - prints result rows tab-separated; a missing db prints 0.
q() {
    [ -f "$1" ] || { echo 0; return; }
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$1" "$2"
}
write_transcript() { printf '%s\n' "$1" > "$TRANSCRIPT"; }
row_count() { q "$ADB" "SELECT COUNT(*) FROM account_quotas"; }
all_raw() { q "$ADB" "SELECT raw FROM account_quotas ORDER BY rowid"; }
last_raw() { q "$ADB" "SELECT raw FROM account_quotas ORDER BY rowid DESC LIMIT 1"; }
snapshots() { q "$SDB" "SELECT COUNT(*) FROM session_snapshots${1:+ WHERE session_id = '$1'}"; }
snapshot_raw() { q "$SDB" "SELECT raw FROM session_snapshots WHERE session_id = '$1' ORDER BY rowid DESC LIMIT 1"; }
reset() { rm -rf "$DATA_DIR" "$STATE"; }

# run_push <transcript> <five_pct> <five_reset> <week_pct> <week_reset>
#          [session_id] [session_cost_usd] [prompt_cache_json] [model_id]
# Builds the statusline payload those values would arrive in and pipes it
# into the ingest script. An empty five_pct means "no rate_limits at all".
run_push() {
    local err_file payload
    payload="$(jq -nc --arg t "$1" --arg f "$2" --arg fr "$3" --arg w "$4" --arg wr "$5" \
        --arg sid "${6:-}" --arg cost "${7:-}" --arg pc "${8:-}" --arg model "${9:-}" '
        def nullable: if . == "" then null else . end;
        {transcript_path: ($t | nullable), session_id: ($sid | nullable),
         model: {id: ($model | nullable)},
         cost: {total_cost_usd: ($cost | if . == "" then null else tonumber end)},
         prompt_cache: ($pc | if . == "" then null else fromjson end)}
        + (if $f == "" then {} else
            {rate_limits: {five_hour: {used_percentage: ($f | tonumber), resets_at: ($fr | nullable)},
                           seven_day: {used_percentage: ($w | tonumber), resets_at: ($wr | nullable)}}} end)')"
    err_file="$(mktemp "${TMPDIR:-/tmp}/th-err.XXXXXX")"
    TH_OUT="$(printf '%s' "$payload" | HOME="$TH_HOME" bash "$INGEST" 2>"$err_file")"
    TH_STATUS=$?
    TH_ERR="$(cat "$err_file")"
    rm -f "$err_file"
}

section "no rate_limits in the payload -> true no-op, nothing written anywhere"
reset
run_push "" "" "" "" ""
assert_status "exits 0" 0 "$TH_STATUS"
assert_file_missing "nothing logged" "$ADB"
assert_file_missing "state file untouched" "$STATE"

section "no transcript_path -> nothing written anywhere (no date beats a wrong date)"
reset
run_push "" 42 "2026-01-01T00:00:00Z" 55 "2026-01-05T00:00:00Z"
assert_status "exits 0" 0 "$TH_STATUS"
assert_file_missing "no history row" "$ADB"
assert_file_missing "no state file either - never dated 'now'" "$STATE"

section "transcript path doesn't exist -> nothing written"
reset
run_push "$TH_HOME/nope.jsonl" 42 "" 55 ""
assert_status "exits 0" 0 "$TH_STATUS"
assert_file_missing "no history row" "$ADB"
assert_file_missing "no state file" "$STATE"

section "transcript has timestamps but no assistant entry -> nothing written"
reset
write_transcript "$(cat <<'EOF2'
{"type":"user","timestamp":"2026-01-01T10:00:00.000Z"}
{"type":"attachment","timestamp":"2026-01-01T10:00:01.000Z"}
{"type":"summary","leafUuid":"x"}
EOF2
)"
run_push "$TRANSCRIPT" 42 "" 55 ""
assert_status "exits 0" 0 "$TH_STATUS"
assert_file_missing "no history row" "$ADB"
assert_file_missing "no state file" "$STATE"

section "first genuine reading -> dated by the last assistant entry, not by later lines"
reset
write_transcript "$(cat <<'EOF2'
{"type":"user","timestamp":"2026-01-01T10:00:00.000Z"}
{"type":"assistant","timestamp":"2026-01-01T10:00:05.500Z"}
{"type":"attachment","timestamp":"2026-01-01T10:39:00.000Z"}
{"type":"user","timestamp":"2026-01-01T10:40:00.000Z","message":{"content":"quoting {\"type\":\"assistant\",\"timestamp\":\"2026-01-01T11:00:00.000Z\"}"}}
{"type":"last-prompt"}
{"type":"ai-title"}
{"type":"summary","leafUuid":"x"}
EOF2
)"
run_push "$TRANSCRIPT" 42 "2026-01-01T15:00:00Z" 55 "2026-01-08T00:00:00Z"
assert_status "exits 0" 0 "$TH_STATUS"
assert_eq "exactly one row logged" "1" "$(row_count)"
row="$(all_raw)"
assert_contains "tagged claude_statusline" "$row" '"source":"claude_statusline"'
assert_contains "carries the five-hour percent" "$row" '"five_hour_pct":42'
assert_contains "carries the seven-day percent" "$row" '"seven_day_pct":55'
assert_contains "carries the five-hour reset" "$row" '"five_hour_resets_at":"2026-01-01T15:00:00Z"'
assert_contains "observed_at is the last assistant entry's timestamp" \
    "$row" '"observed_at":1767261605'
assert_file_exists "state file written" "$STATE"
IFS="$SEP" read -r st_five st_five_reset st_week st_week_reset st_source st_observed < "$STATE"
assert_eq "state 5h percent" "42" "$st_five"
assert_eq "state 5h reset, as epoch" "1767279600" "$st_five_reset"
assert_eq "state 7d percent" "55" "$st_week"
assert_eq "state 7d reset, as epoch" "1767830400" "$st_week_reset"
assert_eq "state source is 'claude_statusline'" "claude_statusline" "$st_source"
assert_eq "state observed_at matches the row's" "1767261605" "$st_observed"
assert_eq "ts column = observed_at, dt its UTC text" "1767261605	2026-01-01T10:00:05Z" "$(q "$ADB" "SELECT ts, dt FROM account_quotas")"
assert_eq "typed columns" "42.0	1767279600	55.0	1767830400" \
    "$(q "$ADB" "SELECT five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts FROM account_quotas")"
assert_eq "latest view serves it" "claude_statusline	42.0" "$(q "$ADB" "SELECT source, five_hour_pct FROM latest")"

section "a long bookkeeping tail still finds the assistant entry (byte window, not 20 lines)"
reset
{
    printf '%s\n' '{"type":"assistant","timestamp":"2026-01-01T10:00:05.000Z"}'
    for i in $(seq 1 200); do printf '%s\n' '{"type":"mode","mode":"default"}'; done
} > "$TRANSCRIPT"
run_push "$TRANSCRIPT" 42 "" 55 ""
assert_contains "dated past 200 bookkeeping lines" "$(all_raw)" '"observed_at":1767261605'

section "the window's cut first line is skipped, not fatal"
reset
{
    printf '{"type":"assistant","timestamp":"2026-01-01T09:00:00.000Z","pad":"%s"}\n' "$(head -c 300000 /dev/zero | tr '\0' x)"
    printf '%s\n' '{"type":"assistant","timestamp":"2026-01-01T10:00:05.000Z"}'
} > "$TRANSCRIPT"
run_push "$TRANSCRIPT" 42 "" 55 ""
assert_contains "the complete assistant line after the cut one dates it" "$(all_raw)" '"observed_at":1767261605'

section "re-renders of the same reading append nothing (per-session dedup)"
reset
write_transcript '{"type":"assistant","timestamp":"2026-01-01T10:00:05.000Z"}'
run_push "$TRANSCRIPT" 42 "" 55 "" "sess-1" 0.5 "" "claude-opus-5-5"
run_push "$TRANSCRIPT" 42 "" 55 "" "sess-1" 0.5 "" "claude-opus-5-5"
run_push "$TRANSCRIPT" 42 "" 55 "" "sess-1" 0.5 "" "claude-opus-5-5"
assert_status "exits 0" 0 "$TH_STATUS"
assert_eq "one account row for three renders" "1" "$(row_count)"
assert_eq "one session snapshot for three renders" "1" "$(snapshots sess-1)"
assert_file_missing "no dedup key files any more: the database is the memory" "$TH_HOME/opt/agent-usage-tracker/state/ingest"

section "another session seeing the same reading is its own observation"
run_push "$TRANSCRIPT" 42 "" 55 "" "sess-2" 0.1 "" "claude-opus-5-5"
assert_eq "second account row, observed by sess-2" "2" "$(row_count)"
assert_eq "observed_by_session" "sess-2" "$(last_raw | jq -r .observed_by_session)"

section "a new API response with unchanged percents -> one new account row"
printf '%s\n' '{"type":"assistant","timestamp":"2026-01-01T10:05:00.000Z"}' >> "$TRANSCRIPT"
run_push "$TRANSCRIPT" 42 "" 55 "" "sess-1" 0.5 "" "claude-opus-5-5"
assert_eq "three account rows" "3" "$(row_count)"
assert_eq "session snapshot unchanged (cost, model, cache didn't move)" "1" "$(snapshots sess-1)"
IFS="$SEP" read -r _ _ _ _ _ st_observed < "$STATE"
assert_eq "state file's observed_at advances" "1767261900" "$st_observed"

section "a percent change at the same observed_at -> new account row"
run_push "$TRANSCRIPT" 43 "" 55 "" "sess-1" 0.5 "" "claude-opus-5-5"
assert_eq "four account rows" "4" "$(row_count)"

section "a cost change -> new session row"
run_push "$TRANSCRIPT" 43 "" 55 "" "sess-1" 0.7 "" "claude-opus-5-5"
assert_eq "four account rows still" "4" "$(row_count)"
assert_eq "two session snapshots" "2" "$(snapshots sess-1)"

section "an idle session's frozen reading never beats a fresher one"
reset
FRESH="$TH_HOME/fresh.jsonl"
printf '%s\n' '{"type":"assistant","timestamp":"2026-01-02T10:00:00.000Z"}' > "$FRESH"
{
    printf '%s\n' '{"type":"assistant","timestamp":"2026-01-01T10:00:00.000Z"}'
    printf '%s\n' '{"type":"attachment","timestamp":"2026-01-01T12:00:00.000Z"}'
    for i in $(seq 1 30); do printf '%s\n' '{"type":"atis-latch"}'; done
} > "$TRANSCRIPT"
run_push "$FRESH" 16 "" 18 "" "fresh-session"
run_push "$TRANSCRIPT" 0 "" 11 "" "idle-session"
IFS="$SEP" read -r st_five _ st_week _ st_source st_observed < "$STATE"
assert_eq "state keeps the fresh 5h percent" "16" "$st_five"
assert_eq "state keeps the fresh 7d percent" "18" "$st_week"
assert_eq "state keeps the fresh observed_at" "1767348000" "$st_observed"
run_push "$TRANSCRIPT" 0 "" 11 "" "idle-session"
assert_eq "idle re-render: no new account row" "2" "$(row_count)"

section "empty resets_at fields become JSON null, not empty strings"
reset
write_transcript '{"type":"assistant","timestamp":"2026-02-01T00:00:00.000Z"}'
run_push "$TRANSCRIPT" 10 "" 20 ""
row="$(all_raw)"
assert_contains "five_hour_resets_at is null" "$row" '"five_hour_resets_at":null'
assert_contains "seven_day_resets_at is null" "$row" '"seven_day_resets_at":null'

section "observed_at is exact UTC even when the local zone is in daylight-saving time"
# Regression test: jq 1.6's fromdateiso8601 on macOS returned an epoch one
# hour too late for a summer timestamp under a DST zone such as Europe/Paris.
reset
write_transcript '{"type":"assistant","timestamp":"2026-07-01T10:00:00.250Z"}'
TZ=Europe/Paris run_push "$TRANSCRIPT" 10 "" 20 ""
assert_contains "summer timestamp under Europe/Paris" "$(all_raw)" '"observed_at":1782900000'
reset
write_transcript '{"type":"assistant","timestamp":"2026-07-01T10:00:00+00:00"}'
TZ=America/New_York run_push "$TRANSCRIPT" 10 "" 20 ""
assert_contains "+00:00 suffix under America/New_York" "$(all_raw)" '"observed_at":1782900000'

section "float percents -> logged unrounded, state file gets them rounded"
reset
write_transcript '{"type":"assistant","timestamp":"2026-03-01T00:00:00.000Z"}'
run_push "$TRANSCRIPT" 23.5 "" 41.2 ""
assert_status "exits 0" 0 "$TH_STATUS"
row="$(all_raw)"
assert_contains "five-hour percent keeps its fraction" "$row" '"five_hour_pct":23.5'
assert_contains "seven-day percent keeps its fraction" "$row" '"seven_day_pct":41.2'
IFS="$SEP" read -r st_five _ st_week _ _ _ < "$STATE"
assert_eq "state 5h percent rounded" "24" "$st_five"
assert_eq "state 7d percent rounded" "41" "$st_week"

section "session fields -> sessions database only; account row gets observed_by_session, never cost"
reset
write_transcript '{"type":"assistant","timestamp":"2026-03-01T00:00:00.000Z"}'
run_push "$TRANSCRIPT" 10 "" 20 "" "sess-1" 0.01234 "" "claude-opus-5-5"
row="$(all_raw)"
assert_eq "account row names the observing session" "sess-1" "$(printf '%s' "$row" | jq -r .observed_by_session)"
assert_not_contains "account row carries no cost" "$row" "session_cost_usd"
assert_not_contains "account row has no bare session_id" "$row" '"session_id"'
srow="$(snapshot_raw sess-1)"
assert_eq "one session snapshot" "1" "$(snapshots sess-1)"
assert_eq "cumulative session cost in the snapshot" "0.01234" "$(printf '%s' "$srow" | jq -c .session_cost_usd)"
assert_eq "model id in the snapshot" "claude-opus-5-5" "$(printf '%s' "$srow" | jq -r .model_id)"
assert_eq "same observed_at in both scopes (the join key)" \
    "$(printf '%s' "$row" | jq .observed_at)" "$(printf '%s' "$srow" | jq .observed_at)"
assert_eq "session row has no percent under any name" "" \
    "$(printf '%s' "$srow" | jq -r '[paths | map(tostring) | join(".") | select(test("pct|percent|utiliz"; "i"))] | join(",")')"
reset
run_push "$TRANSCRIPT" 10 "" 20 ""
assert_contains "no session id -> observed_by_session null" "$(all_raw)" '"observed_by_session":null'
assert_eq "no session id -> no snapshot" "0" "$(snapshots)"

section "a session id that isn't a plain token is never stored as one"
reset
run_push "$TRANSCRIPT" 10 "" 20 "" "../escape" 0.5
assert_eq "account row still written" "1" "$(row_count)"
assert_eq "no snapshot" "0" "$(snapshots)"
assert_eq "no file named after it anywhere" "0" "$(find "$TH_HOME" -name '*escape*' | wc -l | tr -d ' ')"
run_push "$TRANSCRIPT" 10 "" 20 "" "account" 0.5
assert_eq "the reserved name 'account' is refused too" "0" "$(snapshots)"
assert_not_contains "the account database never holds a cost" "$(all_raw)" "session_cost_usd"

section "prompt_cache -> the snapshot, as the raw object, null when absent"
reset
run_push "$TRANSCRIPT" 10 "" 20 "" "sess-1" 0.5 '{"warm":true,"misses":2,"miss_causes":{"system_prompt_changed":2},"hit_ratio":0.83}'
assert_eq "prompt_cache object logged unchanged" '{"warm":true,"misses":2,"miss_causes":{"system_prompt_changed":2},"hit_ratio":0.83}' \
    "$(snapshot_raw sess-1 | jq -c .prompt_cache)"
assert_not_contains "never in the account row" "$(all_raw)" "prompt_cache"
reset
run_push "$TRANSCRIPT" 10 "" 20 "" "sess-1" 0.5 ""
assert_contains "absent -> null" "$(snapshot_raw sess-1)" '"prompt_cache":null'

section "a payload that is not JSON -> exits 0, writes nothing"
reset
th_run env HOME="$TH_HOME" bash -c "printf 'garbage' | bash '$INGEST'"
assert_status "exits 0" 0 "$TH_STATUS"
assert_eq "prints nothing" "" "$TH_OUT$TH_ERR"
assert_file_missing "no state file" "$STATE"

section "real time: when the script returns, the state file already holds the new reading"
reset
write_transcript '{"type":"assistant","timestamp":"2026-04-01T00:00:00.000Z"}'
payload='{"transcript_path":"'"$TRANSCRIPT"'","session_id":"bg-1","rate_limits":{"five_hour":{"used_percentage":7,"resets_at":1775000000},"seven_day":{"used_percentage":9,"resets_at":1775500000}}}'
th_run env HOME="$TH_HOME" bash -c "printf '%s' '$payload' | bash '$INGEST'"
assert_status "exits 0" 0 "$TH_STATUS"
assert_eq "prints nothing" "" "$TH_OUT$TH_ERR"
IFS="$SEP" read -r st_five st_five_reset _ _ _ _ < "$STATE"
assert_eq "state file refreshed before the script returned" "7 1775000000" "$st_five $st_five_reset"

harness_summary
