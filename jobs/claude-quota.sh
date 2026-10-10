#!/bin/bash
# ~/opt/agent-usage-tracker/claude-quota.sh: job agent-usage-tracker.claude-quota.
# Requirements:
#   - one reading of the Claude account's quota (GET /api/oauth/usage); Mac and VM, the same file, tick every 60 s
#   - the Mac preferred: lower thresholds; it also takes the VM's reading when the VM polled since, and sends it its own
#     (the VM cannot reach the Mac: the Mac does both directions)
#   - status line on screen here (heartbeat < 90 s): poll when the quota is older than 110 s (Mac) / 170 s (VM);
#     idle: older than 300 s (Mac) / 360 s (VM); the quota's age from the quota file (any source, any machine)
#   - VM unreachable: the Mac goes on alone
#   - 429: wait its Retry-After, on this machine only (WAIT, not WAIT … all): the endpoint counts per login token,
#     one per machine - on 2026-10-10 the Mac polled fine 27 times during a VM Retry-After of 3600 s
#     (USAGE_DATA_SOURCES.md §3.5)
#   - 401 or another error: a failure
#   - timeout 1 min; two readings at once are harmless: no claim
#   - summary: "5h …% · 7d …%" (the poller's last line)
#   - alert: red after 30 min without a success (dashboard settings on the VM, DESIGN §11)
. ~/opt/job-runner/lib.sh                # this machine's own pending WAIT: skipped right here
limit=170 idle=360 peer=               # the VM waits a minute longer than the Mac
[ $JR_MACHINE_NAME = chan-lescut-macbook-pro ] && limit=110 idle=300 peer=--peer=H-Frank-1   # 120 s and 5 min minus 10 s of tick slack

# 1. Local checks, no network
observed=$(cut -d$'\034' -f6 ~/opt/agent-usage-tracker/state/quota/claude 2>/dev/null)   # the freshest reading's own time
[ $(jr_age ~/opt/agent-statusline/state/heartbeat/claude) -lt 90 ] || limit=$idle   # a status line on screen here
[ $((NOW-${observed:-0})) -lt $limit ] && jr_skip "quota fresh enough"

# 2. The poller: with --peer, first the VM's latest reading (fresh enough: stored here, exit 10), else a poll, sent to the VM
jr_execute 1 python3 src/claude_quota_api_poller.py $peer --fresh-within=$limit
case $JR_RC in
  10) jr_write_status SKIP "$JR_LAST_LINE";;                                      # "the VM's reading is fresh: taken"
  75) jr_write_status WAIT "${JR_LAST_LINE##* } 429 Retry-After";;               # its last line: "retry-after 1820"
esac                                                                              # 0: SUCCESS "5h 42% · 7d 18%"; else FAILURE
