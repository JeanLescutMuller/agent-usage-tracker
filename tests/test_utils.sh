#!/bin/bash
# Smoke tests for utils.sh's echo/color helpers, shared by install.sh and
# the Codex patch script.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"
source "$REPO_ROOT/utils.sh"

section "each helper prints its message"
for fn in step ok installed skip fail; do
    out="$("$fn" "hello-$fn")"
    assert_contains "$fn() includes its message" "$out" "hello-$fn"
done

harness_summary
