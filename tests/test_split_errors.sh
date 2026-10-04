#!/bin/bash
# Tests for adhoc_quotas_analysis/split_errors.py: the one-time move of
# failed-poll rows out of data/<agent>/account.jsonl into
# logs/<agent>-poll-errors.jsonl, against fixture files in a temp runtime dir.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SCRIPT="$REPO_ROOT/adhoc_quotas_analysis/split_errors.py"
RT="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-splitErr.XXXXXX")"
trap 'rm -rf "$RT"' EXIT
mkdir -p "$RT/data/claude" "$RT/data/codex" "$RT/logs"

# Claude: a reading, a 429, a push row, an unparsable line, a network error.
cat > "$RT/data/claude/account.jsonl" <<'EOF'
{"ts": 1, "source": "claude", "api": {"five_hour": {"utilization": 5}}, "error": null}
{"ts": 2, "source": "claude", "api": null, "error": {"stage": "http", "status": 429}}
{"ts":3,"source":"claude_statusline","observed_at":3,"five_hour_pct":5}
not json at all
{"ts": 4, "api": null, "error": {"stage": "network"}}
EOF
# An error the new code already wrote to the error log.
printf '%s\n' '{"ts": 9, "source": "claude", "error": {"stage": "http", "status": 429}}' > "$RT/logs/claude-poll-errors.jsonl"
# Codex: one reading, one timeout.
cat > "$RT/data/codex/account.jsonl" <<'EOF'
{"ts": 1, "source": "codex", "codex_rate_limits": {}, "error": null}
{"ts": 2, "source": "codex", "codex_rate_limits": null, "error": {"stage": "timeout"}}
EOF
before_claude="$(cat "$RT/data/claude/account.jsonl")"

section "first run -> error rows moved, everything else kept in order, originals archived"
th_run python3 "$SCRIPT" --runtime-dir "$RT"
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "claude reconciles" "$TH_OUT" "claude: archived 5 lines"
assert_contains "claude counts" "$TH_OUT" "kept 3, moved 2"
assert_contains "codex counts" "$TH_OUT" "kept 1, moved 1"
assert_not_contains "nothing fails to reconcile" "$TH_OUT" "DOES NOT RECONCILE"
assert_eq "claude data keeps the reading, the push row and the unparsable line, in order" \
    '{"ts": 1, "source": "claude", "api": {"five_hour": {"utilization": 5}}, "error": null}
{"ts":3,"source":"claude_statusline","observed_at":3,"five_hour_pct":5}
not json at all' "$(cat "$RT/data/claude/account.jsonl")"
assert_eq "error log: moved rows byte-identical, then the rows it already held" \
    '{"ts": 2, "source": "claude", "api": null, "error": {"stage": "http", "status": 429}}
{"ts": 4, "api": null, "error": {"stage": "network"}}
{"ts": 9, "source": "claude", "error": {"stage": "http", "status": 429}}' "$(cat "$RT/logs/claude-poll-errors.jsonl")"
assert_eq "codex error log" '{"ts": 2, "source": "codex", "codex_rate_limits": null, "error": {"stage": "timeout"}}' \
    "$(cat "$RT/logs/codex-poll-errors.jsonl")"
archive="$(ls "$RT"/data/_archive/claude-account.jsonl.* 2>/dev/null | head -1)"
assert_file_exists "claude original archived" "$archive"
assert_eq "archive is the untouched original" "$before_claude" "$(cat "$archive" 2>/dev/null)"

section "second run -> no-op"
th_run python3 "$SCRIPT" --runtime-dir "$RT"
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "claude: nothing to do" "$TH_OUT" "claude: no error rows, nothing to do"
assert_contains "codex: nothing to do" "$TH_OUT" "codex: no error rows, nothing to do"
assert_eq "no second archive" "2" "$(ls "$RT/data/_archive" | wc -l | tr -d ' ')"

harness_summary
