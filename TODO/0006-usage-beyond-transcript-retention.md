---
# ── What
id: agent-usage-tracker#6
type: one-off
title: Keep usage history beyond the transcripts' retention
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

Claude Code deletes transcripts after `cleanupPeriodDays` (365 on the Mac). The slots keep Claude's aggregates for good, but the per-request rows in `intermediary/` are rebuilt from the transcripts and would lose their oldest requests. Codex's session files (per-turn tokens and quota snapshots) follow Codex's own retention rules, not checked yet. Proposed if history beyond a year matters: a nightly extraction of the usage records only (a few MB instead of ~517 MB of transcripts).

## Done when

- Codex's session-file retention is checked and written below.
- A recommendation for Jean: needed or not, and for what.

## History
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `USAGE_DATA_REFERENCE.md` §5.7 and §7

## Result
