#!/bin/bash
# Unit tests for src/claude_quota_api_poller.py: fetch_usage's error
# handling (every transport failure must come back as an error dict, stored
# as a row, never escape and crash the run), where each outcome is stored,
# its exit codes and last lines (read by its job-runner entrypoint), and
# --peer, with a fake ssh running the peer's gate in a second temp runtime.
# urlopen is monkeypatched - no network, no Keychain.
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

# A fake ssh: runs the remote command locally with HOME=$PEER_HOME, whose
# ~/opt/agent-usage-tracker/src is this repo's src/; PEER_HOME=down: unreachable.
FAKE="$(mktemp -d "${TMPDIR:-/tmp}/th-peer.XXXXXX")"
trap 'rm -rf "$FAKE"' EXIT
mkdir -p "$FAKE/bin" "$FAKE/peer/opt/agent-usage-tracker"
ln -s "$SRC_DIR" "$FAKE/peer/opt/agent-usage-tracker/src"
cat > "$FAKE/bin/ssh" <<'SSH'
#!/bin/bash
while [ "$1" = -o ]; do shift 2; done
[ "$PEER_HOME" = down ] && exit 255
env -u AGENT_USAGE_TRACKER_RUNTIME HOME="$PEER_HOME" bash -c "$2"   # a real ssh passes no environment
SSH
chmod +x "$FAKE/bin/ssh"
LOCAL="$FAKE/local/opt/agent-usage-tracker"
peer_db() { python3 -c "
import sqlite3, sys
d = sqlite3.connect('$FAKE/peer/opt/agent-usage-tracker/data/claude/account_quotas.db')
print(d.execute(sys.argv[1]).fetchone()[0])" "$1"; }
local_db() { python3 -c "
import sqlite3, sys
d = sqlite3.connect('$LOCAL/data/claude/account_quotas.db')
print(d.execute(sys.argv[1]).fetchone()[0])" "$1"; }

# poll <fetch_usage return expression> [poller args...] -> "<exit code> <last line>"; the local runtime is $LOCAL
poll() {
    local expr="$1"; shift
    out="$(PATH="$FAKE/bin:$PATH" AGENT_USAGE_TRACKER_RUNTIME="$LOCAL" python3 -c "
import sys
sys.path.insert(0, '$SRC_DIR')
import claude_quota_api_poller as poll_claude
poll_claude.fetch_token = lambda: ('dummy', None)
poll_claude.fetch_usage = lambda token: $expr
poll_claude.main(sys.argv[1:])
" "$@" 2>/dev/null)"
    echo "$? $(printf '%s\n' "$out" | tail -n 1)"
}
READING="({'five_hour': {'utilization': 42.0}, 'seven_day': {'utilization': 18}}, {}, None)"
NEVER="(_ for _ in ()).throw(AssertionError('must not poll'))"

section "exit codes and last lines (read by claude-quota.sh)"
assert_eq "a reading -> 0, its summary last" "0 5h 42% · 7d 18%" "$(poll "$READING")"
assert_eq "429 -> 75, retry-after last" "75 retry-after 1820" \
    "$(poll "(None, None, {'stage': 'http', 'type': 'HTTPError', 'status': 429, 'retry_after_s': 1820})")"
assert_eq "429 without Retry-After -> retry-after 0" "75 retry-after 0" "$(poll "(None, None, {'stage': 'http', 'type': 'HTTPError', 'status': 429})")"
assert_eq "401 -> 1" "1 failed: http HTTPError 401" "$(poll "(None, None, {'stage': 'http', 'type': 'HTTPError', 'status': 401})")"
assert_eq "network error -> 1" "1 failed: network URLError" "$(poll "(None, None, {'stage': 'network', 'type': 'URLError'})")"

section "--peer: the peer's fresh reading is taken (exit 10), no poll"
LOCAL="$FAKE/local2/opt/agent-usage-tracker"   # a machine with no reading yet
NOW_TS="$(date +%s)"
printf '{"ts":%s,"source":"claude_api","api":{"five_hour":{"utilization":7}},"error":null}\n' "$((NOW_TS - 30))" \
    | HOME="$FAKE/peer" python3 "$SRC_DIR/ingest_account_quota.py" --agent claude >/dev/null
assert_eq "--latest prints the peer's freshest reading" "$((NOW_TS - 30))" \
    "$(HOME="$FAKE/peer" python3 "$SRC_DIR/ingest_account_quota.py" --agent claude --latest | jq .ts)"
assert_match "30 s old, fresh within 110 s -> exit 10" "$(PEER_HOME="$FAKE/peer" poll "$NEVER" --peer=vm --fresh-within=110)" \
    "^10 vm's reading is fresh \(3[0-9] s old\): taken$"
assert_eq "stored here through the local gate" "$((NOW_TS - 30))" "$(local_db "SELECT MAX(ts) FROM account_quotas")"
assert_eq "and in this machine's state file" "7" "$(cut -d $'\034' -f1 "$LOCAL/state/quota/claude")"

section "--peer: the peer's reading is stale -> poll, the reading handed to the peer's gate"
assert_eq "fresh within 20 s -> poll" "0 5h 42% · 7d 18%" "$(PEER_HOME="$FAKE/peer" poll "$READING" --peer=vm --fresh-within=20)"
assert_eq "the peer stored the reading" "2" "$(peer_db "SELECT COUNT(*) FROM account_quotas")"
assert_eq "the same row on both sides (same row_hash)" "$(local_db "SELECT row_hash FROM account_quotas ORDER BY rowid DESC LIMIT 1")" \
    "$(peer_db "SELECT row_hash FROM account_quotas ORDER BY rowid DESC LIMIT 1")"
assert_eq "the peer's state file refreshed" "42" "$(cut -d $'\034' -f1 "$FAKE/peer/opt/agent-usage-tracker/state/quota/claude")"
assert_eq "a 429 is not handed over" "75 retry-after 60" \
    "$(PEER_HOME="$FAKE/peer" poll "(None, None, {'stage': 'http', 'status': 429, 'retry_after_s': 60})" --peer=vm --fresh-within=0)"
assert_eq "the peer has no failed attempt" "0" "$(peer_db "SELECT COUNT(*) FROM poll_errors")"

section "--peer: the peer unreachable -> polls alone"
assert_eq "exit 0, the summary still last" "0 5h 42% · 7d 18%" "$(PEER_HOME=down poll "$READING" --peer=vm --fresh-within=110)"

harness_summary
