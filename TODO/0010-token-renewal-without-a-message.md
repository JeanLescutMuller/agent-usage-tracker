---
# ── What
id: agent-usage-tracker#10
type: one-off
title: Test whether a Claude Code command that sends no message renews an expired token
category: question
# ── Who
owner: claude
autonomy: approval_needed
# ── Urgency
priority: someday
estimated_work_duration: 20 min
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

Accepted on 2026-10-10 (`doc/design.md` P15): no Claude reading is stored while Claude Code runs on neither machine for more than 8 h. A keepalive message was rejected: any message opens a 5-hour window. Not tried: whether `claude auth status` or `claude doctor`, which send no message, renew the access token. If one does, a job running it every 6 h on the VM keeps its poller alive without opening a window. Related: #1 (the refresh token's fixed expiry).

## Done when

- Once the VM's token has expired (401 in its `poll_errors`), run one of those commands there and check whether `~/.claude/.credentials.json`'s `expiresAt` moved. Result written below.

## History
- 2026-10-11: created (claude), from `TODO.md` (merged into `TODO/` and removed)

## Links
- `doc/design.md` P15, `doc/upstream.md` §3.5

## Result
