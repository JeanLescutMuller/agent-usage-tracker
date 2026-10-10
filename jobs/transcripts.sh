#!/bin/bash
# ~/opt/agent-usage-tracker/transcripts.sh: job agent-usage-tracker.transcripts.
# Requirements:
#   - Claude Code transcripts and Codex session files → one row per request; Mac and VM, each for itself
#   - every 5 min (Mac StartInterval 300; VM OnUnitActiveSec=5min); incremental (a byte offset per file)
#   - timeout 5 min, as long as the tick: no overlap, two runs would read the same bytes (claim, lockfile)
#   - summary: its last line ("requests added 12; …")
. ~/opt/job-runner/lib.sh
jr_claim 5                               # the lockfile; held: SKIP "$JR_WHY"
jr_execute 5 python3 src/transcript_reader.py
