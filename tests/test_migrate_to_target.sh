#!/bin/bash
# Tests for adhoc_quotas_analysis/migrate_to_target.py, the hand-run steps of
# design/MIGRATION_PLAN.md. backup: every database copied whole and checked,
# other files kept, _archive and WAL side files left out, a log line per run.
# Phase 1: the readings both machines hold, the sync's first watermarks, the
# machine column filled (shared readings NULL), the VM seeded from its
# central copy; every step idempotent.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-target.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
MIGRATE="$REPO_ROOT/adhoc_quotas_analysis/migrate_to_target.py"
RT="$TMP/runtime"

section "backup: a consistent copy of data/, state/ and central/"
# A WAL database with rows still in its -wal file (the connection stays open
# while the backup runs), a state file, a central copy, and things to skip.
mkdir -p "$RT/data/claude" "$RT/state/quota" "$RT/central/m1/data/claude" "$RT/data/_archive"
printf 'x\n' > "$RT/state/quota/claude"
printf 'old\n' > "$RT/data/_archive/old.tar.gz"
python3 - "$RT" <<'PY' &
import sqlite3, sys, time
for path in (f"{sys.argv[1]}/data/claude/account_quotas.db", f"{sys.argv[1]}/central/m1/data/claude/account_quotas.db"):
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("CREATE TABLE account_quotas (ts INTEGER, row_hash TEXT UNIQUE)")
    db.executemany("INSERT INTO account_quotas VALUES (?, ?)", [(i, f"h{i}") for i in range(250)])
open(f"{sys.argv[1]}/ready", "w").close()
time.sleep(5)  # keep the WAL alive while the backup reads
PY
writer=$!
for _ in $(seq 50); do [ -e "$RT/ready" ] && break; sleep 0.1; done
out="$(python3 "$MIGRATE" backup --runtime "$RT")"
wait "$writer"
assert_contains "each database checked, with its counts" "$out" "data/claude/account_quotas.db: ok; account_quotas 250"
assert_contains "...the central copy too" "$out" "central/m1/data/claude/account_quotas.db: ok; account_quotas 250"
tarball="$(ls "$RT/data/_archive"/pre-target-*.tar.gz)"
listing="$(tar -tzf "$tarball")"
assert_contains "state files are in the archive" "$listing" "state/quota/claude"
assert_not_contains "_archive is not archived again" "$listing" "old.tar.gz"
assert_not_contains "nor WAL side files" "$listing" "-wal"
mkdir "$TMP/x" && tar -xzf "$tarball" -C "$TMP/x"
assert_eq "the copy has every row, WAL included" "250" \
    "$(python3 -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT COUNT(*) FROM account_quotas").fetchone()[0])' "$TMP/x/data/claude/account_quotas.db")"
assert_contains "the run is logged" "$(cat "$RT/data/_archive/migrate_to_target.log")" "backup: "
assert_eq "no staging folder left behind" "" "$(ls -a "$RT/data/_archive" | grep staging)"

section "phase 1: machine column, shared readings, the VM seeded"
# Two machines in the old layout (no machine column): "mac" holds r1 (its own)
# and r2 (copied by the old --peer); "vm" holds v1 (its own), r2, and the
# central copy of the Mac's readings (r1, r2).
MAC="$TMP/mac/opt/agent-usage-tracker"
VMH="$TMP/vm"
VM="$VMH/opt/agent-usage-tracker"
old_db() {   # old_db <path> <ts>... : an old-layout account_quotas.db holding readings ts...
    AGENT_USAGE_TRACKER_RUNTIME="$TMP/scratch" python3 - "$@" <<PY
import sys, sqlite3, json; sys.path.insert(0, "$REPO_ROOT/src")
import usage_db, ingest_account_quota as g
from pathlib import Path
path = Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)
db = usage_db.connect(path, usage_db.ACCOUNT_SCHEMA)
for ts in sys.argv[2:]:
    g.add_row(db, "claude", {"ts": int(ts), "source": "claude_api", "api": {"five_hour": {"utilization": 1.0}}})
db.execute("DROP VIEW latest")
db.execute("ALTER TABLE account_quotas DROP COLUMN machine")
db.execute("CREATE VIEW latest AS SELECT ts FROM account_quotas ORDER BY ts DESC LIMIT 1")
db.execute("PRAGMA user_version = 1")
PY
}
old_db "$MAC/data/claude/account_quotas.db" 100 200
old_db "$VM/data/claude/account_quotas.db" 150 200
old_db "$VM/central/mac/data/claude/account_quotas.db" 100 200
mkdir -p "$MAC/state/push" && printf '{"claude/account_quotas": 2}' > "$MAC/state/push/watermarks.json"
mkdir -p "$TMP/bin" && cat > "$TMP/bin/ssh" <<SSH
#!/bin/bash
while [ "\$1" = -o ]; do shift 2; done
echo "a login banner"
HOME="$VMH" bash -c "\$2"
SSH
chmod +x "$TMP/bin/ssh"
step() { local rt="$1" name="$2"; shift 2; PATH="$TMP/bin:$PATH" JR_MACHINE_NAME="$name" python3 "$MIGRATE" "$@" --runtime "$rt"; }
qq() { python3 -c 'import sqlite3, sys
for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2]):
    print("\t".join("" if v is None else str(v) for v in r))' "$1" "$2"; }
