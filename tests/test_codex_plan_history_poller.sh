#!/bin/bash
# Tests for src/codex_plan_history_poller.py: the row it stores (through the
# account gate), its once-a-day / hourly-retry cadence read back from
# account_quotas.db, and that the bearer token never reaches the database.
# Runs against a temp runtime with a fake ~/.codex/auth.json; urlopen is
# monkeypatched - no network.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TH_TMP2="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-planhist.XXXXXX")"
trap 'rm -rf "$TH_TMP2"' EXIT
RT="$TH_TMP2/rt"
mkdir -p "$TH_TMP2/home/.codex"
printf '{"tokens":{"access_token":"SECRET-TOKEN-xyz","account_id":"acct-1"}}' > "$TH_TMP2/home/.codex/auth.json"
CDB="$RT/data/codex/account_quotas.db"
q() {
    python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$CDB" "$1"
}
readings() { q "SELECT COUNT(*) FROM account_quotas"; }
last_error_raw() { q "SELECT raw FROM poll_errors ORDER BY rowid DESC LIMIT 1"; }
# age <seconds> - moves the poller's clock that far forward (SHIFT).
SHIFT=0
age() { SHIFT=$((SHIFT + $1)); }

# run_poller <python expression raised or returned by the fake urlopen>
run_poller() {
    HOME="$TH_TMP2/home" AGENT_USAGE_TRACKER_RUNTIME="$RT" python3 -c "
import sys, io, json, http.client, urllib.error, urllib.request
sys.path.insert(0, '$REPO_ROOT/src')
import time
_real_time = time.time
time.time = lambda: _real_time() + $SHIFT
import codex_plan_history_poller as m
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

section "first run -> fetches, stores the raw response"
out="$(run_poller "return Resp(b'$BODY')")"
assert_contains "sends the bearer token from auth.json" "$out" '"auth": "Bearer SECRET-TOKEN-xyz"'
assert_contains "sends the account id" "$out" '"acct": "acct-1"'
row="$(q "SELECT raw FROM account_quotas ORDER BY rowid DESC LIMIT 1")"
assert_eq "tagged codex_plan_limit_history" "codex_plan_limit_history" "$(printf '%s' "$row" | jq -r .source)"
assert_eq "raw response kept unchanged, fractional basis points intact" "118.790896" \
    "$(printf '%s' "$row" | jq -r '.plan_limit_history.periods[0].used_basis_points')"
assert_eq "error is null" "null" "$(printf '%s' "$row" | jq -c .error)"
assert_eq "no current reading in the percent columns" "" "$(q "SELECT five_hour_pct FROM account_quotas")"
assert_file_missing "no cadence state file any more: the database is the memory" "$RT/state/poll"

section "second run within 24h -> no request, no row"
out="$(run_poller "raise AssertionError('must not be called')")"
assert_contains "skips" "$out" "skip:"
assert_eq "still one row" "1" "$(readings)"

section "24h later -> fetches again"
age 86401
run_poller "return Resp(b'$BODY')" > /dev/null
assert_eq "two rows" "2" "$(readings)"

section "HTTP 401 -> a failed attempt in poll_errors (not a reading), retried after an hour, not a day"
age 86401
run_poller "raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, None)" > /dev/null
assert_eq "readings untouched by the failure" "2" "$(readings)"
row="$(last_error_raw)"
assert_eq "error row" '{"stage":"http","type":"HTTPError","status":401,"detail":"Unauthorized"}' "$(printf '%s' "$row" | jq -c .error)"
assert_eq "no response stored" "null" "$(printf '%s' "$row" | jq -c .plan_limit_history)"
assert_contains "not retried within the hour" "$(run_poller "raise AssertionError('must not be called')")" "skip:"
age 3601
run_poller "return Resp(b'$BODY')" > /dev/null
assert_eq "retried after an hour" "3" "$(readings)"

section "server disconnect -> network error row, no crash"
age 86401
run_poller "raise http.client.RemoteDisconnected('closed')" > /dev/null
assert_eq "network error" "network RemoteDisconnected" "$(last_error_raw | jq -r '"\(.error.stage) \(.error.type)"')"

section "missing auth file -> auth error row without any file content"
rm "$TH_TMP2/home/.codex/auth.json"
age 86401
run_poller "raise AssertionError('must not be called')" > /dev/null
assert_eq "auth error" '{"stage":"auth","type":"FileNotFoundError"}' "$(last_error_raw | jq -c .error)"

section "the token never reaches the database"
assert_not_contains "no token in any row" "$(q "SELECT raw FROM account_quotas UNION ALL SELECT raw FROM poll_errors")" "SECRET-TOKEN-xyz"
assert_eq "readings hold no failed attempt" "0" "$(q "SELECT COUNT(*) FROM account_quotas WHERE raw LIKE '%\"stage\"%'")"

harness_summary
