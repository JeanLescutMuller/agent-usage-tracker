#!/bin/bash
# Unit tests for src/quota_polling/_quota_common.py's write_state_if_newer -
# the Python-side mirror of bin/ingest-claude-statusline.sh's
# write_quota_if_newer (see test_ingest_claude_statusline.sh). Both write the
# exact same FS-delimited state file format.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

QUOTA_POLLING_DIR="$REPO_ROOT/src/quota_polling"
TH_TMP2="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-quotacommon.XXXXXX")"
trap 'rm -rf "$TH_TMP2"' EXIT
SEP=$'\034'

py() {
    python3 -c "
import sys
sys.path.insert(0, '$QUOTA_POLLING_DIR')
import _quota_common
$1
"
}

state="$TH_TMP2/state/quota/claude"

section "write_state_if_newer: first write always writes"
py "
from pathlib import Path
_quota_common.write_state_if_newer(Path('$state'), 42, 1700000000, 55, 1700100000, 'API', 500)
"
IFS="$SEP" read -r v1 v2 v3 v4 v5 v6 < "$state"
assert_eq "5h pct" "42" "$v1"
assert_eq "5h reset" "1700000000" "$v2"
assert_eq "7d pct" "55" "$v3"
assert_eq "7d reset" "1700100000" "$v4"
assert_eq "source tag" "API" "$v5"
assert_eq "observed_at" "500" "$v6"

section "write_state_if_newer: returns True/False matching whether it wrote"
result="$(py "
from pathlib import Path
print(_quota_common.write_state_if_newer(Path('$state'), 1, '', 2, '', 'statusline', 400))
")"
assert_eq "an older observed_at: returns False" "False" "$result"
IFS="$SEP" read -r v1 _ _ _ v5 v6 < "$state"
assert_eq "...and does not overwrite" "42" "$v1"
assert_eq "...tag unchanged" "API" "$v5"

result="$(py "
from pathlib import Path
print(_quota_common.write_state_if_newer(Path('$state'), 99, 1700200000, 88, 1700300000, 'statusline', 600))
")"
assert_eq "a genuinely newer observed_at: returns True" "True" "$result"
IFS="$SEP" read -r v1 v2 v3 v4 v5 v6 < "$state"
assert_eq "...and does overwrite" "99" "$v1"
assert_eq "...every field" "1700200000" "$v2"
assert_eq "...tag flips" "statusline" "$v5"
assert_eq "...observed_at advances" "600" "$v6"

section "write_state_if_newer: empty resets become empty fields, not the string 'None'"
rm -rf "$TH_TMP2/state"
py "
from pathlib import Path
_quota_common.write_state_if_newer(Path('$state'), 10, None, 20, None, 'API', 100)
"
IFS="$SEP" read -r v1 v2 v3 v4 v5 v6 < "$state"
assert_eq "5h reset is empty, not 'None'" "" "$v2"
assert_eq "7d reset is empty, not 'None'" "" "$v4"

section "write_state_if_newer: creates parent directories as needed"
deep_state="$TH_TMP2/deep/does/not/exist/yet/claude"
py "
from pathlib import Path
_quota_common.write_state_if_newer(Path('$deep_state'), 1, '', 2, '', 'API', 1)
"
assert_file_exists "state file written despite missing parent dirs" "$deep_state"

section "FIELD_SEP matches the ingest script's \$'\\034'"
sep_repr="$(py "print(repr(_quota_common.FIELD_SEP))")"
assert_eq "both sides use ASCII file separator (\\x1c / \\034)" "'\x1c'" "$sep_repr"

harness_summary
