#!/bin/bash
# Tests for src/push_to_central.py and src/receive_from_machine.py (Category
# 3 sent to the VM, design/DESIGN.md §6.4): the new slots reach
# data/claude/aggregates/<machine>.db through the gate, a second
# send sends nothing, a dead link keeps the watermark, a bad chunk is
# refused whole; and aggregate_slots.py --send-to. A fake ssh runs the
# receiver in a second temp home (the "VM").
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SRC_DIR="$REPO_ROOT/src"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-push.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
RT="$TMP/mac/opt/agent-usage-tracker"
VMH="$TMP/vm"
mkdir -p "$VMH/opt/agent-usage-tracker" "$TMP/bin" "$RT"
ln -s "$SRC_DIR" "$VMH/opt/agent-usage-tracker/src"
CENTRAL_DB="$VMH/opt/agent-usage-tracker/data/claude/aggregates/m1.db"
cat > "$TMP/bin/ssh" <<SSH
#!/bin/bash
while [ "\${1:0:1}" = - ]; do [ "\$1" = -o ] && shift; shift; done
[ "\$VM_DOWN" = 1 ] && { echo "ssh: connect to host: Network is unreachable" >&2; exit 255; }
env -u AGENT_USAGE_TRACKER_RUNTIME HOME="$VMH" JR_MACHINE_NAME=vm bash -c "\$2"
SSH
chmod +x "$TMP/bin/ssh"
export AGENT_USAGE_TRACKER_RUNTIME="$RT" JR_MACHINE_NAME=m1 PATH="$TMP/bin:$PATH"

q() { python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$1" "$2"; }
push() { python3 "$SRC_DIR/push_to_central.py" --central vm; }
# slots <ts>...: one slot per ts on "this machine", through the gate
slots() { python3 -c "
import sys; sys.path.insert(0, '$SRC_DIR'); import usage_db, ingest_aggregates as g
g.add_slots(usage_db.open_aggregates('claude'), [dict(ts=t, dt=usage_db.iso(t), machine='m1', folder='/a', entrypoint='cli', model='m',
    requests=1, input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_write_5m_tokens=0, cache_write_1h_tokens=0,
    cache_write_unknown_tokens=0, usd=0.5, tokens_without_usd=0, written_ts=1) for t in map(int, sys.argv[1:])])" "$@"; }

slots 300 600 900
section "first send: every slot reaches the VM's aggregates/<machine>.db, through the gate"
th_run push
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "says how many" "$TH_OUT" "sent 3 to vm"
assert_eq "the same rows there" "$(q "$RT/data/claude/aggregates/m1.db" "SELECT * FROM slots ORDER BY ts")" "$(q "$CENTRAL_DB" "SELECT * FROM slots ORDER BY ts")"
assert_eq "nothing else written on the VM" "m1.db" "$(ls "$VMH/opt/agent-usage-tracker/data/claude/aggregates" | grep -v -e '-wal$' -e '-shm$')"

section "second send: nothing new, nothing sent"
assert_contains "zero" "$(push)" "sent 0 to vm"

section "a new slot: only it goes"
slots 1200
assert_contains "one" "$(push)" "sent 1 to vm"
assert_eq "four slots there" "4" "$(q "$CENTRAL_DB" "SELECT COUNT(*) FROM slots")"

section "a dead link: error, watermark unchanged, sent on the next run"
slots 1500
before="$(cat "$RT/state/push/watermarks.json")"
th_run env VM_DOWN=1 python3 "$SRC_DIR/push_to_central.py" --central vm
assert_status "exits 1" 1 "$TH_STATUS"
assert_contains "says it will retry" "$TH_ERR" "retried next run"
assert_eq "watermark unchanged" "$before" "$(cat "$RT/state/push/watermarks.json")"
assert_contains "the next working run sends it" "$(push)" "sent 1 to vm"

section "the receiver: one bad line refuses the whole chunk"
good='{"agent":"claude","table":"slots","row":{"ts":1800,"dt":"1970-01-01T00:30:00Z","machine":"m1","folder":"","entrypoint":"","model":"m","requests":1,"input_tokens":1,"output_tokens":1,"cache_read_tokens":0,"cache_write_5m_tokens":0,"cache_write_1h_tokens":0,"cache_write_unknown_tokens":0,"usd":null,"tokens_without_usd":2,"written_ts":1}}'
for bad in '{"agent":"claude","table":"account_quotas","raw":"{}"}' "${good/\"machine\":\"m1\"/\"machine\":\"other\"}" "${good/\"ts\":1800/\"ts\":1801}"; do
    th_run env -u AGENT_USAGE_TRACKER_RUNTIME HOME="$VMH" JR_MACHINE_NAME=vm python3 "$SRC_DIR/receive_from_machine.py" --machine m1 <<< "$good
$bad"
    assert_status "refused: ${bad:0:60}…" 1 "$TH_STATUS"
done
assert_eq "...and the good line with it" "0" "$(q "$CENTRAL_DB" "SELECT COUNT(*) FROM slots WHERE ts = 1800")"
th_run python3 "$SRC_DIR/receive_from_machine.py" --machine "../escape" < /dev/null
assert_status "a machine name that is not a host name: refused" 1 "$TH_STATUS"
assert_file_missing "nothing created outside data/claude/aggregates/" "$RT/escape"
th_run env -u AGENT_USAGE_TRACKER_RUNTIME HOME="$VMH" JR_MACHINE_NAME=vm python3 "$SRC_DIR/receive_from_machine.py" --machine vm <<< "${good/\"machine\":\"m1\"/\"machine\":\"vm\"}"
assert_status "slots sent as the receiving machine itself: refused" 1 "$TH_STATUS"
assert_file_missing "...its own file untouched" "$VMH/opt/agent-usage-tracker/data/claude/aggregates/vm.db"

section "aggregate_slots.py --send-to: written, then sent"
th_run python3 -c "
import sys; sys.path.insert(0, '$SRC_DIR'); import aggregate_slots as a
a.transcript_reader.main = lambda: ''
a.main(['--send-to', 'vm'])"
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "the summary says what was sent" "$TH_OUT" "· sent 0 to vm"
slots 2100   # something to send (with nothing to send, no ssh call is made)
th_run env VM_DOWN=1 python3 -c "
import sys; sys.path.insert(0, '$SRC_DIR'); import aggregate_slots as a
a.transcript_reader.main = lambda: ''
a.main(['--send-to', 'vm'])"
assert_status "the VM unreachable: exit 1" 1 "$TH_STATUS"
assert_contains "...the summary says so" "$TH_OUT" "· vm not reached"

harness_summary
