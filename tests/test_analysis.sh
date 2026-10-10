#!/bin/bash
# Tests for adhoc_quotas_analysis/ (load.py, windows.py, budgets.py, usage_split.py): on a fixture runtime
# whose readings and slots are written through the gates, each question's answer is the one built in.
#   window A  09-15 08:00 -> 13:00: 10% / 20% / 15% (an older reading: dropped) / 30%, $1 before each,
#             so B = $10, R2 1; the 7-day meter +2 -> 5-hour windows per week 10; 3% of its tokens
#             without $ (a scheduled run): under the 5% allowed
#   gap       13:00 -> 16:00, a server reading saying "no 5-hour window" at 14:00
#   window B  16:00 -> 21:00, opened by a request at 16:05; two readings only: no B
#   plan      a 7-day window across the 2026-10-07 plan change: cut in two
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-analysis.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export AGENT_USAGE_TRACKER_RUNTIME="$TMP/rt" JR_MACHINE_NAME=m1
A="$REPO_ROOT/adhoc_quotas_analysis"

python3 - "$REPO_ROOT/src" <<'PY'
import sys; sys.path.insert(0, sys.argv[1])
import usage_db, ingest_account_quota as q, ingest_aggregates as g
T, END7 = 1789459200, 1790017200          # 2026-09-15T08:00Z (a Tuesday); 2026-09-21T19:00Z (a Monday)
db = usage_db.open_account("claude")
def reading(t, p5, end5, p7, end7=END7):
    w = lambda p, e: {"utilization": p, "resets_at": None if e is None else usage_db.iso(e)}
    q.add_row(db, "claude", {"ts": t, "source": "claude_api", "machine": "m1",
                             "api": {"five_hour": w(p5, end5), "seven_day": w(p7, end7)}})
A5, B5 = T + 5 * 3600, T + 13 * 3600
for h, p5, p7 in ((1, 10, 1), (2, 20, 2), (2.5, 15, 2), (3, 30, 3)):
    reading(T + int(h * 3600), p5, A5 - 1, p7, END7 + 1)          # reset times jitter by a second
reading(T + 6 * 3600, 0, None, 3)
reading(T + 9 * 3600, 10, B5, 4)
reading(T + 10 * 3600, 20, B5, 5)
reading(1791388800, 0, None, 60, 1791831600)                    # 10-07 16:00, pro; the week ends 10-12 19:00
reading(1791393600, 1, 1791408000, 1, 1791831600)               # 10-07 17:20, max5x, its 5-hour window ends 21:20
def slot(t, folder, entrypoint, usd, tokens=1000):
    return dict(ts=t, dt=usage_db.iso(t), machine="m1", folder=folder, entrypoint=entrypoint, model="claude-opus-5-5",
                requests=1, input_tokens=0, output_tokens=0, cache_read_tokens=tokens, cache_write_5m_tokens=0,
                cache_write_1h_tokens=0, cache_write_unknown_tokens=0, usd=usd,
                tokens_without_usd=0 if usd is not None else tokens, written_ts=t)
g.add_slots(usage_db.open_aggregates("claude"), [
    slot(T + 1800, "/Users/x/dev/alpha", "cli", 1.0), slot(T + 5400, "/Users/x/dev/alpha/sub", "cli", 1.0),
    slot(T + 9000, "/Users/x/dev/alpha", "cli", 1.0), slot(T + 14400, "/Users/x/dev/alpha", "cli", 1.0),
    slot(T + 9000, "/Users/x/opt/beta", "sdk-py", None, 100),
    slot(T + 8 * 3600 + 300, "/private/tmp/claude-502/-Users-x-dev-gamma/u1/scratchpad", "cli", 2.0),
    dict(slot(T + 600, "/Users/x/dev/alpha", "cli", None, 0), model="<synthetic>")])
PY
# The databases' content (a reader's close may checkpoint the WAL into the file: same rows, other bytes).
md5() { python3 -c 'import sqlite3, sys, hashlib; print(hashlib.md5("".join("".join(sqlite3.connect(p).iterdump()) for p in sys.argv[1:]).encode()).hexdigest())' "$TMP/rt/data/claude/"*.db; }
before="$(md5)"

section "Q1 windows.py: windows open at the first request; idle gaps between"
out="$(python3 "$A/windows.py" 2>&1)"
assert_contains "two 5-hour windows, one idle gap" "$out" "pairs 1: back to back 0, within 20 min 0, idle gaps 1, overlaps 0"
assert_contains "the gap holds the server's 'no window' reading" "$out" "with a server 'no window' reading 1/1"
assert_contains "window B opened by the request at 16:05: first activity 5 min after its start" "$out" "09-15 13:00  09-15 16:00    3.00         1                   1             0         5"
assert_contains "the 7-day end is a Monday, snapped to the grid" "$out" "09-21 19:00  Mon 19:00"
assert_contains "the plan change cuts the 7-day window" "$out" "max5x  10-07 16:51  10-12 19:00"

section "Q2 budgets.py: B per window, windows per week"
out="$(python3 "$A/budgets.py" 2>&1)"
assert_contains "window A: \$3 at 30%, B \$10, R2 1 (the older 15% reading dropped)" "$out" "pro    09-15 08:00  09-15 13:00      30     3.00       0.0           100     10.00   1.00"
assert_contains "window B: two readings, no B" "$out" "fewer than 3 readings at 5%+"
assert_contains "the summary per plan" "$out" "pro: 5-hour B \$10.00 [10.00-10.00] (n=1), 1% = \$0.10"
assert_contains "A: 5-hour +20 for 7-day +2, B: +10 for +1: 10 windows per week" "$out" "5-hour windows per week: median 10.00 [10.00-10.00], all gains summed 10.00 (n=2)"

section "Q3 usage_split.py: \$ per window by project and kind"
out="$(python3 "$A/usage_split.py" --span 5h 2>&1)"
assert_contains "window A: \$4 (the \$1 after its last reading counts too)" "$out" "pro 09-15 08:00 -> 09-15 13:00, peak 30%: \$4.00"
assert_contains "a subfolder counts for its repo" "$out" "  alpha                                   4.00 100.0%        0.0          0.0         4"
assert_contains "a scratchpad counts for its repo" "$out" "  gamma                                   2.00 100.0%"
assert_not_contains "a <synthetic> slot is left out" "$out" "      5"
out="$(python3 "$A/usage_split.py" --span 5h --by kind 2>&1)"
assert_contains "an SDK run under ~/opt is a scheduled run, its tokens without \$" "$out" "  scheduled run                           0.00   0.0%        0.0          0.0         1"
assert_contains "--span day" "$(python3 "$A/usage_split.py" --span day 2>&1)" "pro 2026-09-15: \$6.00"

section "load.py"
assert_eq "project()" "alpha beta ~ ~/dev ? /tmp/x" "$(cd "$A" && python3 -c "import load; print(*[load.project(f) for f in ('/Users/x/dev/alpha/a', '/home/x/opt/beta', '/Users/x', '/Users/x/dev', '', '/tmp/x')])")"
assert_eq "kind()" "development|test run|scheduled run|?" "$(cd "$A" && python3 -c "import load; print(load.kind('/Users/x/dev/a', 'cli'), load.kind('/Users/x/dev/a', 'sdk-cli'), load.kind('/Users/x/opt/a', 'sdk-py'), load.kind('', ''), sep='|')")"
assert_eq "the databases are unchanged: every script only reads" "$before" "$(md5)"

harness_summary
