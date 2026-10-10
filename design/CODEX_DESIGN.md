# Codex: current capture and how it would fit the four categories

**Status: someday.** The first target design (`design/DESIGN_MAP.html`, decided with the user on 2026-10-10) is **Claude only**. Codex is left out of it on purpose: its jobs and databases keep running as they do today, and nothing in the first target may break them. This file keeps everything Codex-related in one place for the day it is picked up (`TODO.md`, "Someday: Codex in the four categories").

## What runs today (2026-10-10, MacBook only: Codex is not installed on the VM)

```
job codex-quota          tick every 1 min ─▶ codex_quota_api_poller.py ◀─ codex app-server (account/rateLimits/read, account/usage/read)
                                                     │ % per window, raw result kept
job codex-plan-history   tick every 1 h, ──▶ codex_plan_history_poller.py ◀─ ChatGPT backend (usage/plan_limit_history)
                         fetch once a day            │ basis points per finished window
                                                     ▼
                                       ingest_account_quota.py ─▶ data/codex/account_quotas.db
                                                                   (sources codex_app_server, codex_plan_limit_history)

job transcripts          every 5 min ───▶ transcript_reader.py ◀─ ~/.codex/sessions, ~/.codex/archived_sessions
                                                     │ one row per turn (token_count event), tokens
                                                     ▼
                                       ingest_session_usage.py ─▶ data/codex/sessions_usages.db (requests, scans)

job push                 every 5 min ───▶ push_to_central.py ─▶ VM central/chan-lescut-macbook-pro/data/codex/
```

| Piece | Where | Notes |
|---|---|---|
| `jobs/codex-quota.sh`, `src/codex_quota_api_poller.py` | Mac | skips while a Codex session file is fresher than 5 min (the session itself writes the quota); exit 10 = skip |
| `jobs/codex-plan-history.sh`, `src/codex_plan_history_poller.py` | Mac | once per 24 h after a success, 1 h after a failure; skips without `~/.codex/auth.json` |
| Codex part of `src/transcript_reader.py` | Mac | `read_codex()`; entrypoint = `session_meta.source` or `subagent`; matches ccusage's monthly totals exactly (`USAGE_DATA_SOURCES.md` §4.1) |
| `requests.usd` for Codex | Mac | computed from `usage_db.PRICES` (ccusage's $4 / $20 / cached 0.1× for `gpt-5.6-sol`); NULL for `codex-auto-review`, `gpt-6-astra`, `gpt-6-luna`. Not a number OpenAI states: no Codex source carries a dollar amount (`USAGE_DATA_SOURCES.md` §4.5) |

## Side effects of the first (Claude-only) target on Codex

| First-target change | Effect on Codex | To decide when Codex is picked up |
|---|---|---|
| The `transcripts` and `push` jobs become one `aggregates` job (Claude Category 3) | the Codex turn reading must keep running inside it, unchanged, writing `data/codex/sessions_usages.db` as today | — |
| The `push` of raw database rows stops | Codex rows **no longer reach the VM** (`central/chan-lescut-macbook-pro/data/codex/` freezes) | Codex Category 3 sent like Claude's |
| `requests.usd` removed for Claude | Codex's `requests.usd` stays as it is until Codex is redesigned | drop it too (no $ computed by us) |
| Category 1 sync between the machines (Claude quotas only) | `codex/account_quotas.db` stays on the Mac only | add it to the sync |

## Codex in the four categories (sketch, not decided)

| Category | Claude (first target) | Codex (someday) |
|---|---|---|
| 1 · Quotas, shared | `claude/account_quotas.db`, synced both ways every 2 min | `codex/account_quotas.db` in the same sync. Polled on the Mac only (no Codex on the VM). Reset rule: Codex reset times **move** and Codex reports a fake idle countdown (`USAGE_DATA_SOURCES.md` §5.1, §5.3), so "show 0% after the reset" needs care |
| 2 · Machine raw, never shared | `sessions_usages.db` + transcripts | only the session files (`~/.codex/sessions`, `archived_sessions`): Codex has no statusline payload we capture and no telemetry, so no `sessions_usages.db` |
| 3 · Aggregates | `slots`: 5-min slot × machine × folder × entrypoint × model: tokens + Claude Code's $ | same row shape, **tokens only** (OpenAI states no $) |
| 4 · Meta-analysis | built by the quota maximizer, not here | the same, with Codex rows |

## Open questions for that day

- Codex session-file retention: not checked yet (Claude Code deletes transcripts after 365 days).
- Usage no local file sees (cloud tasks, IDE extension, ChatGPT app): visible only account-wide, in the app-server's daily buckets and `plan_limit_history`'s `breakdowns[]` (`USAGE_DATA_SOURCES.md` §4.3, §4.4). Worth a Category 1 table of their own?
- `codex-auto-review` turns: ccusage reports them as `gpt-5.6-luna`; keep Codex's own model name.
- Install Codex on the VM, or keep Codex a Mac-only agent?
