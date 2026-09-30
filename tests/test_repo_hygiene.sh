#!/bin/bash
# Repo-wide checks that don't belong to any one file: every script parses,
# shellcheck passes where available, and the boundary with agent-statusline
# documented in README.md's "Contract with agent-statusline" holds - this
# repo never sources or runs anything from it, and reads exactly one thing
# from its runtime tree: the heartbeat files.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

section "every shell script parses (bash -n)"
while IFS= read -r -d '' f; do
    rel="${f#"$REPO_ROOT"/}"
    th_run bash -n "$f"
    assert_status "$rel has valid syntax" 0 "$TH_STATUS"
done < <(find "$REPO_ROOT" -name '*.sh' -not -path '*/.git/*' -print0)

section "every Python script compiles (python3 -m py_compile)"
while IFS= read -r -d '' f; do
    rel="${f#"$REPO_ROOT"/}"
    th_run python3 -m py_compile "$f"
    assert_status "$rel has valid syntax" 0 "$TH_STATUS"
done < <(find "$REPO_ROOT/adhoc_quotas_analysis" "$REPO_ROOT/src" -name '*.py' -not -path '*/__pycache__/*' -print0)

section "shellcheck (if available)"
if command -v shellcheck >/dev/null 2>&1; then
    while IFS= read -r -d '' f; do
        rel="${f#"$REPO_ROOT"/}"
        th_run shellcheck -x "$f"
        assert_status "$rel passes shellcheck" 0 "$TH_STATUS"
    done < <(find "$REPO_ROOT" -name '*.sh' -not -path '*/.git/*' -not -path '*/tests/*' -print0)
else
    section "  (skipped: shellcheck not installed)"
fi

section "boundary with agent-statusline: only its heartbeat files are read"
# Code (not docs) that names agent-statusline's runtime tree must only be
# reaching for state/heartbeat/. Comment lines are ignored.
other_refs="$(grep -rn 'opt.*agent-statusline\|"agent-statusline"' "$REPO_ROOT/src" "$REPO_ROOT/bin" \
    --include='*.py' --include='*.sh' 2>/dev/null \
    | grep -v -e '^[^:]*:[0-9]*: *#' -e 'heartbeat' || true)"
assert_eq "no code path into agent-statusline other than state/heartbeat/" "" "$other_refs"
sources_statusline="$(grep -rln 'src/statusline\|providers/' "$REPO_ROOT/src" "$REPO_ROOT/bin" "$REPO_ROOT/install.sh" "$REPO_ROOT/uninstall.sh" 2>/dev/null || true)"
assert_eq "nothing references agent-statusline's source tree" "" "$sources_statusline"

harness_summary
