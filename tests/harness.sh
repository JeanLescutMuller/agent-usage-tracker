#!/bin/bash
# Minimal bash test harness: assert_* helpers, isolated fixtures, a summary.
# No external framework - styled after utils.sh's color/step conventions so
# the two feel like one project. Each test_*.sh sources this, then must call
# harness_summary at the end (exits 0 if everything passed, 1 otherwise).
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
source "$REPO_ROOT/utils.sh"

TH_PASS=0
TH_FAIL=0

_th_report() {
    local ok="$1" desc="$2" detail="${3:-}"
    if [ "$ok" -eq 0 ]; then
        TH_PASS=$((TH_PASS + 1))
        printf '  \033[0;32m✓\033[0m %s\n' "$desc"
    else
        TH_FAIL=$((TH_FAIL + 1))
        printf '  \033[0;31m✗\033[0m %s\n' "$desc"
        [ -n "$detail" ] && printf '      %s\n' "$detail"
    fi
}

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        _th_report 0 "$desc"
    else
        _th_report 1 "$desc" "expected [$expected], got [$actual]"
    fi
}

assert_ne() {
    local desc="$1" not_expected="$2" actual="$3"
    if [ "$not_expected" != "$actual" ]; then
        _th_report 0 "$desc"
    else
        _th_report 1 "$desc" "expected something other than [$not_expected]"
    fi
}

assert_contains() {
    local desc="$1" haystack="$2" needle="$3"
    case "$haystack" in
        *"$needle"*) _th_report 0 "$desc" ;;
        *) _th_report 1 "$desc" "expected to find [$needle] in [$haystack]" ;;
    esac
}

assert_not_contains() {
    local desc="$1" haystack="$2" needle="$3"
    case "$haystack" in
        *"$needle"*) _th_report 1 "$desc" "did not expect to find [$needle] in [$haystack]" ;;
        *) _th_report 0 "$desc" ;;
    esac
}

assert_match() {
    local desc="$1" haystack="$2" pattern="$3"
    if [[ "$haystack" =~ $pattern ]]; then
        _th_report 0 "$desc"
    else
        _th_report 1 "$desc" "expected [$haystack] to match /$pattern/"
    fi
}

assert_file_exists() {
    local desc="$1" path="$2"
    if [ -e "$path" ]; then _th_report 0 "$desc"; else _th_report 1 "$desc" "missing: $path"; fi
}

assert_file_missing() {
    local desc="$1" path="$2"
    if [ ! -e "$path" ]; then _th_report 0 "$desc"; else _th_report 1 "$desc" "should not exist: $path"; fi
}

assert_status() {
    local desc="$1" expected="$2" actual="$3"
    assert_eq "$desc" "$expected" "$actual"
}

# th_run cmd... - runs a command, capturing stdout/stderr/exit code into
# TH_OUT/TH_ERR/TH_STATUS. Never lets set -e in the caller abort the suite.
th_run() {
    local err_file
    err_file="$(mktemp "${TMPDIR:-/tmp}/th-err.XXXXXX")"
    TH_OUT="$("$@" 2>"$err_file")"
    TH_STATUS=$?
    TH_ERR="$(cat "$err_file")"
    rm -f "$err_file"
}

harness_summary() {
    printf '\n'
    if [ "$TH_FAIL" -eq 0 ]; then
        printf '\033[0;32m%d passed\033[0m\n' "$TH_PASS"
        exit 0
    else
        printf '\033[0;32m%d passed\033[0m, \033[0;31m%d failed\033[0m\n' "$TH_PASS" "$TH_FAIL"
        exit 1
    fi
}
