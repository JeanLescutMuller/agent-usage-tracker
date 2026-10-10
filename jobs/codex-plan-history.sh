#!/bin/bash
# ~/opt/agent-usage-tracker/codex-plan-history.sh: job agent-usage-tracker.codex-plan-history.
# Requirements:
#   - Codex's plan_limit_history (ChatGPT backend); Mac only; hourly tick
#   - once per 24 h after a success; 1 h after a failure
#   - skip when Codex is not logged in (~/.codex/auth.json)
#   - timeout 2 min, far shorter than the tick: no claim
. ~/opt/job-runner/lib.sh

[ -f ~/.codex/auth.json ] || jr_skip "no ~/.codex/auth.json: Codex not logged in here"
jr_skip_if SUCCESS 86400 "fetched in the last 24 h"
jr_skip_if FAILURE 3600 "failed less than 1 h ago"
jr_execute 2 python3 src/codex_plan_history_poller.py   # its own interval code removed; 0 = stored, else failure