SHARED="$MAC/data/_archive/cat1_shared.txt"

assert_contains "cat1-shared: one reading in both" "$(step "$MAC" mac cat1-shared --peer vm)" "in both 1 {'claude_api': 1}"
assert_eq "...its row_hash in the file" "$(qq "$MAC/data/claude/account_quotas.db" "SELECT row_hash FROM account_quotas WHERE ts = 200")" "$(cat "$SHARED")"
step "$MAC" mac sync-init >/dev/null
assert_eq "sync-init: sent = the push watermark, received = 0" '{"sent": 2, "received": 0, "last_ok_ts": 0, "seeded_through": 2}' "$(cat "$MAC/state/sync/claude_quotas.json")"
assert_contains "...and never overwrites it" "$(step "$MAC" mac sync-init)" "exists, unchanged"

step "$VM" vm cat1-machine --shared "$SHARED" >/dev/null
assert_eq "cat1-machine (VM): its own reading labelled, the shared one NULL" "150	vm
200	" "$(qq "$VM/data/claude/account_quotas.db" "SELECT ts, machine FROM account_quotas ORDER BY ts")"
assert_eq "...the central copy labelled by its folder" "100	mac
200	" "$(qq "$VM/central/mac/data/claude/account_quotas.db" "SELECT ts, machine FROM account_quotas ORDER BY ts")"
assert_eq "...the latest view names the machine, the current schema version" "200	3" \
    "$(qq "$VM/data/claude/account_quotas.db" "SELECT (SELECT ts FROM latest), (SELECT user_version FROM pragma_user_version)")"
assert_eq "...a copy taken before the first change" "2" "$(ls "$VM/data/_archive" | grep -c 'pre-machine')"
assert_contains "cat1-seed-vm: the Mac's own reading copied, none missing" "$(step "$VM" vm cat1-seed-vm --shared "$SHARED" --from-machine mac)" "1 readings copied"
assert_eq "...labelled mac" "100	mac
150	vm
200	" "$(qq "$VM/data/claude/account_quotas.db" "SELECT ts, machine FROM account_quotas ORDER BY ts")"
step "$MAC" mac cat1-machine --shared "$SHARED" >/dev/null
assert_eq "cat1-machine (Mac)" "100	mac
200	" "$(qq "$MAC/data/claude/account_quotas.db" "SELECT ts, machine FROM account_quotas ORDER BY ts")"

# The old code writes a reading after the labelling (machine NULL); a sync passes over it; re-labelled: sent goes back.
python3 -c 'import sqlite3, sys
db = sqlite3.connect(sys.argv[1]); db.execute("INSERT INTO account_quotas (ts, dt, source, written_ts, raw, row_hash) VALUES (300, 300, \"claude_api\", 300, \"{}\", \"late\")"); db.commit()' "$MAC/data/claude/account_quotas.db"
printf '{"sent": 9, "received": 0, "last_ok_ts": 1, "seeded_through": 2}' > "$MAC/state/sync/claude_quotas.json"
assert_contains "a late reading labelled after a sync: sent goes back" "$(step "$MAC" mac cat1-machine --shared "$SHARED")" "sync: sent 9 -> 2"
assert_eq "...labelled mac" "mac" "$(qq "$MAC/data/claude/account_quotas.db" "SELECT machine FROM account_quotas WHERE ts = 300")"

before="$(qq "$VM/data/claude/account_quotas.db" "SELECT group_concat(ts || machine) FROM account_quotas")"
step "$VM" vm cat1-machine --shared "$SHARED" >/dev/null; step "$VM" vm cat1-seed-vm --shared "$SHARED" --from-machine mac >/dev/null
assert_eq "re-running both changes nothing" "$before" "$(qq "$VM/data/claude/account_quotas.db" "SELECT group_concat(ts || machine) FROM account_quotas")"
assert_eq "...and takes no second copy" "2" "$(ls "$VM/data/_archive" | grep -c 'pre-machine')"

