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

harness_summary
