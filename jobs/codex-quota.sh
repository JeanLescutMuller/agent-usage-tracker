#!/bin/bash
# ~/opt/agent-usage-tracker/codex-quota.sh: job agent-usage-tracker.codex-quota.
# Requirements:
#   - one reading of the Codex account's quota (codex app-server); Mac only (no Codex on the VM); tick every 60 s
#   - the poller keeps its own rules: skip while a Codex session file is fresher than 5 min; poll every tick while a
#     status line is on screen, else every 5 min; skip without a codex binary
#   - its skip is a Skip, not a success: it exits 10 after its "skip: …" line (this project's code)
#   - timeout 1 min; two readings at once are harmless: no claim
. ~/opt/job-runner/lib.sh
jr_execute 1 python3 src/codex_quota_api_poller.py
[ $JR_RC = 10 ] && jr_write_status SKIP "$JR_LAST_LINE"