section "phase 2: remove-trigger, check-intermediary"
P2="$TMP/p2/opt/agent-usage-tracker"
mkdir -p "$TMP/p2/Library/LaunchAgents" "$TMP/p2/.config/systemd/user" "$P2"
touch "$P2/transcripts.sh"
if [ "$(uname)" = Darwin ]; then   # this OS's trigger files only: the step removes those
    touch "$P2/com.jeanlescut.agent-usage-tracker.transcripts.plist"
    ln -s "$P2/com.jeanlescut.agent-usage-tracker.transcripts.plist" "$TMP/p2/Library/LaunchAgents/com.jeanlescut.agent-usage-tracker.transcripts.plist"
else
    touch "$P2/com.jeanlescut.agent-usage-tracker.transcripts.timer"
    ln -s "$P2/com.jeanlescut.agent-usage-tracker.transcripts.timer" "$TMP/p2/.config/systemd/user/com.jeanlescut.agent-usage-tracker.transcripts.timer"
fi
out="$(HOME="$TMP/p2" python3 "$MIGRATE" remove-trigger transcripts --runtime "$P2")"
assert_contains "the job's trigger and entrypoint removed" "$out" "transcripts.sh"
assert_eq "...nothing of it left" "" "$(find "$TMP/p2" -name '*transcripts*' | grep -v _archive)"
assert_contains "re-running: nothing to remove" "$(HOME="$TMP/p2" python3 "$MIGRATE" remove-trigger transcripts --runtime "$P2")" "nothing (already gone)"

# The old requests table, the same requests in intermediary/, telemetry, then the slots: they agree.
export AGENT_USAGE_TRACKER_RUNTIME="$P2" JR_MACHINE_NAME=m1
S1=11111111-1111-1111-1111-111111111111
python3 - <<PY
import sys, time; sys.path.insert(0, "$REPO_ROOT/src")
import usage_db, ingest_session_usage as s, aggregate_slots as a
old, inter = usage_db.connect(usage_db.db_path("claude", "sessions_usages"), usage_db.SESSIONS_SCHEMA + usage_db.CODEX_REQUESTS_SCHEMA), usage_db.open_intermediary("claude")   # the old layout
for ts, rid, out in ((1000, "r1", 10), (1100, "r2", 20), (1400, "r3", 30)):
    s.add_requests(old, [dict(ts=ts, dt="x", request_id=rid, machine="m1", session_id="$S1", folder="/a", entrypoint="cli", model="m",
                              input_tokens=1, output_tokens=out, cache_read_tokens=100, cache_write_5m_tokens=0, cache_write_1h_tokens=0, usd=None)])
    inter.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (ts, "x", rid, "$S1", "/a", "cli", "m", 1, out, 100, 0, 0))
s.add_telemetry(old, {"source": "claude_otel", "observed_at": 1010, "received_at": 1010, "event": "api_request",
                      "attributes": {"session.id": "$S1", "request_id": "r1", "model": "m", "cost_usd": 0.5}})
s.add_telemetry(old, {"source": "claude_otel", "observed_at": 1150, "received_at": 1150, "event": "api_request",
                      "attributes": {"session.id": "$S1", "request_id": "t1", "model": "h", "input_tokens": 4, "cache_creation_tokens": 6, "cost_usd": 0.1}})
inter.execute("INSERT INTO scans VALUES (1950, 'x', 1900, 0)")
a.transcript_reader.main = lambda: ""
time.time = lambda: 2000
a.main()
PY
out="$(python3 "$MIGRATE" check-intermediary --runtime "$P2")"
assert_contains "every old request in intermediary/" "$out" "old requests 3, in intermediary 3; missing 0, different 0"
assert_contains "the slots agree with a separate count" "$out" "slots (4, 373, 0.6, 252), counted (4, 373, 0.6, 252)"
python3 -c 'import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); db.execute("DELETE FROM requests WHERE request_id = \"r2\""); db.commit()' "$P2/intermediary/claude/requests.db"
python3 "$MIGRATE" check-intermediary --runtime "$P2" >/dev/null 2>&1
assert_status "a request missing from intermediary/: exit 1" 1 $?
unset AGENT_USAGE_TRACKER_RUNTIME JR_MACHINE_NAME

section "phase 3: archive-central"
C="$TMP/p3/opt/agent-usage-tracker/central"
mkdir -p "$C/old-mac/data/claude" "$C/new-mac/data/claude" "$C/_archive"
printf 'x' > "$C/old-mac/data/claude/account_quotas.db"; printf 'x' > "$C/new-mac/data/claude/aggregates.db"
out="$(python3 "$MIGRATE" archive-central --runtime "$TMP/p3/opt/agent-usage-tracker")"
assert_contains "the old folder moved whole" "$out" "old-mac: 1 files"
assert_file_exists "...to _archive/<machine>-push-era" "$C/_archive/old-mac-push-era/data/claude/account_quotas.db"
assert_file_exists "a folder with only aggregates.db stays" "$C/new-mac/data/claude/aggregates.db"
assert_contains "re-running moves nothing" "$(python3 "$MIGRATE" archive-central --runtime "$TMP/p3/opt/agent-usage-tracker")" "new-mac: new layout, left as is"

