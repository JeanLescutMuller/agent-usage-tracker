# Usage data sources — what exists upstream, before we capture anything

**Canonical inventory of every place Claude Code and Codex expose usage data, independent of what this repository does with it.** It answers "what *could* be captured, where, in which unit, at which granularity". What `agent-usage-tracker` actually captures, how, and where it lands is the companion file `USAGE_DATA_REFERENCE.md`. Other projects link to these two files rather than restating them.

It does **not** cover how percentages convert into dollars beyond the constants in §1 — that analysis, with its error bars, is `adhoc_quotas_analysis/CONCLUSIONS.md`.

Markers: **[tested]** was exercised live on this machine; **[verified]** was measured on existing data on this machine; **[source]** was read in upstream source code (`openai/codex` at commit `a5cce88`, 2026-09-30) but not exercised; **[docs]** comes from official documentation. Anything else is inference and says so.

*Last tested: 2026-09-30, Claude Code 2.1.284, codex-cli 0.154.0, Claude Pro and ChatGPT Plus accounts. Transcript and Codex session token totals and the Claude price table re-verified 2026-10-07 against telemetry and ccusage 20.0.26 (§3.4, §4.1). Every raw Codex key scanned for a dollar amount 2026-10-08 (§4.5). Token lifetimes and the per-machine scope of a 429 verified 2026-10-10 (§3.5).*

---

## 1. Three units, and why they do not convert cleanly

| Unit | What it is | Who reports it |
|---|---|---|
| **Quota percent** | Share of a rate-limit window consumed, 0–100 | Both agents, several channels, always account-wide |
| **Tokens** | Input / output / cache-read / cache-write counts | Both agents, per request, per turn or per session |
| **Spend (USD)** | Tokens × a list price, never a billed amount | **Claude only**, computed by Claude Code itself; Codex nowhere |

- **Percent is the only authoritative unit.** It is what actually runs out, and the only number a limit is enforced against. Anything that must not overshoot should be measured in percent.
- **No server returns a dollar amount for usage; every USD figure is tokens × list price.** Claude Code multiplies tokens by its built-in list prices (`costBasis: "list"` **[tested]**) and reports the result in the statusline `cost`, the transcript `cost-state`, telemetry `cost_usd` and the `-p` JSON. ccusage and this repo recompute the same product and agree to the cent (§3.4). Codex reports no dollars at all (§4.5): a Codex USD figure is tokens × a price someone chose, with nothing upstream to check it against. Dollars are a *comparison* metric (was this model worth it?), never a budget; on a subscription the only real money is the plan fee.
- **Tokens convert to percent by an unknown, variable factor.** The meter weights cache reads far below list price (roughly 4–5×, `CONCLUSIONS.md`), and the dollars-per-percent ratio varies about ±30% between windows.

Rule of thumb: **enforce on percent, compare on dollars, diagnose on tokens.**

Conversion constants, from `CONCLUSIONS.md` (measured, not published):

| | Claude (Pro) | Codex (Plus) |
|---|---|---|
| 100% of one 5-hour window | ≈ $32 (middle 80%: $20–39) | ≈ $20.5 (range $16.7–26.3) |
| 100% of the 7-day period | ≈ 8.85 five-hour windows, ≈ $283–314 | ≈ 6.2 five-hour windows, ≈ $127 |
| 1% of the 7-day period | ≈ $2.8 | ≈ $1.3 |

---

## 2. Availability matrix

Rows are agent + unit + granularity; columns are sources. ✅ available · ⚠️ partial or conditional · ❌ absent · 📄 read in source or docs only. Every ✅/⚠️/❌ was tested on 2026-09-30 unless marked 📄.

### 2.1 Claude

