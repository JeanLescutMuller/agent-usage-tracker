#!/bin/bash
# Tests for adhoc_quotas_analysis/rename_sources.py: the one-time rename of
# the pollers' `source` values ("claude" -> "claude_api", "codex" ->
# "codex_app_server") and of state/quota/claude's source field, against
# fixture files in a temp runtime dir.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

SCRIPT="$REPO_ROOT/adhoc_quotas_analysis/rename_sources.py"
RT="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-rename.XXXXXX")"
trap 'rm -rf "$RT"' EXIT
mkdir -p "$RT/data/claude" "$RT/data/codex" "$RT/logs" "$RT/state/quota"

# Both spacings writers have used, a push row that must not match, a nested
# "source" inside a payload, and an unparsable line.
cat > "$RT/data/claude/account.jsonl" <<'ROWS'
{"ts": 1, "iso": "x", "source": "claude", "api": {"five_hour": {"utilization": 5}, "source": "claude"}, "error": null}
{"ts":2,"source":"claude_statusline","observed_at":2,"five_hour_pct":5}
{"ts":3,"source":"claude","api":null,"error":null}
not json at all
ROWS
printf '%s\n' '{"ts": 4, "source": "claude", "api": null, "error": {"stage": "http", "status": 429}}' > "$RT/logs/claude-poll-errors.jsonl"
cat > "$RT/data/codex/account.jsonl" <<'ROWS'
{"ts": 1, "source": "codex", "codex_rate_limits": {}, "error": null}
{"ts": 2, "source": "codex_plan_limit_history", "plan_limit_history": {}, "error": null}
ROWS
printf '15\0341790980800\03418\0341791226800\034API\0341790963400\n' > "$RT/state/quota/claude"
before_claude="$(cat "$RT/data/claude/account.jsonl")"

section "first run -> only the top-level source value changes"
th_run python3 "$SCRIPT" --runtime-dir "$RT"
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "claude data counts" "$TH_OUT" "data/claude/account.jsonl: renamed 2 of 4 lines - reconciles"
assert_contains "claude error log counts" "$TH_OUT" "logs/claude-poll-errors.jsonl: renamed 1 of 1 lines - reconciles"
assert_contains "codex counts" "$TH_OUT" "data/codex/account.jsonl: renamed 1 of 2 lines - reconciles"
assert_eq "claude data: renamed in place, spacing kept, push row, nested key and bad line untouched" \
    '{"ts": 1, "iso": "x", "source": "claude_api", "api": {"five_hour": {"utilization": 5}, "source": "claude"}, "error": null}
{"ts":2,"source":"claude_statusline","observed_at":2,"five_hour_pct":5}
{"ts":3,"source":"claude_api","api":null,"error":null}
not json at all' "$(cat "$RT/data/claude/account.jsonl")"
assert_eq "codex: app-server rows renamed, plan history untouched" \
    '{"ts": 1, "source": "codex_app_server", "codex_rate_limits": {}, "error": null}
{"ts": 2, "source": "codex_plan_limit_history", "plan_limit_history": {}, "error": null}' "$(cat "$RT/data/codex/account.jsonl")"
IFS=$'\034' read -r _ _ _ _ st_source st_observed < "$RT/state/quota/claude"
assert_eq "state source API -> claude_api" "claude_api" "$st_source"
assert_eq "state observed_at untouched" "1790963400" "$st_observed"
archive="$(ls "$RT"/data/_archive/claude-account.jsonl.*.pre-rename 2>/dev/null | head -1)"
assert_file_exists "claude original archived" "$archive"
assert_eq "archive is the untouched original" "$before_claude" "$(cat "$archive" 2>/dev/null)"

section "second run -> no-op"
th_run python3 "$SCRIPT" --runtime-dir "$RT"
assert_status "exits 0" 0 "$TH_STATUS"
assert_contains "nothing left in claude data" "$TH_OUT" "data/claude/account.jsonl: no old source value, nothing to do"
assert_contains "nothing left in the state file" "$TH_OUT" "state/quota/claude: no old source value, nothing to do"
assert_eq "no further archives" "3" "$(ls "$RT/data/_archive" | wc -l | tr -d ' ')"

harness_summary
