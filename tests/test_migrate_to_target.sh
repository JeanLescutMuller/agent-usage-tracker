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
    g.add_row(db, "claude", {"ts": int(ts), "source": "claude_api", "api": {"five_hour": {"utilization": 1.0}}}, update_state=False)
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
assert_eq "...the latest view names the machine, version 2" "200	2" \
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

harness_summary
