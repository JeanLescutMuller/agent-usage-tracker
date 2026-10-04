# TODO

## Remaining usage-data gaps

Parked 2026-09-30. Everything important that upstream exposes is now persisted (see `USAGE_DATA_REFERENCE.md` §1.2 and §7); these are what is left, in rough order of value. Details of each source are in `USAGE_DATA_SOURCES.md`.

### Retention of data we rely on but do not own

| Data | Lives in | Risk |
|---|---|---|
| Claude per-message tokens, per-session `cost-state` | `~/.claude/projects/**/*.jsonl` | Claude Code deletes transcripts after `cleanupPeriodDays` (365 on this machine). After that, only the push rows' `session_cost_usd` remains per session |
| Codex per-turn tokens and quota snapshots | `~/.codex/sessions/**/*.jsonl` | Codex's own retention rules — not checked yet |

Proposed fix, if history beyond a year matters: a small nightly job extracting only the usage records (transcript `cost-state` records and deduplicated assistant `usage`, Codex `token_count` events) into `data/`, a few MB instead of the ~517 MB of full transcripts. First step: check Codex's session-file retention.

### Still capturable, not captured

| Gap | Source | Value | Effort |
|---|---|---|---|
| Codex usage per day × model × client (CLI, IDE, web, …) | ChatGPT backend `usage/daily-token-usage-breakdown` (`USAGE_DATA_SOURCES.md` §4.4) | Low to medium: the only per-client split, but **relative** (peak day = 100); `dailyUsageBuckets` gives the scale | One more request in `poll_codex_plan_history.py` |
| Claude quota status beyond the percent (`status` allowed / warning / rejected, `representative-claim`, overage) | `/v1/messages` response headers (`USAGE_DATA_SOURCES.md` §3.6) | Low: the percent already carries most of it | Needs a billed request of our own (≈ $0.0025) or a proxy |

### Debian VM not covered

Every collector (push path, pollers, telemetry receiver) is deployed on the MacBook only, as LaunchAgents. Claude Code or Codex usage on the VM shows only as account-wide percent movement — no telemetry, no push rows. Covering it needs systemd `--user` units for the pollers and receiver, and the telemetry env keys in the VM's `~/.claude/settings.json`. Only worth doing if agents actually run there.

### Not obtainable from any source

For the record, so nobody re-investigates: Codex spend at any granularity on a Plus account (403 or `null` everywhere); account-wide spend for either agent; quota percent per session or thread (Codex's `thread_usage/query_v2` returns `unavailable`); usage while the Mac is asleep or off (Codex's `plan_limit_history` recovers the last 7 days, Claude has no equivalent); a per-client breakdown for Claude (claude.ai web / mobile, other machines).

## Claude poller cadence without agent-statusline

Parked 2026-10-04. `poll_claude.py` polls every tick only while agent-statusline's heartbeat says a status line is on screen; without agent-statusline it stays on its ~5 min idle cadence even while Claude sessions are active. A second liveness signal, recently modified transcripts under `~/.claude/projects/**/*.jsonl`, would fix that, the way `poll_codex.py` uses Codex session files.
