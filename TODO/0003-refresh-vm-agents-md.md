---
# ── What
id: agent-usage-tracker#3
type: one-off
title: Refresh the VM's ~/AGENTS.md: it still describes state/quota/claude
category: docs
# ── Who
owner: claude
autonomy: approval_needed
# ── Urgency
priority: 7 days
estimated_work_duration: 10 min
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

The VM's `~/AGENTS.md` (Oct 9) still describes the old `state/quota/claude` file, gone since migration phase 4 (the latest reading is the view `latest`). The template (`bootstrap-home` `17404ef`) and the Mac's copy are current; the VM's copy is refreshed by the next `bootstrap-home` run there.

## Done when

- Jean has agreed to a `bootstrap-home` run on the VM (another project's deploy).
- `ssh H-Frank-1 grep -c state/quota/claude ~/AGENTS.md` prints 0.

## History
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `~/dev/bootstrap-home/files/home_AGENTS.md` (the template)

## Result
