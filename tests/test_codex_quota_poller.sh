#!/bin/bash
# Tests for src/codex_quota_api_poller.py's skips: each exits 10 after its
# "skip: ..." line, so its job-runner entrypoint records a Skip, not a
# success. No codex binary, no network.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

RT="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-codexq.XXXXXX")"
trap 'rm -rf "$RT"' EXIT

section "no codex binary -> skip, exit 10"
out="$(AGENT_USAGE_TRACKER_RUNTIME="$RT" python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/src')
import codex_quota_api_poller as m
m.CODEX_BIN = '$RT/no-codex'
m.main()
")"
assert_eq "exit 10" "10" "$?"
assert_eq "its skip line last" "skip: no codex binary ($RT/no-codex)" "$out"
assert_file_missing "nothing stored" "$RT/data/codex/account_quotas.db"

harness_summary
