#!/bin/bash
# The Claude statusline's entry point into agent-usage-tracker: the separate
# agent-statusline project pipes its raw stdin payload here, unchanged, on
# every render, before it reads state/quota/claude back. The path is the
# contract (README.md's "Contract with agent-statusline"); everything else
# lives in src/statusline_payload_reader.py.
#
# Runs in the foreground, so the render that brings a new reading also
# displays it. `python -S` (no site-packages: the reader only needs the
# standard library) keeps this at about 28 ms on a live database (2026-10-08;
# the bash/jq ingest it replaced took about 73 ms).
#
# Always exits 0 and prints nothing.
#
# Usage: <claude statusline payload JSON> | ingest-claude-statusline.sh
set -uo pipefail

# install.sh replaces __PYTHON3__ with the absolute interpreter path, since a
# statusline's PATH is not always the user's login PATH.
python="__PYTHON3__"
case "$python" in __*) python=python3 ;; esac
# ${BASH_SOURCE[0]%/*} rather than $(cd ... && pwd): no subshell on every render.
"$python" -S "${BASH_SOURCE[0]%/*}/../src/statusline_payload_reader.py" >/dev/null 2>&1
exit 0
