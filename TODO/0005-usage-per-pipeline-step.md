---
# ── What
id: agent-usage-tracker#5
type: one-off
title: Split a project's $ by pipeline step (auto-apply: judging, drafting, browser…)
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

Requested by Jean on 2026-10-07 from auto-apply. The split by project and kind of use is built (`adhoc_quotas_analysis/usage_split.py --by project|kind`). What is left is the split **inside** test and scheduled runs, by step. `slots` cannot give it: it needs each session's first prompt, which only the transcripts hold.

The example computed by hand on 2026-10-07 (auto-apply, list-price estimate): judging jobs 51 × $0.035, apply in browser 4 × ~$0.20, drafting 5 × $0.074, Sapling detector 1 × $0.29, fact-check review 4 × $0.045, visual check 2 × $0.035, Gmail check 9 × $0.007. It grouped by the first words of each session's first prompt.

Better than prompt prefixes: a project labels its own sessions (auto-apply passing a step name). Agree on that convention with Jean before asking other projects to adopt it. Privacy: those transcripts hold CVs, job ads and email bodies; copy only usage fields, never text.

## Done when

- Jean has chosen: prompt prefixes, or a label the projects pass.
- A report gives, per step, sessions, requests, tokens and Claude Code's $ (no $ computed: tokens where it is missing, see #4).

## History
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `adhoc_quotas_analysis/usage_split.py`, `FOR_READERS.md`

## Result
