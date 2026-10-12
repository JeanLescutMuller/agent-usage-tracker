---
# ── What
id: agent-usage-tracker#2
type: one-off
title: Check that the VM polls Claude's quota while the Mac sleeps, and only then
category: ops
# ── Who
owner: claude
autonomy: auto_mode
# ── Urgency
priority: 48h
estimated_work_duration: 10 min
# ── When
not_before: 2026-10-11T09:00+02:00[Europe/Zurich]
planned_at: 2026-10-11[Europe/Zurich]
not_after: 
# ── Status and claim
status: done
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

Since the 2-min sync (2026-10-10), the VM's `claude-quota` job polls only when no reading is fresh enough: Mac 110 / 300 s, VM 290 / 480 s (`design/DESIGN.md` §3.4). So far the VM has never polled while the Mac was awake, as designed. Its polling while the Mac sleeps has not been seen yet.

## Done when

- After a night with the Mac asleep: `SELECT machine, COUNT(*), MIN(dt), MAX(dt) FROM account_quotas WHERE source = 'claude_api' AND ts > <the night's start> GROUP BY 1` shows `H-Frank-1` readings during the night and none while the Mac was awake.
- The result is written below.

## History
- 2026-10-12: done (claude): the VM takes over as designed, but its polls fail (401: its login token expired); see Result
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `jobs/claude-quota.sh`, `src/claude_quota_api_poller.py`

## Result

Checked 2026-10-12 on the VM's `account_quotas.db` and `poll_errors`, since 2026-10-10 22:00 UTC (the sync live):

| | Value |
|---|---|
| Mac readings | 4,527; the Mac was silent more than 10 min only twice: 10-11 08:05 → 08:23 and 08:23 → 08:37 |
| VM polls while the Mac was awake | 0 (as designed) |
| VM polls during those silences | it **tried**: 08:15 and 08:16 → **401 Unauthorized**, 08:18 → 429, Retry-After 3,600 s; also 10-10 23:25–23:27 → 401 |
| VM readings stored | 0 |

The takeover logic works: the VM polled about 10 min after the Mac's last reading. But the VM's login token has expired: only Claude Code renews it, and Claude Code last ran on the VM on 2026-10-08 (its last slot). So while the Mac sleeps, no reading is taken at all: the gap accepted on 2026-10-10 (`USAGE_DATA_REFERENCE.md` §5.8). It closes only if something renews the VM's token without sending a message: `TODO/0010`.