section "phase 4: view-latest"
P4="$TMP/p4/opt/agent-usage-tracker"
AGENT_USAGE_TRACKER_RUNTIME="$P4" python3 - <<PY
import sys, time; sys.path.insert(0, "$REPO_ROOT/src")
import usage_db
db = usage_db.open_account("claude")
db.execute("DROP VIEW latest")
db.execute("CREATE VIEW latest AS SELECT ts, dt, source, machine, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts FROM account_quotas ORDER BY ts DESC LIMIT 1")
now = int(time.time())
db.execute("INSERT INTO account_quotas (ts, dt, source, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts, written_ts, raw, row_hash, machine) "
           "VALUES (?, 'x', 'claude_api', 42.0, ?, 18.0, ?, ?, '{}', 'h', 'm1')", (now - 100, now - 10, now + 999, now))
PY
assert_contains "the old view replaced: a passed 5h reset reads 0%, no reset" "$(python3 "$MIGRATE" view-latest --runtime "$P4")" "latest recreated, user_version 3; it now reads ('claude_api', 'm1', 0.0, None, 18.0,"
assert_contains "re-running: unchanged" "$(python3 "$MIGRATE" view-latest --runtime "$P4")" "latest unchanged"

section "phase 4: remove-legacy"
mkdir -p "$P4/bin" "$P4/state/quota" && touch "$P4/bin/ingest-claude-statusline.sh" "$P4/state/quota/claude"
assert_contains "bin/ and the state file removed, then the empty folder" "$(python3 "$MIGRATE" remove-legacy --runtime "$P4")" "removed ['bin/', 'state/quota/claude', 'state/quota/']"
assert_file_exists "the databases untouched" "$P4/data/claude/account_quotas.db"
assert_contains "re-running: nothing" "$(python3 "$MIGRATE" remove-legacy --runtime "$P4")" "nothing (already gone)"

section "phase 5: drop-old-requests"
P5="$TMP/p5/opt/agent-usage-tracker"
AGENT_USAGE_TRACKER_RUNTIME="$P5" python3 - <<PY
import sys; sys.path.insert(0, "$REPO_ROOT/src")
import usage_db, ingest_session_usage as s
old = usage_db.connect(usage_db.db_path("claude", "sessions_usages"), usage_db.SESSIONS_SCHEMA + usage_db.CODEX_REQUESTS_SCHEMA)
s.add_requests(old, [dict(ts=1, dt="x", request_id="r1", machine="m", session_id="s", folder="/a", entrypoint="cli", model="m",
                          input_tokens=1, output_tokens=1, cache_read_tokens=0, cache_write_5m_tokens=0, cache_write_1h_tokens=0, usd=None)])
s.add_telemetry(old, {"source": "claude_otel", "observed_at": 1, "received_at": 1, "event": "api_request", "attributes": {"request_id": "r1"}})
codex = usage_db.open_sessions("codex")
PY
mkdir -p "$P5/state/transcript_reader" && printf '{}' > "$P5/state/transcript_reader/offsets.json" && printf '{}' > "$P5/state/transcript_reader/codex_offsets.json"
out="$(python3 "$MIGRATE" drop-old-requests --runtime "$P5")"
assert_contains "a copy first, with the rows" "$out" "requests 1"
assert_contains "the four dropped, the old offsets too" "$out" "dropped ['usage_5m', 'usage_requests', 'requests', 'scans', 'state/transcript_reader/offsets.json']"
assert_contains "telemetry and snapshots stay" "$out" "left: session_snapshots 0, telemetry 1"
assert_file_exists "Codex's offsets stay" "$P5/state/transcript_reader/codex_offsets.json"
assert_eq "Codex's database untouched" "requests" "$(qq "$P5/data/codex/sessions_usages.db" "SELECT name FROM sqlite_master WHERE name = 'requests'")"
assert_contains "re-running: nothing" "$(python3 "$MIGRATE" drop-old-requests --runtime "$P5")" "dropped nothing (already gone)"
mkdir -p "$P5/data/claude" && AGENT_USAGE_TRACKER_RUNTIME="$P5" JR_MACHINE_NAME=m1 python3 -c "import sys; sys.path.insert(0, '$REPO_ROOT/src'); import usage_db; usage_db.open_intermediary('claude'); usage_db.open_aggregates('claude')"
out="$(JR_MACHINE_NAME=m1 python3 "$MIGRATE" check-intermediary --runtime "$P5")"
assert_contains "check-intermediary after phase 5: the slots half only" "$out" "the old requests table is gone (phase 5)"

harness_summary
