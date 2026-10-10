#!/bin/bash
# Tests for adhoc_quotas_analysis/migrate_to_target.py, the hand-run steps of
# design/MIGRATION_PLAN.md. backup: every database copied whole and checked,
# other files kept, _archive and WAL side files left out, a log line per run.
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

harness_summary
