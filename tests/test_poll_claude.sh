#!/bin/bash
# Unit tests for src/quota_polling/poll_claude.py's fetch_usage error
# handling: every transport failure must come back as an error dict (logged
# as a row), never escape and crash the run. urlopen is monkeypatched - no
# network, no Keychain.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

QUOTA_POLLING_DIR="$REPO_ROOT/src/quota_polling"

fetch_with() {
    python3 -c "
import sys, http.client, socket, urllib.error, urllib.request
sys.path.insert(0, '$QUOTA_POLLING_DIR')
import poll_claude
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

# should_poll <heartbeat age s or ""> <rows JSON lines> -> True/False at now=10000
# Points QUOTA_LOG_FILE and HEARTBEAT_FILE at temp files; "" = no heartbeat.
should_poll() {
    local dir; dir="$(mktemp -d "${TMPDIR:-/tmp}/th-poll.XXXXXX")"
    printf '%s\n' "$2" > "$dir/account.jsonl"
    python3 -c "
import os, sys
from pathlib import Path
sys.path.insert(0, '$QUOTA_POLLING_DIR')
import poll_claude
poll_claude.QUOTA_LOG_FILE = Path('$dir/account.jsonl')
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
    "$(should_poll 5 '{"ts":9940,"source":"claude","error":null}')"
assert_eq "last poll 119s ago (second tick, ts stamped after the request) -> poll" "True" \
    "$(should_poll 5 '{"ts":9881,"source":"claude","error":null}')"
assert_eq "push rows in between don't count as polls" "True" \
    "$(should_poll 5 '{"ts":9870,"source":"claude","error":null}
{"ts":9990,"source":"claude_statusline"}')"
assert_eq "no poller row yet -> poll" "True" \
    "$(should_poll 5 '{"ts":9990,"source":"claude_statusline"}')"
assert_eq "Retry-After still wins while watched" "False" \
    "$(should_poll 5 '{"ts":9700,"source":"claude","error":{"retry_after_s":600}}')"

section "idle (no heartbeat) -> unchanged ~5 min cadence"
assert_eq "last row 200s ago -> skip" "False" \
    "$(should_poll "" '{"ts":9800,"source":"claude","error":null}')"
assert_eq "last row 300s ago -> poll" "True" \
    "$(should_poll "" '{"ts":9700,"source":"claude","error":null}')"

harness_summary
