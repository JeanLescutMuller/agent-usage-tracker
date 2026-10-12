#!/bin/bash
# ~/opt/agent-usage-tracker/claude-quota.sh: job agent-usage-tracker.claude-quota.
# Requirements (design/DESIGN.md §3.3, §3.4):
#   - one reading of the Claude account's quota (GET /api/oauth/usage) when the account's newest reading, any
#     source, any machine, is too old; Mac and VM, the same file, tick every 60 s
#   - the Mac syncs the readings with the VM every 2 min, both ways, before deciding (the VM cannot reach the Mac)
#   - thresholds: status line on screen here (heartbeat < 90 s): 110 s (Mac) / 290 s (VM); idle: 300 s / 480 s.
#     The VM waits for the Mac's threshold + the 2-min sync + a tick, so it polls only while the Mac sleeps
#   - VM unreachable: the Mac goes on alone; a sync failing for 30 min is a failure
#   - 429: wait its Retry-After, on this machine only (WAIT, not WAIT … all): the endpoint counts per login token,
#     one per machine - on 2026-10-10 the Mac polled fine 27 times during a VM Retry-After of 3600 s
#     (USAGE_DATA_SOURCES.md §3.5)
#   - 401 or another error: a failure
#   - timeout 1 min; two readings at once are harmless: no claim
#   - summary: the poller's last line ("5h …% · 7d …% · sync ↑2 ↓0")
#   - alert: red after 30 min without a success (dashboard settings on the VM, DESIGN §11)
. ~/opt/job-runner/lib.sh                # this machine's own pending WAIT: skipped right here
limit=290 idle=480 sync=
[ $JR_MACHINE_NAME = chan-lescut-macbook-pro ] && limit=110 idle=300 sync="--sync-with=H-Frank-1 --sync-every=110"
[ $(jr_age ~/opt/agent-statusline/state/heartbeat/claude) -lt 90 ] || limit=$idle   # a status line on screen here

# The poller: sync when due (Mac), then a poll only if the newest reading is $limit s old or more
jr_execute 1 python3 src/claude_quota_api_poller.py $sync --if-older-than=$limit
case $JR_RC in
  10) jr_write_status SKIP "$JR_LAST_LINE";;                                      # "quota fresh enough (40 s) · sync ↑1 ↓0"
  75) jr_write_status WAIT "${JR_LAST_LINE##* } 429 Retry-After";;               # its last line ends with the seconds
esac                                                                              # 0: SUCCESS "5h 42% · 7d 18%"; else FAILURE
