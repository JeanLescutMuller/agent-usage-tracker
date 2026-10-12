---
# ── What
id: agent-usage-tracker#4
type: one-off
title: Give auto-apply's Agent SDK sessions the telemetry keys, so their slots get a $
category: feature
# ── Who
owner: claude
autonomy: approval_needed
# ── Urgency
priority: 30 days
estimated_work_duration: 30 min
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

auto-apply starts its Agent SDK sessions with `setting_sources=[]`, so the telemetry `env` keys of `~/.claude/settings.json` are not applied and the OTLP receiver gets nothing from them. Their slots carry tokens only (`tokens_without_usd`): on 2026-10-11, 2.0 M tokens of test runs and 0.3 M of scheduled runs had no $ (`adhoc_quotas_analysis/CONCLUSIONS.md` §3). The change is in auto-apply (pass the keys in the SDK's `env`), not here.

## Done when

- Jean has agreed to the change in auto-apply.
- After auto-apply's next run: its slots (`entrypoint = 'sdk-py'`) have `usd` and `tokens_without_usd = 0`.

## History
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `FOR_READERS.md`, `src/merge_claude_env.py` (the keys)
- concern C7 of `design/OPEN_QUESTIONS.md`

## Result