| Unit | Granularity | Transcripts (§3.4) | Statusline stdin (§3.1) | `/api/oauth/usage` (§3.5) | `/v1/messages` headers (§3.6) | OpenTelemetry (§3.7) |
|---|---|---|---|---|---|---|
| Quota % (5h / 7d) | Account-wide | ⚠️ only on a 429 refusal: status + reset, no % | ✅ whole numbers; present before the first message | ✅ whole numbers | ✅ two decimals of a fraction, i.e. whole % | ❌ |
| Tokens | Per request, per model | ⚠️ some requests missing (§3.4) | ⚠️ last request only | ❌ | ❌ | ✅ `api_request` event |
| Tokens | Per session, per model | ✅ `cost-state` record, cumulative | ❌ `total_*` is context size | ❌ | ❌ | ✅ `token.usage` counter |
| Tokens | Account-wide | ❌ | ❌ | ❌ | ❌ | ❌ |
| Spend $ | Per request | ⚠️ computable, same gap | ❌ | ❌ | ❌ | ✅ `cost_usd` |
| Spend $ | Per session, per model | ✅ `cost-state` | ⚠️ cumulative, all models combined | ❌ | ❌ | ✅ `cost.usage` counter |
| Spend $ | Account-wide | ❌ | ❌ | ❌ `used_dollars` always `null` | ❌ | ❌ |

The headless `claude -p --output-format json` result object (§3.3) and hooks stdin (§3.2) are omitted from the matrix: the first adds nothing the transcript lacks, the second carries no usage data.

### 2.2 Codex

| Unit | Granularity | Session files (§4.1) | Inside the TUI (§4.2) | app-server RPC (§4.3) | ChatGPT backend HTTP (§4.4) |
|---|---|---|---|---|---|
| Quota % (5h / 7d) | Account-wide | ✅ whole numbers, every turn | ✅📄 whole numbers | ✅ whole numbers, pulled or pushed | ✅ `plan_limit_history`: finished windows, 7 days back, **fractional** |
| Quota % | Per thread | ❌ | ❌ | ❌ | ⚠️ `thread_usage/query_v2` answers, but `unavailable` |
| Tokens | Per turn | ✅ `last_token_usage` | ✅📄 | ✅ `thread/tokenUsage/updated` → `last` | ❌ |
| Tokens | Per session | ✅ `total_token_usage` | ✅📄 | ✅ same notification → `total` | ❌ |
| Tokens | Account-wide | ❌ | ❌ | ✅ daily buckets + lifetime total | ⚠️ analytics: per day × model × surface, **relative** only |
| Spend $ | Per turn | ❌ | ❌ | ❌ | ❌ 403 Forbidden |
| Spend $ | Per thread | ❌ | ❌ | ❌ `threadUsage: null` | ❌ 403 Forbidden |
| Spend $ | Account-wide | ❌ | ❌ | ❌ | ❌ credit report empty |

### 2.3 Two asymmetries that drive most design decisions

1. **Quota percent is always account-wide.** No source attributes a meter movement to a session, a thread or a client. Attribution needs per-session token or spend data plus a time-interval argument.
2. **Only account-wide sources see usage this machine did not generate.** On Codex that is a large blind spot (cloud tasks, IDE extension, ChatGPT app), visible only through the app-server's daily buckets and the backend's `plan_limit_history`. On Claude every percent step since 09-13 matched a local message **[verified]**.

---

## 3. Claude, source by source

### 3.1 Statusline stdin

Claude Code pipes a JSON object to the configured `statusLine` command on every render **[docs]**. Free: no API call of its own. Captured for real from a throwaway interactive session **[tested]**:

```json
{
  "session_id": "248c4a95-...", "session_name": "...", "prompt_id": "...",
  "transcript_path": "/Users/.../248c4a95-....jsonl",
  "model": {"id": "claude-haiku-4-5-20251001", "display_name": "Haiku 4.5"},
  "version": "2.1.284",
  "cost": {"total_cost_usd": 0.0364458, "total_duration_ms": 52873, "total_api_duration_ms": 6337,
           "total_lines_added": 0, "total_lines_removed": 0},
  "context_window": {"total_input_tokens": 35411, "total_output_tokens": 42,
                     "context_window_size": 200000, "used_percentage": 18, "remaining_percentage": 82,
                     "current_usage": {"input_tokens": 10, "output_tokens": 42,
                                       "cache_creation_input_tokens": 106, "cache_read_input_tokens": 35295}},
  "prompt_cache": {"warm": true, "ttl": "1h", "requests": 2, "misses": 0, "hit_ratio": 0.83,
                   "cache_write_tokens": 11919, "recache_tokens_if_cold": 35411, ...},
  "rate_limits": {"five_hour": {"used_percentage": 17, "resets_at": 1790784600},
                  "seven_day": {"used_percentage": 4, "resets_at": 1791226800}}
}
```

