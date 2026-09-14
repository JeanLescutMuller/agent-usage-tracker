# Proposed architecture change — PENDING USER VALIDATION, not yet implemented

Written 2026-08-30, at the end of a same-day investigation into
`GET /api/oauth/usage` 429s. Full factual record (how we know all of this,
with code/capture evidence) is `AGENTS.md`, sections 1–7 — read that first
if any line below is unclear. This file is deliberately short: a decision
list to review next session, not the reasoning.

## The situation in one paragraph

There are three consumers of Claude Code quota data on this machine, and
they currently don't talk to each other. `/usage` and the statusline both
already get fresh numbers for free from response headers Claude Code
receives on ordinary `/v1/messages` traffic — no network call of their
own. Only this project's poller pays a real, currently-unreliable network
cost (`GET /api/oauth/usage`, ~21% 429 rate, account has had multi-day
lockouts). And only this project keeps a durable history — but it's
polling for data that a free, already-flowing, higher-resolution source
could supply instead.

## Proposed changes, in order

1. **Make `claude-statusline-command.sh` log every `rate_limits` reading
   it receives on stdin** into `agent-quota-tracker`'s shared
   `data/utilization-log.jsonl`, tagged with a new `source` value (e.g.
   `claude_pushed`) distinct from the existing poller's `claude` rows.
   Zero network cost, fires every statusline render (~10s) while any
   session is active. **Only append when the value changed** since the
   last logged row, to avoid flooding the log across N concurrent
   sessions rendering in lockstep.
2. **Flip the statusline's own display priority.** Currently: stdin value
   used only as a first-render bootstrap, then unconditionally overwritten
   by the poller's cache forever after. Change to: prefer the fresh stdin
   value; fall back to the poller's cached value only when stdin's
   `rate_limits` key is absent (a session that hasn't sent a message yet,
   or a plan where `rate_limits_available: false`).
3. **Demote `agent-quota-tracker`'s poller to an idle-only backstop.**
   Its only remaining unique job is covering stretches with **zero active
   Claude Code sessions anywhere** — nothing else keeps quota data moving
   then. Keep the `Retry-After`-respecting fix already shipped; widen
   `IDLE_INTERVAL_SECONDS` further (currently 300s) since freshness is no
   longer its job, only "don't let the log go dark for long unattended
   stretches" is.
4. **Leave `/usage` and Claude Code's own internal probe triggers alone**
   — nothing to change, they're Anthropic's code, already have their own
   working fallback chain (AGENTS.md §5/§7).
5. **Update `analysis.ipynb`'s loader** to treat `claude` (polled, richer
   body, occasionally 429) and `claude_pushed` (frequent, two percentages
   only) rows as distinct in reliability/resolution — not interchangeable
   in the $-per-percentage-point regression work.

## Explicitly not proposed

- **Do not** make this project fire its own `messages.create()` probe
  calls to harvest headers on demand. That would mean spending real
  tokens/money specifically to check how much is left — inverts the
  point of a passive tracker. Only ride traffic that already exists
  (statusline renders, which only happen because a real session is
  active anyway).
- **Do not** try to read `~/.claude.json`'s `cachedUsageUtilization` as a
  new source. Checked: it's a single overwritten snapshot (not a
  history), written only by a successful dedicated-endpoint call — same
  reliability problem as our own poller, adds nothing.

## Open questions to settle before implementing

- Exact `source` tag naming and whether `claude_pushed` rows should carry
  a reduced schema (just the two percentages+resets) vs. trying to match
  the existing `api`/`api_headers`/`error` shape for loader simplicity.
- How much to widen `IDLE_INTERVAL_SECONDS` in step 3 — no data yet on
  how long "no active session" stretches typically run on this machine.
- Whether step 1's dedup ("only append on change") should live in the
  bash script directly (simple, but another place bash string-compares
  floats) or be pushed into a tiny shared helper.
