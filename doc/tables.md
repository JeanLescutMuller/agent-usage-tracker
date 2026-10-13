# Tables: every row agent-usage-tracker stores, field by field

What each table holds, which collector fills it from which upstream source, and the traps in the rows. Why it is built this way, where each file lives and which job runs when: `doc/design.md`. What exists upstream, captured or not: `doc/upstream.md`, cited here as "Upstream §n". How another project reads it: `FOR_READERS.md`. When a row's shape changed: `doc/changes.md`.

Markers: **[verified]** was measured on this machine's data; **[docs]** comes from official documentation.

*Last verified: 2026-10-12 (P13 + P14 on both machines: the slots equal a separate count of every request, Mac 3,331 / VM 41, the VM's copy of the Mac's identical). Earlier verifications: `doc/changes.md`.*

---

## 1. Common to every table, and the scope rule

**Every table** starts with `ts` (Unix epoch, when the row was true: `observed_at` for statusline and telemetry rows, the poll time for poll rows, the request's first line for requests) and `dt` (the same instant, ISO 8601 UTC). Every table except `requests` and `scans` keeps the row's JSON **verbatim in `raw`**: for migrated rows the original JSONL line byte for byte, for new rows the same shape the JSONL collector produced. So §3, §4 and §6 describe `raw`, and any old JSONL reader is served by `SELECT raw FROM <table> ORDER BY rowid`. Every row is insert-only; `row_hash` (SHA-1 of `raw`, UNIQUE), `request_id` (UNIQUE) or the slot key makes re-inserting it a no-op. The gates reject a malformed row, an account row with per-session fields, and a session row with any percent-like key.

**The scope rule: percent is account-scope only.** The quota meter is one account-level number: when two sessions spend at once, there is no per-session percentage, not a hidden one, an undefined one. So:

| Database | Carries | Never carries |
|---|---|---|
| `data/<agent>/account_quotas.db` | Meter percent, reset times, `source`, `machine`, and `observed_by_session` on statusline rows | Per-session cost or token totals |
| `data/<agent>/sessions_usages.db`, `data/claude/aggregates/<machine>.db` | Tokens, USD, model, execution folder, entrypoint, cache statistics | **A quota percent, under any name** |

The scopes join on **`ts`** (a time join); no field is duplicated across them. Nothing in this repository estimates or apportions a per-session percentage, and nothing will: any such figure is a reader's, with its uncertainty stated there.

`observed_by_session` on an account row names the session that was rendering when the reading was taken (a free liveness signal), **not** the session whose usage it is.

## 2. What comes from where

### 2.1 Collector → upstream source → table

When each runs: `doc/design.md` §8.

| Collector | Upstream source | Writes (through its gate) |
|---|---|---|
| **Statusline reader**: `src/statusline_payload_reader.py`, run by agent-statusline on every Claude render | Statusline stdin (Upstream §3.1) | `account_quotas` + `session_snapshots` (both `source: "claude_statusline"`); prints the `latest` line |
| **Claude poller**: `src/claude_quota_api_poller.py` | `GET /api/oauth/usage` (Upstream §3.5) | `account_quotas` (`source: "claude_api"`) or `poll_errors` |
| **Codex poller**: `src/codex_quota_api_poller.py` | app-server `account/rateLimits/read` + `account/usage/read` (Upstream §4.3) | codex `account_quotas` (`source: "codex_app_server"`) or `poll_errors` |
| **Codex plan-history poller**: `src/codex_plan_history_poller.py` | ChatGPT backend `plan_limit_history?days=7` (Upstream §4.4) | codex `account_quotas` (`source: "codex_plan_limit_history"`) or `poll_errors` |
| **Telemetry receiver**: `src/telemetry_receiver.py` | Claude Code's OpenTelemetry events (Upstream §3.7) | `telemetry` (`source: "claude_otel"`, §4.2) |
| **Transcript reader**: `src/transcript_reader.py` | Claude Code transcripts `~/.claude/projects/**/*.jsonl` (Upstream §3.4); Codex session files `~/.codex/{sessions,archived_sessions}/**/*.jsonl` (Upstream §4.1) | Claude: `data/claude/intermediary.db` directly (local working tables, not data: `doc/design.md` §5); Codex: `requests` + `scans` in its sessions database (§6.3) |
| **Slots**: `src/aggregate_slots.py` | `intermediary.db`, the `telemetry` table | `slots` in `data/claude/aggregates/<this machine>.db` (§5) |
| **Receiver**: `src/receive_from_machine.py`, on the VM | the Mac's new slots, sent by `src/push_to_central.py` | `slots` in `data/claude/aggregates/chan-lescut-macbook-pro.db` (`doc/design.md` §6.4) |

The Codex status line (agent-statusline's `providers/codex-statusline-command.sh`) displays its own stdin reading and writes nothing of ours.

### 2.2 What each collector captures, by unit

| Collector | Quota % | Tokens | Spend $ |
|---|---|---|---|
| Statusline reader | ✅ 5h + 7d, account-wide, whole numbers | ❌ (already exact in transcripts, Upstream §3.4); prompt-cache statistics per session from 2026-09-30 | ✅ session cumulative, all models combined, list price, from 2026-09-29 |
| Claude poller | ✅ 5h + 7d, account-wide, whole numbers, full raw response | ❌ (the endpoint has none) | ❌ (`used_dollars` always `null`) |
| Codex poller | ✅ 5h + 7d, account-wide, whole numbers, full raw response | ✅ account-wide daily `dailyUsageBuckets` + lifetime total | ❌ |
| Telemetry receiver | ❌ | ✅ **per API request**, per model, with `query_source`, including the requests transcripts never record, from 2026-09-30 | ✅ per request, list price |
| Codex plan-history poller | ✅ finished 5h + 7d windows of the last 7 days, **fractional** (basis points), with their real start and end, full raw response, from 2026-09-30 | ❌ | ❌ |
| Transcript reader | ❌ | ✅ **per API request**, per model, with session, execution folder and entrypoint: every request a transcript records, back to the oldest transcript kept (365 days) | Claude: none computed (Claude Code's own `cost_usd` comes from telemetry, joined in `slots`); Codex: list price (`usage_db.PRICES`) |

### 2.3 When each collector writes a row

| Collector | Writes a row when | Skips when |
|---|---|---|
| Statusline reader | The render's stdin has `rate_limits` **and** the transcript's last 256 KB contain an `assistant` entry to date it (`observed_at`). Deduplicated per session: an account row only when `(observed_at, percents, resets)` differs from this session's last one, a session row only when `(session_cost_usd, prompt_cache, model_id)` does. A new API response with unchanged percents still adds a row | No `rate_limits` on stdin (non-subscriber, or a window just expired); no `assistant` entry in that window: then **nothing** is written; a re-render of a reading this session already wrote |
| Claude poller | The newest reading is older than its threshold (`doc/design.md` §3.4). A failed attempt goes to `poll_errors` (never synced) | Mac asleep (launchd fires once on wake) |
| Codex poller | Every tick while a Codex status line rendered in the last 90 s; otherwise when the last row is ≥ 5 min old | **Any Codex session file changed in the last 5 min**: the session file is then the fresher source (Upstream §4.1); Mac asleep |
| Telemetry receiver | Whenever a Claude session started after the install sends a batch (every few seconds while requests happen), interactive **and** `claude -p` | Receiver down (Claude Code drops the batch, no disk buffer); sessions started before the install |
| Transcript reader | Every 5 min; a request once its first transcript line is ≥ 15 min old (a request's lines span ≤ 292 s), each `request_id` once | Nothing new since the last run (a byte offset per file); Mac asleep |
| Codex plan-history poller | 24 h after its last successful attempt, 1 h after a failed one. A failed attempt goes to `poll_errors` | Mac asleep; independent of the Codex poller's skip rules |

Coverage that follows from this:

| Situation | Claude captured by | Codex captured by |
|---|---|---|
| Interactive session, messages flowing | Statusline reader, each new reading | Nothing in our log: the session file has it |
| Session open, no message sent yet | Statusline reader (stdin has `rate_limits` from startup, Upstream §3.1) | Poller, every 60 s |
| No status line anywhere | Poller, every 5 min | Poller, every 5 min |
| Headless `claude -p`, Agent SDK runs | Per-request tokens via the transcript reader, and $ via telemetry when the session loads `~/.claude/settings.json`; quota only through the next reading's meter movement | Only through the next reading's meter movement |
| Usage on other machines / clients | Next reading's meter movement | Next reading, plus `dailyUsageBuckets` |

---

## 3. Category 1 · Claude readings: `account_quotas`, `poll_errors`, view `latest`

`account_quotas` holds every Claude meter reading, statusline rows and poller rows, told apart by `source`, with typed columns extracted (`doc/design.md` §3.1). It continues `data/claude/account.jsonl` (172,389 lines on 2026-10-08, migrated verbatim) and, before 2026-09-30, `data/claude-quota-history.jsonl`. **Rows come in several shapes** (§3.1, §3.2 and their older formats): readers of `raw` must sniff the shape; the typed columns already do. `poll_errors` has the poller row's shape with `error` set.

### 3.1 Statusline rows: `source: "claude_statusline"`

About 89% of account rows **[verified]**. Every render that writes an account row also writes a session row (§4.1) for the rendering session, with the same `observed_at`:

```json
{"ts": 1790774229, "iso": "2026-09-30T13:17:09Z", "source": "claude_statusline", "observed_at": 1790773293,
 "five_hour_pct": 50, "seven_day_pct": 9, "five_hour_resets_at": "1790784600", "seven_day_resets_at": "1791226800",
 "observed_by_session": "8b301a30-..."}
```

| Field | Unit | Granularity | Meaning |
|---|---|---|---|
| `ts`, `iso` | epoch s / ISO | — | Append time (the column `written_ts`). **Not** when the reading was true |
| `observed_at` | epoch s | — | Timestamp of the transcript's last `assistant` entry (API response): when Claude Code's in-memory reading became true, or slightly before, since an unlogged `quota_check` request can also refresh it. **The only trustworthy time key** (the column `ts`), and the join key to session rows. Older rows can be dated late (§7.2) or an hour late during DST (§7.3) |
| `five_hour_pct`, `seven_day_pct` | % | Account-wide | Level within the current window, resets to 0 at window end. Written as received; always whole in practice (§7.1). A window missing from stdin is written as 0: stdin omits `five_hour` when no 5-hour window is open, and the poller's API then reports `utilization` 0 **[verified 2026-10-02]** |
| `five_hour_resets_at`, `seven_day_resets_at` | epoch s, as a string | Account-wide | Window end; stable while the window runs, so it doubles as a window id. `null` when absent |
| `observed_by_session` | — | — | The session that was rendering when the reading was taken. **Who observed it, not whose usage it is.** `null` when the payload had none; absent before 2026-09-29 |
| `machine` | — | — | The machine that took it, since 2026-10-10 (`doc/design.md` P1) |

**Headless `claude -p` writes no statusline rows**: it never renders a status line (verified 2026-09-30, §9).

### 3.2 Poller rows: `source: "claude_api"`

12,848 rows (11%) **[verified]**.

```json
{"ts": ..., "iso": ..., "source": "claude_api",
 "api": {"five_hour": {"utilization": 15, "resets_at": "2026-09-29T21:00:00.165686+00:00",
                       "limit_dollars": null, "used_dollars": null, "remaining_dollars": null, "locked_reason": null},
         "seven_day": {...}, "extra_usage": {...}, ...},
 "api_headers": {"Date": ..., "request-id": ..., ...},
 "error": null}
```

- `api` is the **full raw response**, unfiltered; `error` is always `null` in `account_quotas` (a failed attempt, with `error` saying which stage failed and why, is a `poll_errors` row).
- **The poller is unreliable: 8,516 of 12,848 attempts failed** (as of 2026-09-29): 6,239 SSL/network, 2,165 HTTP 429, 112 missing Keychain token **[verified]**. The statusline reader does about 92% of the real work.
- **Some failures before 2026-09-30 left no row**: `http.client.RemoteDisconnected` is an `OSError`, not a `URLError`, so it escaped the poller's handlers (7 crashes, the last on 2026-09-29) **[verified]**. Since then any `OSError` is a `network` error row.
- No `observed_at`: `ts` is the reading time.
- Older shapes: rows before 2026-08-30 have no `source` key (treat as `"claude_api"`); rows before 2026-08-26 carry `token_deltas` / `baseline` instead of `api_headers` (computed from transcripts by the old poller, never from the API).

### 3.3 The view `latest`, and the line the reader prints

`latest` is the freshest reading, any source, any machine, with the reset rule applied: `doc/design.md` §3.2 (columns, rule, example). The statusline reader prints it for agent-statusline as six `\034`-separated fields, `five_pct, five_reset, week_pct, week_reset, source, observed_at`: percents rounded (the display does integer arithmetic), a passed reset empty.

---

## 4. Category 2 · `session_snapshots` and `telemetry` (`data/claude/sessions_usages.db`)

### 4.1 `session_snapshots`: `source: "claude_statusline"`

The statusline reader's session rows, keyed by `session_id` (a column), from 2026-09-29/30 (older readings produce none). Never a percent.

```json
{"ts": 1790774229, "iso": "2026-09-30T13:17:09Z", "source": "claude_statusline", "observed_at": 1790773293,
 "model_id": "claude-haiku-4-5-20251001", "session_cost_usd": 0.0360382, "prompt_cache": {"warm": true, "requests": 2, "misses": 0, ...}}
```

| Field | Unit | Granularity | Meaning |
|---|---|---|---|
| `ts`, `iso`, `source`, `observed_at` | — | — | As on the account row written in the same render |
| `model_id` | — | Per session, current model | `model.id` from stdin; absent on rows migrated from before 2026-09-30 |
| `session_cost_usd` | USD, list price | Per session, all models | Cumulative since the session started (or its last `/clear`). The delta between two rows of one session, ordered by `observed_at`, is its spend in the interval |
| `prompt_cache` | object, raw | Per session, main conversation only | Claude Code's `prompt_cache` statistics exactly as on stdin (Upstream §3.1): `warm`, `ttl`, `expires_at`, `requests`, `misses`, `expected_rebuilds`, `hit_ratio`, `cache_write_tokens`, `miss_recache_tokens`, `last_miss_at`, `last_miss_cause`, `miss_causes`, `recache_tokens_if_cold`. Cumulative except the `last_*` / `warm` / `expires_at` fields. `null` before the session's first API response. `requests`, `hit_ratio` and `cache_write_tokens` match the transcript's main-conversation messages (178 / 0.98942 / 344,258 vs 178 / 0.98944 / 344,289 on a live session **[verified]**); the miss diagnostics and `ttl` / `expires_at` are kept nowhere else. `hit_ratio` is a cache ratio, not a quota percent |

A session row is about 300 bytes; a session rendering every 10 s adds about 2.6 MB a day.

### 4.2 `telemetry`: `source: "claude_otel"`

One row per Claude API request (and per API error / refusal / exhausted retry), from 2026-09-30, with typed columns (`ts` = event time, `event`, `session_id`, `request_id`, `model`, `query_source`, the four token counts, `cost_usd`, `received_ts`) and the JSON below in `raw`. Events without a usable session id are kept with `session_id` NULL. A batch Claude Code re-sends is stored again (its `received_at` differs), so count requests through the view `telemetry_requests`, which keeps one event per `request_id` (as `slots` does). The receiver listens on `127.0.0.1:4318`; nothing polls: Claude Code sends the events itself, because `install.sh` adds these keys to the `env` object of `~/.claude/settings.json` (`src/claude_telemetry_env.json`; `uninstall.sh` removes them):

```text
CLAUDE_CODE_ENABLE_TELEMETRY=1            OTEL_LOGS_EXPORTER=otlp      OTEL_METRICS_EXPORTER=none
OTEL_EXPORTER_OTLP_PROTOCOL=http/json     OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
```

Only sessions started after those keys were written send events. If the receiver is down, Claude Code drops the batch; nothing is buffered on disk.

```json
{"source": "claude_otel", "observed_at": 1790773229, "received_at": 1790773236, "event": "api_request", "time_unix_nano": 1790773229123000000,
 "attributes": {"model": "claude-haiku-4-5-20251001", "query_source": "generate_session_title",
                "input_tokens": 897, "output_tokens": 9, "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.000942, "cost_usd_micros": 942, "duration_ms": ..., "ttft_ms": ..., "speed": "normal",
                "request_id": "req_...", "client_request_id": "...", "session.id": "8b301a30-...", "prompt.id": "...",
                "event.sequence": ..., "event.timestamp": "...", "terminal.type": "...",
                "organization.id": "...", "user.account_uuid": "...", "user.email": "...", "user.id": "..."},
 "resource": {"service.name": "claude-code", "service.version": "2.1.284", "os.type": "...", "os.version": "...", "host.arch": "..."}}
```

| Field | Unit | Granularity | Meaning |
|---|---|---|---|
| `event` | — | — | `api_request`, `api_error`, `api_refusal` or `api_retries_exhausted`. Every other telemetry event (prompts, tools, hooks) is dropped on arrival |
| `attributes.*_tokens` | tokens | **Per request** | That request alone (a delta). `cache_creation_tokens` is not split into 5m / 1h |
| `attributes.cost_usd` | USD, list price | Per request | Claude Code's own figure; `cost_usd_micros` is the same as an integer |
| `attributes.query_source` | — | Per request | What the request was for. Seen: `repl_main_thread` (the conversation), `generate_session_title`, `prompt_suggestion`, `sdk` (`claude -p`) |
| `attributes.session.id` | — | Per session | Joins to the transcript's file name and to account rows' `observed_by_session` |
| `attributes.request_id` | — | Per request | Joins to the transcript's `requestId` for the requests the transcript has |
| `time_unix_nano`, `received_at` | ns / s epoch | — | When the event was recorded, and when the receiver wrote it |
| `resource.*` | — | Per process | Claude Code version and host |

What it shows that nothing else does **[verified 2026-09-30, one interactive session]**: the four `api_request` events summed to exactly the session's `cost-state` (1,267 / 286 / 94,492 / 11,946 tokens, $0.0360382), while the transcript held only the two `repl_main_thread` requests: `generate_session_title` and `prompt_suggestion` exist only here. A headless call's single event matched its `-p` result to the dollar ($0.0210743).

Attribution attributes include the account's `user.email`; the file stays local, like everything else under `data/`.

---

## 5. Category 3 · `slots`: how each column is counted

The schema, the key and an example: `doc/design.md` §6.1; how a slot is built and when it is final: §6.2 there. Since 2026-10-10, rebuilt then from every transcript (back to 2026-07-30).

| Column | Counted how |
|---|---|
| `ts`, `dt` | A request belongs to the slot of its first transcript line; a telemetry-only request, to the slot of its event |
| `machine` | `usage_db.machine_name()` (the skill `machine-name`) |
| `folder`, `entrypoint`, `model` | The session's `cwd`, `entrypoint` and `message.model` from the transcript; a telemetry-only request takes the folder and entrypoint of the same session's transcript requests |
| `input_tokens` … `cache_write_1h_tokens` | Each request from its line with the **largest** `output_tokens` (a streamed reply repeats the request over several lines with growing output) |
| `cache_write_unknown_tokens` | Cache writes of the requests only telemetry has (prompt suggestions, conversation summaries, web search, titles, compaction) |
| `usd` | Telemetry's `cost_usd`, joined on `request_id` through `telemetry_requests`; NULL when no request of the slot has one (everything before 2026-09-30) |
| `tokens_without_usd` | Every token of the requests with no `cost_usd` (Agent SDK sessions, sessions started before telemetry) |

---

## 6. Codex: readings, plan history, turns

Codex keeps its pre-design jobs, on the Mac only (`doc/design.md` §13).

### 6.1 Readings: `source: "codex_app_server"` in `data/codex/account_quotas.db`

`raw` continues `data/codex/account.jsonl` (6,884 lines on 2026-10-08, migrated verbatim). The typed columns map `primary` / `secondary` by `windowDurationMins` (300 → five_hour, 10080 → seven_day).

```text
ts, iso, source: "codex_app_server", error
codex_rate_limits.rateLimits.primary.usedPercent / .windowDurationMins (300)   / .resetsAt
codex_rate_limits.rateLimits.secondary.usedPercent / .windowDurationMins (10080) / .resetsAt
codex_rate_limits.rateLimits.planType = "plus", .credits, .rateLimitReachedType
codex_rate_limits.rateLimitsByLimitId, .rateLimitResetCredits.availableCount / .credits[]
codex_usage.summary.lifetimeTokens / peakDailyTokens / longestRunningTurnSec / currentStreakDays / longestStreakDays
codex_usage.dailyUsageBuckets[] = [{"startDate": "2026-08-10", "tokens": 144710}, ...]
```

| Field | Unit | Granularity | Cumulative / delta |
|---|---|---|---|
| `primary.usedPercent`, `secondary.usedPercent` | % (integer) | Account-wide | Level within the window |
| `dailyUsageBuckets[].tokens` | tokens | Account-wide, all clients, per day | Delta per day |
| `summary.lifetimeTokens` | tokens | Account-wide | Cumulative, account lifetime |

Both objects are the **full raw RPC results**. `dailyUsageBuckets` is the only absolute account-wide token signal we record for either agent (Upstream §4.3). Rows with `usedPercent == 0` and `resetsAt ≈ ts + window span` mean no window is open: drop them (Upstream §5.1).

### 6.2 Plan history: `source: "codex_plan_limit_history"`, same table

About one row a day, from 2026-09-30. No current reading: the percent columns are NULL and `latest` skips them.

```json
{"ts": 1790772845, "iso": "2026-09-30T12:14:05Z", "source": "codex_plan_limit_history",
 "plan_limit_history": {"data_as_of": "2026-09-30T00:00:00Z", "coverage_start": "2026-09-23T00:00:00Z",
                        "coverage_complete": false, "approximate": true, "boundary_tolerance_seconds": 60,
                        "periods": [{"window_minutes": 300, "starts_at": "2026-09-24T18:32:13.056000Z",
                                     "ends_at": "2026-09-24T23:32:13.056000Z", "used_basis_points": 118.790896,
                                     "accounting_complete": true, ...}, ...]},
 "error": null}
```

| Field | Unit | Granularity | Meaning |
|---|---|---|---|
| `periods[].used_basis_points` | hundredths of a percent, fractional | Account-wide, per finished window | Final usage of that window. `118.79` = 1.1879%. The live whole percent is `round(used_basis_points / 100)` **[verified on 3 windows]** |
| `periods[].starts_at`, `ends_at` | ISO, UTC | Per window | The window's **real** span. A window reset early shows its actual end, where every live reading kept reporting the scheduled one **[verified: a 7-day window scheduled for 09-28 09:05 ended 09-26 17:09]** |
| `periods[].window_minutes` | minutes | — | 300 (5 h) or 10080 (7 d) |
| `periods[].accounting_complete` | bool | Per window | Whether the backend considers the window's usage final |
| `data_as_of`, `coverage_start`, `coverage_complete`, `approximate`, `boundary_tolerance_seconds` | — | Per response | Freshness and coverage of the whole answer; `data_as_of` lagged to the start of the current UTC day |

Only **finished** windows appear, and only windows that had usage. Each fetch overlaps the previous one by 6 days: dedupe periods by `(window_minutes, starts_at)` and keep the latest fetch.

### 6.3 Turns: `requests` + `scans` in `data/codex/sessions_usages.db`

Since 2026-10-08, with the views `usage_requests` / `usage_5m`: one row per turn (`token_count` event), `request_id` = `<thread id>:<running total>` (a repeated event or an archived copy is skipped), `session_id` = the thread id, `folder` and `model` from `session_meta` / the latest `turn_context`, `entrypoint` = `session_meta.source` (`cli`, `vscode`, `exec`; `subagent` for a subagent thread), `input_tokens` without the cached part, `cache_read_tokens` = `cached_input_tokens`, `output_tokens` including reasoning, `machine`, and `usd` from `usage_db.PRICES` for `gpt-5.6-sol` only. A turn is stored as soon as its event is written. Codex sends no telemetry and has no statusline session snapshot.

---

## 7. Traps in our rows

Traps of the upstream data itself (Codex's fake idle countdown, windows opened by a message, moving Codex resets): Upstream §5.

### 7.1 Resolution is 1%

Every percent we record is a whole number in practice (0 fractional among 255,156 statusline values and 11,314 poller values **[verified]**), because the upstream quota is whole-percent (Upstream §3.6). What 1% weighs in $, per plan: `adhoc_quotas_analysis/CONCLUSIONS.md` §2. The only finer source is Codex's plan history (§6.2).

### 7.2 Stale readings: keep each window's running maximum

**Within one window the true percentage never decreases: keep only the *first* reading of each new running maximum, and discard everything at or below it.** `adhoc_quotas_analysis/load.py`'s `windows()` applies it (a window's `steps`). It is a correctness fix, not an optimisation: idle renderers re-sent readings taken much earlier, so the same minute can hold:

```text
five_hour_pct 18, observed_at 1790170589   <- real
five_hour_pct  0, observed_at 1790078292   <- a day-old reading re-sent by an idle renderer
```

- **Key readings by `observed_at`, never by row order or by `written_ts`.**
- Apply the running maximum **per window**, keyed by its reset time.
- **100,158 usable Claude rows collapse to 687** over 30 days: 146×, about 30 rows a day **[verified]**.
- **Statusline rows before 2026-10-04 can be dated late**: their `observed_at` was the last transcript entry of any type, so an attachment or user message after the last API response moved it forward (session `bc36b32e`'s reading taken at 12:58:48Z on 2026-09-30 is dated 13:37:46Z **[verified]**), and they repeat every ~10 s while a session stays open (96% were exact repeats). The running maximum handles both.

### 7.3 Statusline `observed_at` one hour late during daylight-saving time, before 2026-09-30

The old push script (then `agent-statusline`'s `src/statusline/push-claude-quota.sh`) converted the transcript timestamp with jq 1.6's `fromdateiso8601`, which on this Mac returns `2026-09-30T11:00:00Z` as 1790769600 instead of 1790766000: **+3,600 s** whenever the local zone is in DST **[verified]**. Ordering among statusline rows is preserved, but they look an hour fresher than poller rows. **When comparing them with poller rows, subtract 3,600 s from statusline rows written during DST before the first deploy after 2026-09-30.** The code now converts by calendar arithmetic (`tests/test_push_claude_quota.sh`).

### 7.4 Codex gaps during active use

The Codex poller skips while a session file is fresh (§2.3), so the Codex readings are thinnest exactly when usage is highest. For those stretches, read the session files' `token_count` events (Upstream §4.1) or the turns (§6.3).

### 7.5 No schema version

`raw` carries several row shapes (§3.2's older formats, §3.1's added fields, §6's two sources), migrated as-is rather than normalised. Readers must sniff the shape; a missing key means "older row", never zero. Which shape since when: `doc/changes.md`.

### 7.6 Retention

None: rows are kept indefinitely. Revisit if `data/claude/` passes ~1 GB (178 MB after the 2026-10-08 migration). Claude Code deletes its transcripts after 365 days ([Todoist: usage beyond transcript retention](https://app.todoist.com/app/task/6hm2wFCFJ2CGcx7V)).

---

## 8. Available upstream but not captured

| Data | Source | Why it matters |
|---|---|---|
| Codex usage per day × model × client, relative | Backend analytics (Upstream §4.4) | The only per-client split; relative only |
| Claude quota status beyond the percent (`status`, `representative-claim`, overage) | `/v1/messages` headers (Upstream §3.6) | Only reachable through a billed request of our own |

Deliberately not captured because it already persists elsewhere: Codex's quota as each session file records it, turn by turn (Upstream §4.1; [Todoist: codex in the four categories](https://app.todoist.com/app/task/6hm2wFCjhFFJqQQV)).

---

## 9. How to re-verify

| Claim | How |
|---|---|
| Row shapes and counts | `python3 -c "import sqlite3,json,collections;d=sqlite3.connect('data/claude/account_quotas.db');print(collections.Counter(tuple(sorted(json.loads(r))) for (r,) in d.execute('SELECT raw FROM account_quotas')).most_common())"` |
| Poller failure rate | `SELECT source, COUNT(*) FROM poll_errors GROUP BY source` against the same in `account_quotas` |
| Nothing lost in the 2026-10-08 migration | `git checkout 12956dc -- adhoc_quotas_analysis/migrate_to_sqlite.py`, then `python3 adhoc_quotas_analysis/migrate_to_sqlite.py --logs data/_archive/pre-sqlite-20261008T090432Z/logs` after copying the archived JSONL back into a scratch runtime (`--runtime`): every line must be found, 0 inserted |
| Running-maximum compression | Count the `steps` of `load.windows()` (`adhoc_quotas_analysis/load.py`) against the readings |
| Fractional percent | `SELECT COUNT(*) FROM account_quotas WHERE five_hour_pct != CAST(five_hour_pct AS INT)` |
| Collector behaviour | `bash tests/run.sh` (`tests/README.md` lists what each file covers) |
| Scope rule holds | No percent-like key path (`pct\|percent\|utiliz\|basis_points`) in any `raw` of `sessions_usages.db`; no `account_quotas.raw` with `session_cost_usd`, `prompt_cache` or a bare `session_id`. Both gates refuse such rows |
| Transcript totals are exact | `python3 adhoc_quotas_analysis/verify_token_costs.py` (Upstream §3.4) |
| The slots add up to their requests | `python3 adhoc_quotas_analysis/migrate_to_target.py check-intermediary` on each machine: the slots written so far against a separate count over `intermediary.db` + telemetry (requests, tokens, $, tokens without $); exits 1 on a difference |
| Both machines hold the same readings | `SELECT machine, COUNT(*) FROM account_quotas GROUP BY 1` on each: equal within one sync (2 min) |

When any of this changes, add a row to `doc/changes.md` and update the "Last verified" line above.
