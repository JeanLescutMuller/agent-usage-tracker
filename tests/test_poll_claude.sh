#!/bin/bash
# Unit tests for src/claude_quota_api_poller.py: fetch_usage's error
# handling (every transport failure must come back as an error dict, stored
# as a row, never escape and crash the run), its cadence and backoff read
# back from account_quotas.db, and where each outcome is stored. urlopen is
# monkeypatched - no network, no Keychain.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SRC_DIR="$REPO_ROOT/src"

fetch_with() {
    python3 -c "
import sys, http.client, socket, urllib.error, urllib.request
sys.path.insert(0, '$SRC_DIR')
import claude_quota_api_poller as poll_claude
def boom(*a, **k): raise $1
urllib.request.urlopen = boom
body, headers, err = poll_claude.fetch_usage('dummy-token')
print(err['stage'], err['type'])
" 2>&1
}

section "server closes the connection -> network error row, no crash"
# Regression test: http.client.RemoteDisconnected is an OSError, not a
# URLError, and used to escape every handler (7 crashes by 2026-09-29).
assert_eq "RemoteDisconnected" "network RemoteDisconnected" \
    "$(fetch_with 'http.client.RemoteDisconnected("Remote end closed connection without response")')"
assert_eq "ConnectionResetError" "network ConnectionResetError" \
    "$(fetch_with 'ConnectionResetError(54, "Connection reset by peer")')"

section "existing failure classes keep their stage"
assert_eq "URLError stays a network error" "network URLError" \
    "$(fetch_with 'urllib.error.URLError("offline")')"

# should_poll <heartbeat age s or ""> <reading rows> [failed-attempt rows] ->
# True/False at now=10000. The rows (old JSONL shape, one per line) go
# through the gate into a temp runtime's account_quotas.db; "" = no heartbeat.
should_poll() {
    local dir; dir="$(mktemp -d "${TMPDIR:-/tmp}/th-poll.XXXXXX")"
    printf '%s\n%s\n' "$2" "${3:-}" | AGENT_USAGE_TRACKER_RUNTIME="$dir" \
        python3 "$SRC_DIR/ingest_account_quota.py" --agent claude --no-dedup --no-state >/dev/null
    AGENT_USAGE_TRACKER_RUNTIME="$dir" python3 -c "
import os, sys
from pathlib import Path
sys.path.insert(0, '$SRC_DIR')
import claude_quota_api_poller as poll_claude
poll_claude.HEARTBEAT_FILE = Path('$dir/heartbeat')
age = '$1'
if age:
    poll_claude.HEARTBEAT_FILE.touch()
    os.utime(poll_claude.HEARTBEAT_FILE, (10000 - int(age), 10000 - int(age)))
print(poll_claude._should_poll(10000))
" 2>&1
    rm -rf "$dir"
}

section "watched (fresh heartbeat) -> poll every 120s, not every 60s tick"
assert_eq "last poll 60s ago -> skip (a minute apart drew a 429 every other time)" "False" \
    "$(should_poll 5 '{"ts":9940,"source":"claude_api","error":null}')"
assert_eq "last poll 119s ago (second tick, ts stamped after the request) -> poll" "True" \
    "$(should_poll 5 '{"ts":9881,"source":"claude_api","error":null}')"
assert_eq "push rows in between don't count as polls" "True" \
    "$(should_poll 5 '{"ts":9870,"source":"claude_api","error":null}
{"ts":9990,"source":"claude_statusline"}')"
assert_eq "no poller row yet -> poll" "True" \
    "$(should_poll 5 '{"ts":9990,"source":"claude_statusline"}')"
assert_eq "Retry-After still wins while watched, read from poll_errors" "False" \
    "$(should_poll 5 '{"ts":9600,"source":"claude_api","error":null}' '{"ts":9700,"source":"claude_api","error":{"retry_after_s":600}}')"
assert_eq "a failed attempt 60s ago counts as the last poll" "False" \
    "$(should_poll 5 '{"ts":9800,"source":"claude_api","error":null}' '{"ts":9940,"source":"claude_api","error":{"stage":"http","status":429}}')"

section "idle (no heartbeat) -> unchanged ~5 min cadence"
assert_eq "a failed attempt 200s ago holds off the idle poll" "False" \
    "$(should_poll "" '{"ts":9000,"source":"claude_api","error":null}' '{"ts":9800,"source":"claude_api","error":{"stage":"network"}}')"
assert_eq "last row 200s ago -> skip" "False" \
    "$(should_poll "" '{"ts":9800,"source":"claude_api","error":null}')"
assert_eq "last row 300s ago -> poll" "True" \
    "$(should_poll "" '{"ts":9700,"source":"claude_api","error":null}')"

# poll_once <fetch_usage return expression> -> runs main() with the token
# and request faked, against a temp runtime; prints "<readings> <failed
# attempts> <state file source>".
poll_once() {
    local dir; dir="$(mktemp -d "${TMPDIR:-/tmp}/th-poll.XXXXXX")"
    AGENT_USAGE_TRACKER_RUNTIME="$dir" python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '$SRC_DIR')
import claude_quota_api_poller as poll_claude
poll_claude.HEARTBEAT_FILE = Path('$dir/heartbeat')
poll_claude.fetch_token = lambda: ('dummy', None)
poll_claude.fetch_usage = lambda token: $1
poll_claude.main()
" > /dev/null 2>&1
    python3 -c "
import sqlite3
d = sqlite3.connect('$dir/data/claude/account_quotas.db')
print(d.execute('SELECT COUNT(*) FROM account_quotas').fetchone()[0], d.execute('SELECT COUNT(*) FROM poll_errors').fetchone()[0], end=' ')
"
    cut -d $'\034' -f5 "$dir/state/quota/claude" 2>/dev/null || printf 'none'
    rm -rf "$dir"
}

section "a reading goes to account_quotas (and the state file), a failed attempt only to poll_errors"
assert_eq "success -> one reading, no failed attempt, state file from claude_api" "1 0 claude_api" \
    "$(poll_once "({'five_hour': {'utilization': 5, 'resets_at': '2026-10-08T12:00:00Z'}}, {}, None)")"
assert_eq "429 -> no reading, one failed attempt, no state file" "0 1 none" \
    "$(poll_once "(None, None, {'stage': 'http', 'status': 429})")"

harness_summary