- **`rate_limits.*.used_percentage` is typed as a float [docs], but carries whole numbers in practice [tested]** — it comes from the `/v1/messages` headers (§3.6), which have two decimals of a fraction.
- **`rate_limits` appeared before the first message was sent** — in 4 of 28 captured payloads the session cost was still 0 **[tested]**. The docs say "only after the first API response"; presumably Claude Code's own startup requests count as one (not verified).
- `rate_limits` is absent for non-Pro/Max accounts, and each window disappears once its `resets_at` passes **[docs]**. Handle absence, never assume 0.
- **`rate_limits.spend_limit`** exists only behind a Claude apps gateway (an admin-set dollar cap), Claude Code ≥ 2.1.251, and can exceed 100 **[docs]**. It does not apply to a Pro subscription and was absent in every captured payload **[tested]**.
- **`cost.total_cost_usd` is cumulative for the session, all models combined** **[docs]**, and matched the transcript's `cost-state` exactly ($0.0364458) **[tested]**. It resets on `/clear` since 2.1.211.
- **`context_window.total_input_tokens` / `total_output_tokens` are NOT session totals.** They are the context size as of the last response: 35,411 = 10 + 106 + 35,295 of `current_usage` **[tested]**.
- `current_usage` is the last API call only, `null` before the first call and right after `/compact` **[docs]**.
- `prompt_cache` is session-level cache statistics for the main conversation, subagents excluded, Claude Code ≥ 2.1.251 **[docs]**.
- **Headless `claude -p` never renders a status line**, so this channel carries interactive activity only.

### 3.2 Hooks stdin — no usage data

Hooks receive `session_id`, `cwd`, `hook_event_name`, `transcript_path` and event-specific fields **[docs]**. No `cost`, no tokens, no `rate_limits`, on any event.

### 3.3 `claude -p --output-format json` — adds nothing the transcript lacks

One JSON object on stdout **[tested]**:

```text
total_cost_usd  0.0210686
modelUsage      {"claude-haiku-4-5-20251001": {"inputTokens": 10, "outputTokens": 66, "thinkingTokens": 59,
                 "cacheReadInputTokens": 12306, "cacheCreationInputTokens": 9749,
                 "costUSD": 0.0210686, "costBasis": "list", ...}}
usage           {"input_tokens": 10, "output_tokens": 66, "cache_creation": {"ephemeral_1h_input_tokens": 9749, ...}, ...}
session_id, num_turns, duration_ms, duration_api_ms, ...
```

The object itself is not written anywhere, but **its content is**: the session's transcript holds the same per-message `usage` and a `cost-state` record with identical `totalCostUSD` and `modelUsage` **[tested]**. No `rate_limits`: the headless path reports cost, never quota.

### 3.4 `.jsonl` transcripts

`~/.claude/projects/<project>/<session-uuid>.jsonl`, one file per session; subagent transcripts under `<session-uuid>/subagents/` **[verified]**. 156 files, 517 MB, median 604 KB as of 2026-09-29 **[verified]**. Interactive sessions carry `"entrypoint": "cli"`, headless ones `"sdk-cli"` **[verified]**. Three record kinds matter:

**Assistant messages — per-request tokens.** `message.model` and `message.usage` (`input_tokens`, `output_tokens`, `output_tokens_details.thinking_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` split into `ephemeral_5m` / `ephemeral_1h`, `service_tier`, `speed`) **[verified]**. One request is written as several lines sharing one `requestId`, and `output_tokens` grows from line to line: **dedup by `requestId` (fall back to `message.id`) across all files, keeping the line with the largest `output_tokens`** **[verified]**. Keeping the first line undercounts output about 2× (319 K vs 639 K tokens in 2026-07); deduplicating per file double-counts resumed sessions, which copy earlier requests into their new file. Cost is computable:

