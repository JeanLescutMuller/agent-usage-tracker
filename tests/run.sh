#!/bin/bash
# Runs every tests/test_*.sh, aggregates pass/fail, prints a summary.
# Each test file is also independently runnable: bash tests/test_x.sh
#
# Slowest files: test_provider_codex.sh polls the real carousel rotation
# (bounded, up to ~1 minute total across its sections) - the rest finish in
# well under a second each. Total suite runtime is usually under a minute.
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$TESTS_DIR/../utils.sh"

total_pass=0
total_fail=0
failed_files=()

for f in "$TESTS_DIR"/test_*.sh; do
    name="$(basename "$f")"
    step "$name"
    output="$(bash "$f" 2>&1)"
    status=$?
    printf '%s\n' "$output"
    pass_n="$(printf '%s\n' "$output" | grep -c '✓' || true)"
    fail_n="$(printf '%s\n' "$output" | grep -c '✗' || true)"
    total_pass=$((total_pass + pass_n))
    total_fail=$((total_fail + fail_n))
    [ "$status" -eq 0 ] || failed_files+=("$name")
done

echo ""
if [ "$total_fail" -eq 0 ] && [ "${#failed_files[@]}" -eq 0 ]; then
    echo -e "${GREEN}All tests passed: ${total_pass}${NC}"
    exit 0
else
    echo -e "${RED}${total_fail} failing assertion(s)${NC}"
    [ "${#failed_files[@]}" -gt 0 ] && echo -e "${RED}Failed files: ${failed_files[*]}${NC}"
    exit 1
fi
