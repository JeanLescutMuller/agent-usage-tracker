#!/bin/bash
# Tests for src/quota_polling/poll_codex_plan_history.py: the row it writes,
# its once-a-day / hourly-retry cadence, and that the bearer token never
# reaches the log. Runs against a temp copy of the runtime layout with a
# fake ~/.codex/auth.json; urlopen is monkeypatched - no network.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TH_TMP2="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-planhist.XXXXXX")"
trap 'rm -rf "$TH_TMP2"' EXIT
RT="$TH_TMP2/rt"
mkdir -p "$RT/src" "$TH_TMP2/home/.codex"
cp -R "$REPO_ROOT/src/quota_polling" "$RT/src/"
printf '{"tokens":{"access_token":"SECRET-TOKEN-xyz","account_id":"acct-1"}}' > "$TH_TMP2/home/.codex/auth.json"
LOG="$RT/data/codex/account.jsonl"
STATE="$RT/state/poll/codex_plan_limit_history"

# run_poller <python expression raised or returned by the fake urlopen>
run_poller() {
    HOME="$TH_TMP2/home" python3 -c "
import sys, io, json, http.client, urllib.error, urllib.request
sys.path.insert(0, '$RT/src/quota_polling')
import poll_codex_plan_history as m
d_seen = {}
class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass
def fake(req, timeout):
    d_seen['auth'] = req.get_header('Authorization'); d_seen['acct'] = req.get_header('Chatgpt-account-id')
    $1
urllib.request.urlopen = fake
m.main()
print(json.dumps(d_seen))
"
}
BODY='{"data_as_of":"2026-09-30T00:00:00Z","approximate":true,"periods":[{"window_minutes":300,"used_basis_points":118.790896}]}'

section "first run -> fetches, logs the raw response, records a successful attempt"
out="$(run_poller "return Resp(b'$BODY')")"
assert_contains "sends the bearer token from auth.json" "$out" '"auth": "Bearer SECRET-TOKEN-xyz"'
assert_contains "sends the account id" "$out" '"acct": "acct-1"'
row="$(tail -n 1 "$LOG")"
assert_eq "tagged codex_plan_limit_history" "codex_plan_limit_history" "$(printf '%s' "$row" | jq -r .source)"
assert_eq "raw response kept unchanged, fractional basis points intact" "118.790896" \
    "$(printf '%s' "$row" | jq -r '.plan_limit_history.periods[0].used_basis_points')"
assert_eq "error is null" "null" "$(printf '%s' "$row" | jq -c .error)"
assert_eq "state records a successful attempt" "true" "$(jq -r .ok "$STATE")"

section "second run within 24h -> no request, no row"
out="$(run_poller "raise AssertionError('must not be called')")"
assert_contains "skips" "$out" "skip:"
assert_eq "still one row" "1" "$(wc -l < "$LOG" | tr -d ' ')"

section "24h later -> fetches again"
jq '.ts -= 86401' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
run_poller "return Resp(b'$BODY')" > /dev/null
assert_eq "two rows" "2" "$(wc -l < "$LOG" | tr -d ' ')"

section "HTTP 401 -> error row, retried after an hour, not a day"
jq '.ts -= 86401' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
run_poller "raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, None)" > /dev/null
row="$(tail -n 1 "$LOG")"
assert_eq "error row" '{"stage":"http","type":"HTTPError","status":401,"detail":"Unauthorized"}' "$(printf '%s' "$row" | jq -c .error)"
assert_eq "no response stored" "null" "$(printf '%s' "$row" | jq -c .plan_limit_history)"
assert_eq "state records a failed attempt" "false" "$(jq -r .ok "$STATE")"
jq '.ts -= 3601' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
run_poller "return Resp(b'$BODY')" > /dev/null
assert_eq "retried after an hour" "4" "$(wc -l < "$LOG" | tr -d ' ')"

section "server disconnect -> network error row, no crash"
jq '.ts -= 86401' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
run_poller "raise http.client.RemoteDisconnected('closed')" > /dev/null
assert_eq "network error" "network RemoteDisconnected" "$(tail -n 1 "$LOG" | jq -r '"\(.error.stage) \(.error.type)"')"

section "missing auth file -> auth error row without any file content"
rm "$TH_TMP2/home/.codex/auth.json"
jq '.ts -= 86401' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
run_poller "raise AssertionError('must not be called')" > /dev/null
assert_eq "auth error" '{"stage":"auth","type":"FileNotFoundError"}' "$(tail -n 1 "$LOG" | jq -c .error)"

section "the token never reaches the log"
assert_not_contains "no token in any row" "$(cat "$LOG")" "SECRET-TOKEN-xyz"

harness_summary