```text
price ($/MTok in/out, cache-read ratio):  opus-5 5/25 ×0.1 · opus-5-5 4/20 ×0.05 · sonnet-5, sonnet-5-5 2/10 ×0.1 · haiku-4-5 1/5 ×0.1
cost = in×pin + out×pout + cache_read×pin×ratio + cache_write_5m×pin×1.25 + cache_write_1h×pin×2
```

**Tokens and USD from transcripts are proven exact [verified 2026-10-07].** `adhoc_quotas_analysis/verify_token_costs.py` re-runs all three checks, read-only:

| Check | Result |
|---|---|
| Transcript tokens vs telemetry `api_request` (§3.7), same `request_id` | 2,163 requests, **0 mismatches** |
| Price table above × transcript tokens vs Claude Code's own `cost_usd` | **0.000% median error** for every model; Opus 5.5: 3 of 1,470 requests off, −0.15% in total |
| Monthly tokens and USD per model vs `npx ccusage@20.0.26 monthly --mode calculate --breakdown` | **Identical to the token and the cent** for every month and model, 2026-07 → 2026-10 (the running month only differs by usage after ccusage ran) |

Opus 5.5's price differs from Opus 5's: $4/$20 with cache reads at 0.05× input, fitted from telemetry and then confirmed by ccusage. Sonnet 5 had no telemetry; ccusage confirms $2/$10. **What transcripts alone miss:** the requests below, 4.6% of USD over 2026-09-30 → 10-07 ($14.82 of $324.12): `prompt_suggestion` $10.62, `compact` $3.21, `web_search_tool` $0.70, `web_fetch_apply` $0.16, `away_summary` $0.12, `generate_session_title` $0.02. ccusage reads only transcripts, so it undercounts USD by the same share; only telemetry has these requests.

**Assistant messages do not cover every request.** In the tested interactive session, the two recorded messages summed to 20 input / 101 output / 58,777 cache-read / 11,919 cache-write tokens, while the same session's `cost-state` counted 1,267 / 365 / 94,178 / 11,968 **[tested]**. Computed from the messages, the cost is ≈ $0.030 against $0.0364 in `cost-state` — about 17% of the spend is not in the messages. OpenTelemetry (§3.7) identified them in a second interactive session: **session-title generation (`generate_session_title`) and prompt suggestions (`prompt_suggestion`)** **[tested]**.

**`cost-state` records — per-session, per-model totals.** Present since Claude Code 2.1.239, in 71 of 159 main-session transcripts on 2026-09-30 (192 transcript files on 2026-10-08), 1–5 records per session; the exact trigger is not verified **[verified]**. It includes the requests the messages miss:

```json
{"type": "cost-state", "sessionId": "...", "totalCostUSD": 0.0364458,
 "totalAPIDuration": 1319, "totalAPIDurationWithoutRetries": 1317, "totalToolDuration": 0,
 "totalDuration": 3675, "startTime": 1790767681103, "totalLinesAdded": 0, "totalLinesRemoved": 0,
 "modelUsage": {"claude-haiku-4-5-20251001": {"inputTokens": 1267, "outputTokens": 365, "thinkingTokens": 336,
                "cacheReadInputTokens": 94178, "cacheCreationInputTokens": 11968, "webSearchRequests": 0,
                "costUSD": 0.0364458}},
 "hasUnknownModelCost": false}
```

It has no timestamp of its own; `startTime + totalDuration` dates it.

**429 refusals — the only quota trace.** A refused request writes a synthetic assistant message carrying `quotaLimits.status: "rejected"`, `quotaLimits.rateLimitType` (`five_hour` / `seven_day`), `quotaLimits.resetsAt` and `apiErrorStatus: 429` — but no percentage **[verified]**.

### 3.5 `GET /api/oauth/usage` — the metadata endpoint

Authenticated with the Claude Code OAuth token from the macOS Keychain. Returns `five_hour` / `seven_day` (and several always-`null` model- or product-specific windows), each with `utilization`, `resets_at`, and `limit_dollars` / `used_dollars` / `remaining_dollars` that are **always `null`**, plus an `extra_usage` object (disabled on this account) **[verified]**. `utilization` is a whole number: 0 fractional among 11,314 values **[verified]**. Unreliable: about 21% of calls got HTTP 429, and more failed on network errors (`adhoc_quotas_analysis/AGENTS.md`). No tokens, no dollars.

