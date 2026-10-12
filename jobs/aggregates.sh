#!/bin/bash
# ~/opt/agent-usage-tracker/aggregates.sh: job agent-usage-tracker.aggregates.
# Requirements (design/DESIGN.md §6, §8):
#   - Claude transcripts -> data/claude/intermediary.db (each request once) -> the final 5-min slots ->
#     data/claude/aggregates/<machine>.db;
#     Codex session files -> data/codex/sessions_usages.db as before; Mac and VM, each for itself
#   - the Mac then sends its new slots to the VM (aggregates/chan-lescut-macbook-pro.db there); the VM sends nothing.
#     The VM unreachable: a failure, the slots go with the next run
#   - every 5 min (Mac StartInterval 300; VM OnUnitActiveSec=5min); incremental (a byte offset per file)
#   - timeout 5 min, as long as the tick: no overlap, two runs would read the same bytes (claim, lockfile)
#   - summary: its last line ("slots: 12 written (… → …), 40 requests · sent 12 to H-Frank-1")
. ~/opt/job-runner/lib.sh
send=
[ $JR_MACHINE_NAME = chan-lescut-macbook-pro ] && send=--send-to=H-Frank-1
jr_claim 5                               # the lockfile; held: SKIP "$JR_WHY"
jr_execute 5 python3 src/aggregate_slots.py $send
