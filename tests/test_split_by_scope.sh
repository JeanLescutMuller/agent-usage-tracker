#!/bin/bash
# Tests for adhoc_quotas_analysis/split_by_scope.py - the one-time migration
# from per-provider logs to data/<agent>/account.jsonl +
# data/<agent>/<session-id>.jsonl. Fixture rows cover every historical shape.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SPLIT="$REPO_ROOT/adhoc_quotas_analysis/split_by_scope.py"
TH_TMP2="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-split.XXXXXX")"
trap 'rm -rf "$TH_TMP2"' EXIT
D="$TH_TMP2/data"
SID="8b301a30-3609-4e04-8a39-fb19a3b8be54"

seed() {
    rm -rf "$D"; mkdir -p "$D"
    cat > "$D/claude-quota-history.jsonl" <<ROWS
{"ts": 100, "token_deltas": {"claude-opus-5": 10}, "api": {"five_hour": {"utilization": 3}}}
{"ts": 200, "iso": "x", "source": "claude", "api": {"five_hour": {"utilization": 4}}, "api_headers": {}, "error": null}
{"ts":300,"iso":"x","source":"claude_statusline","observed_at":299,"five_hour_pct":5,"seven_day_pct":1,"five_hour_resets_at":"1","seven_day_resets_at":"2"}
{"ts":400,"iso":"x","source":"claude_statusline","observed_at":399,"five_hour_pct":6,"seven_day_pct":1,"five_hour_resets_at":"1","seven_day_resets_at":"2","session_id":"$SID","session_cost_usd":1.5,"prompt_cache":{"misses":1,"hit_ratio":0.9}}
{"ts":500,"iso":"x","source":"claude_statusline","observed_at":499,"five_hour_pct":7,"seven_day_pct":1,"five_hour_resets_at":"1","seven_day_resets_at":"2","session_id":null,"session_cost_usd":null}
{"ts":600,"iso":"x","source":"claude_statu
ROWS
    cat > "$D/codex-quota-history.jsonl" <<'ROWS'
{"ts": 100, "source": "codex", "codex_rate_limits": {"rateLimits": {"primary": {"usedPercent": 2}}}, "codex_usage": null, "error": null}
{"ts": 200, "source": "codex_plan_limit_history", "plan_limit_history": {"periods": [{"used_basis_points": 118.79}]}, "error": null}
ROWS
    cat > "$D/claude-telemetry.jsonl" <<ROWS
{"received_at": 700, "event": "api_request", "time_unix_nano": 1790773229123000000, "attributes": {"session.id": "$SID", "cost_usd": 0.01}, "resource": {}}
{"received_at": 800, "event": "api_request", "time_unix_nano": 1790773230000000000, "attributes": {"cost_usd": 0.02}, "resource": {}}
ROWS
}

section "first run: every row lands in its scope, counts reconcile"
seed
cp "$D/claude-quota-history.jsonl" "$TH_TMP2/claude.orig"; cp "$D/codex-quota-history.jsonl" "$TH_TMP2/codex.orig"
out="$(python3 "$SPLIT" --data-dir "$D" 2>&1)"; status=$?
assert_status "exits 0" 0 "$status"
assert_contains "claude log reconciled" "$out" "claude-quota-history.jsonl: input=6, account=5, session=1, unparsed=1, with_session_fields=2 -> reconciled"
assert_contains "codex log reconciled" "$out" "codex-quota-history.jsonl: input=2, account=2 -> reconciled"
assert_contains "telemetry reconciled" "$out" "claude-telemetry.jsonl: input=2, account=0, session=1, unattributed=1 -> reconciled"
assert_eq "old files moved aside, not deleted" "3" "$(ls "$D/_archive" | grep -c '\.jsonl\.')"
assert_file_missing "old claude log gone from data/" "$D/claude-quota-history.jsonl"

section "account rows: untouched shapes byte-identical, push rows lose their session fields"
head -n 3 "$TH_TMP2/claude.orig" > "$TH_TMP2/expected_head"
assert_eq "legacy, poller and pre-session push rows byte-identical" "$(cat "$TH_TMP2/expected_head")" "$(head -n 3 "$D/claude/account.jsonl")"
row4="$(sed -n 4p "$D/claude/account.jsonl")"
assert_eq "session_id renamed observed_by_session" "$SID" "$(printf '%s' "$row4" | jq -r .observed_by_session)"
assert_eq "no session fields left on the account row" "" "$(printf '%s' "$row4" | jq -r 'keys - ["ts","iso","source","observed_at","five_hour_pct","seven_day_pct","five_hour_resets_at","seven_day_resets_at","observed_by_session"] | join(",")')"
assert_eq "a null session_id stays null under the new name" "null" "$(sed -n 5p "$D/claude/account.jsonl" | jq -c .observed_by_session)"
assert_eq "codex account file byte-identical to the original" "$(cat "$TH_TMP2/codex.orig")" "$(cat "$D/codex/account.jsonl")"
assert_eq "the truncated line is kept verbatim, not dropped" '{"ts":600,"iso":"x","source":"claude_statu' "$(cat "$D/_archive/unparsed-claude-quota-history.jsonl")"

section "session file: push row + telemetry row, never a percent"
assert_eq "one session file" "1" "$(ls "$D/claude" | grep -vc '^account.jsonl$')"
assert_eq "two rows (one push, one telemetry)" "2" "$(wc -l < "$D/claude/$SID.jsonl" | tr -d ' ')"
assert_eq "push session row" '{"ts":400,"iso":"x","source":"claude_statusline","observed_at":399,"session_cost_usd":1.5,"prompt_cache":{"misses":1,"hit_ratio":0.9}}' "$(head -n 1 "$D/claude/$SID.jsonl")"
assert_eq "telemetry row tagged and given observed_at" "claude_otel 1790773229" "$(tail -n 1 "$D/claude/$SID.jsonl" | jq -r '"\(.source) \(.observed_at)"')"
assert_eq "no percent under any name" "" "$(jq -r '[paths | map(tostring) | join(".") | select(test("pct|percent|utiliz|basis_points"; "i"))] | join(",")' "$D/claude/$SID.jsonl" | tr -d '\n')"
assert_eq "telemetry without a session id is unattributed" "1" "$(wc -l < "$D/_unattributed/claude-otel.jsonl" | tr -d ' ')"
assert_file_missing "no session files for codex" "$D/codex/$SID.jsonl"

section "second run is a no-op"
before="$(find "$D" -type f -exec md5 -q {} + | sort | md5 -q)"
out="$(python3 "$SPLIT" --data-dir "$D" 2>&1)"
assert_contains "reports nothing to do" "$out" "nothing to migrate"
assert_eq "no file changed" "$before" "$(find "$D" -type f -exec md5 -q {} + | sort | md5 -q)"

section "an old file recreated by a not-yet-updated writer is swept on re-run"
printf '{"ts":900,"source":"claude","api":null,"error":null}\n' > "$D/claude-quota-history.jsonl"
out="$(python3 "$SPLIT" --data-dir "$D" 2>&1)"
assert_contains "one more row" "$out" "claude-quota-history.jsonl: input=1, account=1 -> reconciled"
assert_eq "appended to the account file" "6" "$(wc -l < "$D/claude/account.jsonl" | tr -d ' ')"

section "a leftover in-progress marker blocks the run"
touch "$D/_archive/.in-progress"; printf '{}\n' > "$D/codex-quota-history.jsonl"
python3 "$SPLIT" --data-dir "$D" >/dev/null 2>&1; status=$?
assert_status "refuses" 1 "$status"
assert_file_exists "and leaves the old file in place" "$D/codex-quota-history.jsonl"

harness_summary
