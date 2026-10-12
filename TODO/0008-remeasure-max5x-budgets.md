---
# ── What
id: agent-usage-tracker#8
type: one-off
title: Re-measure the Claude budgets on Max 5x after a full week
category: question
# ── Who
owner: claude
autonomy: auto_mode
# ── Urgency
priority: 7 days
estimated_work_duration: 20 min
# ── When
not_before: 2026-10-19T21:05+02:00[Europe/Zurich]
planned_at: 2026-10-20[Europe/Zurich]
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

The plan changed from Pro to Max 5x on 2026-10-07 about 16:51Z. First measure, 2026-10-11 (`adhoc_quotas_analysis/budgets.py`): a Max 5x 5-hour window holds about $202 of Claude Code's $ against $47 on Pro, **about 4.3×, not 5×**, from only 4 windows at 30% or more and no full week. The first full Max week ends 2026-10-12 19:00 UTC; the 7-day meter also jumped 7% → 18% in 9 min on 10-08, which skews this week's ratio.

## Done when

- `budgets.py` re-run with 10 or more Max 5x windows at 30%, and one full week.
- `adhoc_quotas_analysis/CONCLUSIONS.md` §2 and its run date updated; the 4.3× confirmed or corrected.
- Optional: the token-type weights (on Pro, cache reads counted 4–5× less than list price) re-measured with a fit per token column.

## History
- 2026-10-12: first full Max 5x week measured (claude): 4.1×, 7 windows at 30% of the 10 asked; kept open for the second week (ends 2026-10-19 19:00 UTC) and the 10-12 13:10 outlier; see Result
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `adhoc_quotas_analysis/budgets.py`, `adhoc_quotas_analysis/CONCLUSIONS.md` §2

## Result

2026-10-12 18:59 UTC, `budgets.py` on the VM (both machines' slots), the first full Max 5x week (10-07 16:51 → 10-12 19:00):

| | Pro | Max 5x | Max / Pro |
|---|---|---|---|
| 5-hour B, median [p10–p90] | $47.29 [33.05–50.74], n=6 | $194.17 [179.47–202.21], n=7 | 4.11 |
| 7-day B | $357 (week cut at 60%) | $1,469 (week at 69%) | 4.11 |
| 7-day B / 5-hour B | 7.55 | 7.57 | — |

Not done yet: 7 windows reached 30%, not 10. To check next time: the outlier 10-12 13:10 (B $134), and the token-type weights. Recorded in `adhoc_quotas_analysis/CONCLUSIONS.md` §2.
