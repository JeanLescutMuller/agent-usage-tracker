---
# ── What
id: agent-usage-tracker#1
type: one-off
title: Verify that the Claude refresh token expires on a fixed date, whatever the use
category: question
# ── Who
owner: claude
autonomy: approval_needed
# ── Urgency
priority: 7 days
estimated_work_duration: 20 min
# ── When
not_before: 2026-10-19[Europe/Zurich]
planned_at: 2026-10-19[Europe/Zurich]
not_after: 2026-11-04[Europe/Zurich]
# ── Status and claim
status: todo
claimed_since:
claimed_by:
# ── Links
depends_on: []
todoist_id: 6hjCw3qm5C5hJHJ3
# ── Origin
created_at: 2026-10-10T18:46+02:00[Europe/Zurich]
created_by: chan-lescut-macbook-pro, claude, b6a587c3-ab40-438e-b17b-58c9b3999ede
---
## Context

Claude Code's login has two tokens (in `~/.claude/.credentials.json` on the VM, Keychain `Claude Code-credentials` on the Mac):

| Token | Lifetime | Renewed by |
|---|---|---|
| access token (`expiresAt`) | 8 h | any `claude` run, interactive or `claude -p`, no login |
| refresh token (`refreshTokenExpiresAt`) | ~30 d? | **theory: nothing but a new login** |

Theory: the refresh token's expiry is fixed at login and does not move with use. Evidence on 2026-10-10: both machines use Claude daily, yet neither expiry date moves 30 days ahead.

| Machine | `refreshTokenExpiresAt` on 2026-10-10 |
|---|---|
| Mac | 2026-10-18T13:43Z |
| VM H-Frank-1 | 2026-11-05T21:51Z |

If the theory holds, on 2026-11-05 on the VM: `defcon-alert`'s `claude -p` (its LLM check) fails, and it should fall back to its plain rules (not verified). The `claude-quota` job here gets 401s for good. Other VM jobs don't use Claude auth.

Do not fix anything until the theory is confirmed. The candidate fix is `claude setup-token` (a token valid ~1 year) in `CLAUDE_CODE_OAUTH_TOKEN` for the VM units.

## Done when

- Mac, after 2026-10-18T13:43Z: did Claude Code ask for a login (ask Jean)? Is the new `refreshTokenExpiresAt` ~30 days after that login?
- VM: re-read `refreshTokenExpiresAt` after a few access-token renewals. Is it still 2026-11-05T21:51Z?
- Read `defcon-alert`'s code: what does it do when `claude -p` fails with an auth error (falls back to rules? crashes? alerts?).
- Verdict written below, with a dated recommendation for Jean, before 2026-11-04.

## History
- 2026-10-10: created (claude), after checking both machines' credentials

## Links
- `src/claude_quota_api_poller.py` (401 comment fixed in cd5fa39)
- `~/opt/defcon-alert/app/defcon_alert.py:299` (the `claude -p` call, on the VM)

## Result
