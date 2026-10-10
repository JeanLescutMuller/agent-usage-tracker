#!/bin/bash
# Unit tests for src/claude_quota_api_poller.py: fetch_usage's error
# handling (every transport failure must come back as an error dict, stored
# as a row, never escape and crash the run), where each outcome is stored,
# its exit codes and last lines (read by its job-runner entrypoint), the
# age check (--if-older-than), and the sync (--sync-with), with a fake ssh
# running the VM's gate in a second temp runtime.
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
# attempts> <latest view's source, or none>".
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
    python3 -c "
import sqlite3
row = sqlite3.connect('$dir/data/claude/account_quotas.db').execute('SELECT source FROM latest').fetchone()
print(row[0] if row else 'none')"
    rm -rf "$dir"
}

section "a reading goes to account_quotas, a failed attempt only to poll_errors"
assert_eq "success -> one reading, no failed attempt, latest from claude_api" "1 0 claude_api" \
    "$(poll_once "({'five_hour': {'utilization': 5, 'resets_at': '2026-10-08T12:00:00Z'}}, {}, None)")"
assert_eq "429 -> no reading, one failed attempt, nothing in latest" "0 1 none" \
    "$(poll_once "(None, None, {'stage': 'http', 'status': 429})")"

# A fake ssh: runs the remote command locally as machine "vm", with
# HOME=$PEER_HOME, whose ~/opt/agent-usage-tracker/src is this repo's src/;
# PEER_HOME=down: unreachable. The local machine is "mac".
FAKE="$(mktemp -d "${TMPDIR:-/tmp}/th-peer.XXXXXX")"
trap 'rm -rf "$FAKE"' EXIT
mkdir -p "$FAKE/bin" "$FAKE/peer/opt/agent-usage-tracker"
ln -s "$SRC_DIR" "$FAKE/peer/opt/agent-usage-tracker/src"
cat > "$FAKE/bin/ssh" <<'SSH'
#!/bin/bash
while [ "$1" = -o ]; do shift 2; done
[ "$PEER_HOME" = down ] && exit 255
echo "a login banner"                 # what a login script may print first
env -u AGENT_USAGE_TRACKER_RUNTIME HOME="$PEER_HOME" JR_MACHINE_NAME=vm bash -c "$2"   # a real ssh passes no environment
SSH
chmod +x "$FAKE/bin/ssh"
LOCAL="$FAKE/local/opt/agent-usage-tracker"
PEER="$FAKE/peer/opt/agent-usage-tracker"
export PEER_HOME="$FAKE/peer"
peer_db() { python3 -c "
import sqlite3, sys
d = sqlite3.connect('$PEER/data/claude/account_quotas.db')
print(*d.execute(sys.argv[1]).fetchone(), sep='\t')" "$1"; }
local_db() { python3 -c "
import sqlite3, sys
d = sqlite3.connect('$LOCAL/data/claude/account_quotas.db')
print(*d.execute(sys.argv[1]).fetchone(), sep='\t')" "$1"; }

# poll <fetch_usage return expression> [poller args...] -> "<exit code> <last line>"; the local runtime is $LOCAL
poll() {
    local expr="$1"; shift
    out="$(PATH="$FAKE/bin:$PATH" AGENT_USAGE_TRACKER_RUNTIME="$LOCAL" JR_MACHINE_NAME=mac python3 -c "
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
assert_eq "...the reading names its machine, in raw and in the column" "mac	mac" \
    "$(local_db "SELECT machine, json_extract(raw, '$.machine') FROM account_quotas ORDER BY rowid DESC LIMIT 1")"
assert_eq "429 -> 75, retry-after last" "75 retry-after 1820" \
    "$(poll "(None, None, {'stage': 'http', 'type': 'HTTPError', 'status': 429, 'retry_after_s': 1820})")"
assert_eq "429 without Retry-After -> retry-after 0" "75 retry-after 0" "$(poll "(None, None, {'stage': 'http', 'type': 'HTTPError', 'status': 429})")"
assert_eq "401 -> 1" "1 failed: http HTTPError 401" "$(poll "(None, None, {'stage': 'http', 'type': 'HTTPError', 'status': 401})")"
assert_eq "network error -> 1" "1 failed: network URLError" "$(poll "(None, None, {'stage': 'network', 'type': 'URLError'})")"

section "--if-older-than: no poll while the newest reading is younger"
assert_match "the reading above is seconds old: exit 10, its age" "$(poll "$NEVER" --if-older-than=110)" "^10 quota fresh enough \([0-9]+ s\)$"
assert_eq "older than 0 s: polls" "0 5h 42% · 7d 18%" "$(poll "$READING" --if-older-than=0)"

section "--sync-with: each machine's own readings, both ways, in one ssh call"
# The VM's own reading (1 s after the Mac's, so it is the newest), and one from before 2026-10-10
# (no machine in raw; labelled by the migration).
sleep 1
JR_MACHINE_NAME=vm AGENT_USAGE_TRACKER_RUNTIME="$PEER" python3 -c "
import sys, time; sys.path.insert(0, '$SRC_DIR')
import ingest_account_quota as g, usage_db
db = usage_db.open_account('claude')
g.add_row(db, 'claude', {'ts': int(time.time()), 'source': 'claude_api', 'machine': 'vm', 'api': {'five_hour': {'utilization': 50.0}}})
g.add_row(db, 'claude', {'ts': int(time.time()) - 900, 'source': 'claude_api', 'api': {'five_hour': {'utilization': 49.0}}}, machine='vm')"
mac_before="$(local_db "SELECT COUNT(*) FROM account_quotas")"
assert_eq "fresh: no poll, the sync said in the last line" "10 quota fresh enough (5 s) · sync ↑$mac_before ↓2" \
    "$(poll "$NEVER" --sync-with=vm --sync-every=110 --if-older-than=99999 | sed -E 's/\(-?[0-9]+ s\)/(5 s)/')"
assert_eq "the VM got the Mac's readings, labelled mac" "$mac_before" "$(peer_db "SELECT COUNT(*) FROM account_quotas WHERE machine = 'mac'")"
assert_eq "the Mac got the VM's two, labelled vm, the old one included" "2" "$(local_db "SELECT COUNT(*) FROM account_quotas WHERE machine = 'vm'")"
assert_eq "the same rows on both sides (row_hash)" "$(peer_db "SELECT group_concat(row_hash) FROM (SELECT row_hash FROM account_quotas ORDER BY row_hash)")" \
    "$(local_db "SELECT group_concat(row_hash) FROM (SELECT row_hash FROM account_quotas ORDER BY row_hash)")"
assert_eq "the Mac's latest is the VM's fresher reading" "vm	50.0" "$(local_db "SELECT machine, five_hour_pct FROM latest")"
assert_match "watermarks saved" "$(cat "$LOCAL/state/sync/claude_quotas.json")" '"sent": [0-9]+, "received": [0-9]+, "last_ok_ts": [0-9]+'
assert_eq "not due yet (110 s): no sync" "10 quota fresh enough (5 s)" \
    "$(poll "$NEVER" --sync-with=vm --sync-every=110 --if-older-than=99999 | sed -E 's/\(-?[0-9]+ s\)/(5 s)/')"
assert_match "due again: nothing new either way, nothing echoed back" "$(poll "$NEVER" --sync-with=vm --sync-every=0 --if-older-than=99999)" "· sync ↑0 ↓0$"
peer_before="$(peer_db "SELECT COUNT(*) FROM account_quotas")"
poll "({'five_hour': {'utilization': 43.0}}, {}, None)" --sync-with=vm --sync-every=110 >/dev/null   # 43: not the same row as a poll of the same second
assert_eq "a new Mac reading waits for the next sync" "$peer_before" "$(peer_db "SELECT COUNT(*) FROM account_quotas")"
poll "$NEVER" --sync-with=vm --sync-every=0 --if-older-than=99999 >/dev/null
assert_eq "...and then goes" "$((peer_before + 1))" "$(peer_db "SELECT COUNT(*) FROM account_quotas")"

section "--sync-with: the VM unreachable"
assert_match "the run goes on, the summary says so" "$(PEER_HOME=down poll "$NEVER" --sync-with=vm --sync-every=0 --if-older-than=99999)" "^10 .* · vm not reached$"
assert_eq "...and polls when needed" "0 5h 42% · 7d 18% · vm not reached" "$(PEER_HOME=down poll "$READING" --sync-with=vm --sync-every=0)"
python3 -c "
import json, sys; p = sys.argv[1]; d = json.load(open(p)); d['last_ok_ts'] -= 1800; json.dump(d, open(p, 'w'))" "$LOCAL/state/sync/claude_quotas.json"
assert_match "unreachable for 30 min: a failure" "$(PEER_HOME=down poll "$NEVER" --sync-with=vm --sync-every=0 --if-older-than=99999)" "^1 .* · vm not reached$"
assert_eq "a 429 still waits, its seconds last" "75 vm not reached · retry-after 60" \
    "$(PEER_HOME=down poll "(None, None, {'stage': 'http', 'status': 429, 'retry_after_s': 60})" --sync-with=vm --sync-every=0)"

harness_summary