**The token, and the scope of a 429 [verified 2026-10-10].** Each machine where Claude Code is logged in has its own OAuth pair: an access token valid 8 h and a refresh token valid weeks (Mac: macOS Keychain, `Claude Code-credentials`; Linux: `~/.claude/.credentials.json`). A poller borrows that machine's access token; only Claude Code renews it, when it runs on that machine. A machine with no Claude Code activity for 8 h therefore gets **401** until its next session (the VM: 16 a day on 2026-10-09 and 10-10); once the refresh token expires too, only a new login helps. **A 429 is counted per token, i.e. per machine, not per account**: on 2026-10-10 the VM got 429 with `Retry-After: 3600` at 13:17:06 UTC, and the Mac's poller then succeeded 27 times, every 2 minutes, until 14:14:20. It limits only this metadata endpoint; Claude Code's own requests are unaffected (§3.6). So a 429 makes only the machine that got it wait.

**`seven_day_breakdown`** (present since 2026-09-14, already in every poller row's raw `api`) splits the 7-day meter by product: `rows[]` of `{key, display_name, percent}` with keys `claude_code`, `chat`, `cowork`, `other`, plus `as_of` and `window_started_at` **[verified 2026-10-08]**. It is the only account-wide signal of usage outside Claude Code (web and app chats, Cowork). Every reading so far has `claude_code` at 100% and the others at 0. It does not say which machine or whether a Claude Code session ran locally or in the cloud. Re-checked 2026-10-08 after the Max 5x upgrade: the `*_dollars` fields are still `null` and `spend` (usage credits beyond the plan limits) is disabled with `used` 0: **no account-wide token or dollar figure exists for a Claude subscription**.

### 3.6 `POST /v1/messages` response headers — the real quota source

Every Messages API response carries the quota as headers **[tested]**:

```text
anthropic-ratelimit-unified-5h-utilization: 0.17
anthropic-ratelimit-unified-5h-reset: 1790784600
anthropic-ratelimit-unified-5h-status: allowed
anthropic-ratelimit-unified-7d-utilization: 0.04
anthropic-ratelimit-unified-7d-reset: 1791226800
anthropic-ratelimit-unified-7d-status: allowed
anthropic-ratelimit-unified-status / -reset / -representative-claim / -fallback-percentage
anthropic-ratelimit-unified-overage-status / -overage-disabled-reason
```

The utilization is a fraction with two decimals, so **the quota's true resolution is 1%**. This is what Claude Code turns into `rate_limits` on stdin, and it never got rate-limited in any test. Claude Code also sends a dedicated one-token `quota_check` request to harvest these headers (`adhoc_quotas_analysis/AGENTS.md`). Seen with `ANTHROPIC_LOG=debug` on a headless run, which prints each request and response to stdout.

### 3.7 OpenTelemetry export — per-request cost from Claude Code itself

Enabled with `CLAUDE_CODE_ENABLE_TELEMETRY=1`; exporters `otlp`, `prometheus` (metrics only) or `console` **[docs]**. Works for headless runs **[tested]**. A console export of one `-p` call produced **[tested]**:

- **`claude_code.api_request` event**, one per API request: `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `cost_usd`, `cost_usd_micros`, `duration_ms`, `request_id`, `speed`, `query_source`.
- **`claude_code.token.usage`** and **`claude_code.cost.usage`** counters, attributed by `model` and `query_source` (`main` / `subagent` / `auxiliary` per the docs).
- No quota percent anywhere **[docs]**.

Tested on an interactive session too **[tested 2026-09-30]**: four `api_request` events — `query_source` `repl_main_thread` ×2, `generate_session_title` and `prompt_suggestion` — summed exactly to the session's `cost-state` (1,267 / 286 / 94,492 / 11,946 tokens, $0.0360382). The transcript held only the two `repl_main_thread` requests: **the missing requests of §3.4 are session-title generation and prompt suggestions**, and telemetry is the only per-request record of them. Events are pushed by each Claude process in batches over OTLP; with `http/json`, a stdlib HTTP server is enough to receive them.

### 3.8 Anthropic Admin Usage / Cost API — not applicable

`/v1/organizations/usage_report/messages`, `/v1/organizations/cost_report` and `/v1/organizations/usage_report/claude_code` give account-wide tokens and dollars, but require an Admin API key for a Claude API organization **[docs]**. A Pro subscription has none.

---

## 4. Codex, source by source

### 4.1 Session `.jsonl` files

`~/.codex/sessions/**/*.jsonl` (and `~/.codex/archived_sessions/`), one file per thread; the first line's `payload.id` is the thread id **[verified]**. 66 files, 54.5 MB as of 2026-09-29 **[verified]**. The `token_count` event carries both percent and tokens:

```json
{"timestamp": "2026-09-16T15:43:30.869Z", "type": "event_msg", "payload": {
  "type": "token_count",
  "info": {
    "total_token_usage": {"input_tokens": 19757, "cached_input_tokens": 10880, "cache_write_input_tokens": 0,
                          "output_tokens": 218, "reasoning_output_tokens": 40, "total_tokens": 19975},
    "last_token_usage": {...},
    "model_context_window": 258400},
  "rate_limits": {
    "primary":   {"used_percent": 1.0,  "window_minutes": 300,   "resets_at": 1789581115},
    "secondary": {"used_percent": 16.0, "window_minutes": 10080, "resets_at": 1789817081},
    "plan_type": "plus", "credits": {...}}}}
```

- `total_token_usage` is cumulative for the thread and repeats unchanged when nothing happened — **skip consecutive repeats**. `last_token_usage` is that turn alone.
- `used_percent` is a float in the core protocol **[source]** but whole-valued in all 4,952 readings **[verified]**.
- No model per turn in `token_count`, no cost. The model comes from the preceding `turn_context` record's `payload.model`; ccusage reports `codex-auto-review` as `gpt-5.6-luna`.
- Per turn, `last_token_usage` is exact: one row per event whose running `total_tokens` is new (repeats share it) reproduces ccusage's monthly totals exactly; `transcript_reader.py` stores Codex this way **[verified 2026-10-08]**.
- `total_token_usage` can **reset** within a file (3 times by 2026-10-07): a delta from the previous event goes negative. Use that event's `last_token_usage` instead. With this, monthly tokens per model match ccusage 20.0.26 exactly for 2026-08 → 2026-10 **[verified 2026-10-07]**, `adhoc_quotas_analysis/verify_token_costs.py`.
- Our own runs are identified by `session_meta.cwd`, set with `codex exec -C <dir>` **[verified]**.

### 4.2 Inside the TUI — what a status line could be given

Upstream Codex has no status-line command; anything handed to one comes from TUI state. The TUI reads quota through the app-server layer, which converts with `used_percent.round() as i32` **[source]**, so its copy is whole numbers — which loses nothing, since the raw values are whole anyway (§4.1). It holds per-turn and per-thread tokens **[source]**, and a "backend-estimated cost" per thread fetched through `account/usage/read` **[source]** — which returns `null` for this account (§4.3), so in practice it holds no cost.

### 4.3 `codex app-server` JSON-RPC

`codex app-server --stdio`, authenticated by `~/.codex/auth.json`; `initialize`, then requests **[tested]**.

| Method | Kind | Returns |
|---|---|---|
| `account/rateLimits/read` | request | `primary` / `secondary` `usedPercent` (integer), `windowDurationMins`, `resetsAt`, `planType`, `credits`, `rateLimitResetCredits` **[tested]** |
| `account/usage/read` | request | `summary.lifetimeTokens`, `peakDailyTokens`, streak stats, `dailyUsageBuckets[] {startDate, tokens}` — account-wide, all clients **[tested]** |
| `account/usage/read` + `threadId` | request | `threadUsage` with per-thread estimated credits / USD micros and a per-model breakdown **[source]**; returned `null` for 5 threads, including a brand-new one **[tested]** |
| `account/rateLimits/updated` | notification | Same shape as the read, pushed after each turn **[tested]** |
| `thread/tokenUsage/updated` | notification | `tokenUsage.total` (cumulative) and `.last` (turn), same fields as §4.1, plus `modelContextWindow` — only for threads this app-server runs **[tested]** |

`dailyUsageBuckets` is **the only absolute account-wide token signal for either agent**: it counts cloud tasks, the IDE extension and the ChatGPT app, none of which leave a local trace. One-day granularity.

### 4.4 ChatGPT backend HTTP — `https://chatgpt.com/backend-api/wham/...`

Called directly by the Codex TUI and backend client, not exposed through app-server **[source]**. Bearer token and `ChatGPT-Account-Id` from `~/.codex/auth.json`. All tested read-only on 2026-09-30:

| Endpoint | Result | Content |
|---|---|---|
| `GET usage/plan_limit_history?days=7` | ✅ 200 | Finished windows only: `window_minutes`, `starts_at`, `ends_at`, `used_basis_points` (**hundredths of a percent, fractional**: 118.79 = 1.1879%), `accounting_complete`; plus `data_as_of`, `coverage_start`, `approximate: true`, `boundary_tolerance_seconds: 60`. Three periods returned for the last 7 days. Each period also has `breakdowns[]` of the same basis points by `thread_source` (`user`, `guardian_review`, `thread_title`) and by `turn_trigger` (`user`, `exec`) **[verified 2026-10-08]**. |
| `POST usage/thread_usage/query_v2` | ⚠️ 200 | Per thread: `five_hour_limit_percent`, `weekly_limit_percent`, amounts, per-model groups **[source]** — but `data_status: "unavailable"` for all 6 threads tried, old and new |
| `POST usage/thread_usage/query` | ❌ 403 | Per-thread estimated USD **[source]** |
| `POST usage/thread-estimates/query` | ❌ 403 | Per-turn estimated USD **[source]** |
| `GET usage/daily-token-usage-breakdown` | ⚠️ 200 | Per day × model × surface (`cli`, `vscode`, `web`, `work_web`, …), but `units: "percent"` normalised so the peak day = 100 — relative, not absolute; lags at least a day |
| `GET usage/credit-usage-events` | ❌ 200 | Empty list on a Plus account |

`plan_limit_history` is **the only fractional quota source for either agent**, and the only one that can be read retroactively (7 days). Two more facts it revealed **[verified 2026-09-30]**: the whole percent every live Codex source reports is `round(used_basis_points / 100)` (118.79 → 1, 806.21 → 8, 164.90 → 2), and `ends_at` is a window's *real* end — a 7-day window whose live readings all said `resetsAt` 09-28 09:05 actually ended 09-26 17:09, an early reset no live reading shows.

### 4.5 No dollar amount anywhere [verified 2026-10-08]

Every key of every raw Codex source, conversation text excluded, scanned for `cost|usd|dollar|price|credit|spend|amount|balance|bill|charge|cents|currency`: 77 session files (2,904 `token_count` events), 6,985 app-server rate-limit readings, all `plan_limit_history` rows, Codex's own `~/.codex/*.sqlite` schemas (`state_5`, `logs_2`, `goals_1`, `memories_1`, `queue_1`, `thread_history_1`) and `~/.codex/models_cache.json` (no prices).

| Money-like key | Where | Value on this account |
|---|---|---|
| `credits.balance`, `credits.has_credits` / `hasCredits` | `token_count.rate_limits` (§4.1), `account/rateLimits/read` (§4.3) | `"0"`, `false` in every reading |
| `spendControlReached` / `spend_control_reached` | same | `false` / `null` |
| `rateLimitResetCredits.credits` | `account/rateLimits/read` | `null` |

These describe usage credits bought beyond the plan (none here), not the cost of usage. The per-thread and per-turn USD of §4.3 and §4.4 are `null` or 403. So Codex usage exists only as **tokens** (per turn) and **quota percent / basis points** (per window).

---

## 5. Traps inherent in the upstream data

### 5.1 Codex's fake idle countdown

When no window is running, the Codex API returns `used_percent == 0` with `resets_at ≈ now + window span`. This is **not** a fresh empty window: it means *no window is open*. Drop it **[rule from `quota_model.py`]**.

### 5.2 Windows exist only because a message opened one

Neither agent starts a window on a schedule. A 5-hour window begins at the first message after the previous one expired; for **Codex the 7-day period also begins at the first message after idle**, so idle time is not banked. Claude's 7-day period is fixed at **Monday 19:00 UTC**. Idle gaps are the norm: 26 of 31 consecutive Claude window pairs (median 11.5 h), 21 of 28 for Codex (median 10.0 h). Claude windows cover only **24% of elapsed time** **[verified]**.

### 5.3 Codex reset times move

Observed 09-07 19:20, 09-15 09:14, 09-19 11:24, then **04:13 and 09:05 for the same period** **[verified]**. Early server-side resets (08-27, 08-31, 09-12) can re-anchor both meters at once. No Codex schedule can be precomputed; re-read the meter.

### 5.4 Freshness differs by an order of magnitude

| | Claude | Codex |
|---|---|---|
| Pushed on every render | ✅ statusline stdin | ❌ (only with a patched TUI) |
| Inside a running session | seconds | per turn, via `token_count` |
| Between sessions | only by polling or a billed request | only by polling |

---

## 6. What is not available anywhere

- **Subscription-basis dollars.** Every USD figure is list-price (Claude) or absent (Codex). The percent-to-dollar mapping in §1 is inferred, with ±30% variance between windows.
- **Account-wide spend** for either agent, and **account-wide tokens** for Claude.
- **Quota percent per session or thread.** Codex's `thread_usage/query_v2` has the fields but returns `unavailable`.
- **Fractional quota for Claude.** The headers carry whole percents.
- **Codex spend at any granularity** on a Plus account (403 or `null` everywhere, §4.5).
- **The conversion factor between one saturated 5-hour window and the 7-day meter.** Unpublished; `CONCLUSIONS.md` estimates it (Claude ≈ 8.85 windows/week, Codex ≈ 6.2).
- **Demand above the cap.** When a window saturates, what the user *wanted* is unrecorded — only what they were allowed to spend. 5 of 35 Claude windows reached 100%, ≈ 4 hours at the cap over 30 days **[verified]**.

---

## 7. How to re-verify

| Claim | How |
|---|---|
| Statusline stdin content | Run `claude --settings <file>` with a `statusLine` whose command is `cat > dump-$(date +%s%N).json`, send a message, read the dumps |
| `-p` result vs transcript | `claude -p "Reply with: ok" --model claude-haiku-4-5-20251001 --output-format json`, then compare with the `cost-state` record in `~/.claude/projects/*/<session_id>.jsonl` (≈ $0.02) |
| Transcript tokens and USD exact; ccusage agrees | `python3 adhoc_quotas_analysis/verify_token_costs.py` (refresh its ccusage reference with a new `npx ccusage@latest monthly --mode calculate --breakdown` run) |
| Transcripts missing requests | Sum deduplicated assistant `usage` in one interactive transcript and compare with its last `cost-state` |
| `/v1/messages` headers | `ANTHROPIC_LOG=debug claude -p ...` and grep `anthropic-ratelimit-unified` in stdout |
| OpenTelemetry content | `CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=console OTEL_LOGS_EXPORTER=console claude -p ... < /dev/null` |
| Fractional percent anywhere | Scan `used_percent`, `usedPercent`, `utilization`, `five_hour_pct` for non-integers |
| Codex app-server methods | Spawn `codex app-server --stdio`, send `initialize`, `initialized`, then the method; for notifications run a `thread/start` + `turn/start` |
| Codex backend endpoints | `curl` the paths in §4.4 with the bearer token and `ChatGPT-Account-Id` from `~/.codex/auth.json` |
| No Codex dollar amount (§4.5) | Walk every JSON key path of the session files (skipping `response_item` and message events), of `account_quotas.raw` in `data/codex/account_quotas.db` and of `~/.codex/*.json`, plus `sqlite3 ~/.codex/<db>.sqlite .schema`, and grep the money-like names above |
| Upstream field types | Sparse-clone `openai/codex` and grep `used_percent` in `codex-rs/protocol` and `codex-rs/app-server-protocol` |

When any of this changes, update this file and its "Last tested" line.
