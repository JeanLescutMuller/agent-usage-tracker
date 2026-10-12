---
# ── What
id: agent-usage-tracker#9
type: one-off
title: Poll at the active pace when Claude sessions run without agent-statusline
category: feature
# ── Who
owner: claude
autonomy: approval_needed
# ── Urgency
priority: someday
estimated_work_duration: 60 min
# ── When
not_before: 
planned_at: 
not_after: 
# ── Status and claim
status: todo
claimed_since:
claimed_by:
# ── Links
depends_on: []
todoist_id:
# ── Origin
created_at: 2026-10-11T00:55+02:00[Europe/Zurich]
created_by: chan-lescut-macbook-pro, claude, 18992588-d956-40f0-84e4-45868a226a51
---
## Context

Parked 2026-10-04. `claude_quota_api_poller.py` polls at its active pace only while agent-statusline's heartbeat says a status line is on screen; without agent-statusline it stays at its idle pace even while Claude sessions are active. A second signal, recently modified transcripts under `~/.claude/projects/**/*.jsonl`, would fix that, the way the Codex poller uses Codex's session files. Less needed since the status line's readings are stored on every render.

## Done when

- Jean has agreed it is worth it.
- With agent-statusline stopped and a Claude session active, the job polls at its active pace (job-runner status).

## History
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `src/claude_quota_api_poller.py`, `jobs/claude-quota.sh`

## Result
