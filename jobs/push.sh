#!/bin/bash
# ~/opt/agent-usage-tracker/push.sh: job agent-usage-tracker.push.
# Requirements:
#   - this machine's new database rows to the central store on the VM; Mac and VM (the VM locally, no ssh)
#   - every 5 min (Mac StartInterval 300; VM OnUnitActiveSec=5min)
#   - a failed push is retried by the next run (watermarks)
#   - timeout 5 min, as long as the tick: no overlap, two runs would send the same rows (claim, lockfile)
#   - summary: its last line ("pushed 42 rows from … in 0.8 s")
. ~/opt/job-runner/lib.sh
jr_claim 5                               # the lockfile; held: SKIP "$JR_WHY"
jr_execute 5 python3 src/push_to_central.py
