# Usage data reference — what `agent-usage-tracker` captures, how, and where

**Canonical description of the usage data this repository records: which writer captures what, when, from which upstream source, in which file, with which shape and which traps.** For the inventory of everything that exists upstream — including what we do *not* capture — see the companion file `USAGE_DATA_SOURCES.md`, whose section numbers are cited here as "Sources §n". Other projects link to these two files rather than restating them.

Markers: **[verified]** was measured on this machine's data; **[docs]** comes from official documentation.

*Last verified: 2026-10-10 (machine names merged: per-table counts, Mac vs VM copy, identical up to the rows written since the last push); 2026-10-08 (central store on the VM: first push, per-table counts identical; move to SQLite: databases, gates, views, the transcript reader, the migration's counts); 2026-10-04 (push dating and dedup, source names, Claude poller cadence, error rows moved out of data/); 2026-09-30 for everything else. Row counts are as of the 2026-10-08 migration: 172,250 Claude readings, 6,895 Codex readings, 10,889 failed poll attempts, 35,808 session snapshots, 2,714 telemetry events, 18,151 transcript requests.*

---

## 1. Overview

All data lives under `~/opt/agent-usage-tracker/` (the deployed runtime; the code lives in `~/dev/agent-usage-tracker`). The status line is a separate project, `agent-statusline`: it feeds this one by piping each Claude render's raw stdin payload into `bin/ingest-claude-statusline.sh`, and displays this project's `state/quota/claude` (README.md's "Contract with agent-statusline").

**Usage data is in two SQLite databases per agent, split by scope** (since 2026-10-08; JSONL files before, same split since 2026-09-30):

```
data/
├── claude/
│   ├── account_quotas.db      account scope: account_quotas (readings), poll_errors (failed poll attempts), view latest
│   └── sessions_usages.db     session scope: requests, telemetry, session_snapshots, scans, views usage_requests and usage_5m
├── codex/
│   └── account_quotas.db      account scope (no Codex sessions database yet - see below)
└── _archive/                  originals kept by one-time migrations; pre-sqlite-20261008T090432Z/ holds every JSONL file the databases were built from
```

| Table | One row per | Written by (through its gate) |
|---|---|---|
| `account_quotas` | Quota reading (§2, §3) | Statusline payload reader, the three pollers |
| `poll_errors` | Failed poll attempt (same JSON shape, `error` set) | The pollers |
| `session_snapshots` | Session's cumulative cost, model and cache statistics at one moment (§2.1) | Statusline payload reader |
| `telemetry` | Claude Code OpenTelemetry usage event (§9) | Telemetry receiver |
| `requests` | API request found in a transcript (§10) | Transcript reader |
| `scans` | Transcript-reader run, with `complete_through_ts` | Transcript reader |

**Common to every table:** `ts` (Unix epoch, when the row was true: `observed_at` for push and telemetry rows, the poll time for poll rows, the request's first line for requests) and `dt` (the same instant, ISO 8601 UTC) come first. Every table except `requests` and `scans` keeps the row's JSON **verbatim in `raw`**: for migrated rows the original JSONL line byte for byte, for new rows the same shape the JSONL writer produced. So §2, §3 and §9 below describe `raw`, and any old JSONL reader is served by `SELECT raw FROM <table> ORDER BY rowid`. Every row is insert-only; `row_hash` (SHA-1 of `raw`, UNIQUE) or `request_id` (UNIQUE) makes re-inserting it a no-op.

**Only the gates write.** `src/ingest_account_quota.py` writes `account_quotas.db`, `src/ingest_session_usage.py` writes `sessions_usages.db`; collectors hand rows to them. The gates reject a malformed row, an account row with per-session fields, and a session row with any percent-like key.

**Reading: open the database normally and run `PRAGMA query_only = ON`, not a `mode=ro` URI.** macOS's system SQLite intermittently refuses a mode=ro open of these WAL databases ("unable to open database file") when no connection holds the `-shm` file at that instant, which is most of the time [verified 2026-10-08].

**The scope rule — percent is account-scope only.** The quota meter is one account-level number: when two sessions spend at once, there is no per-session percentage — not a hidden one, an undefined one. So:

| Database | Carries | Never carries |
|---|---|---|
| `data/<agent>/account_quotas.db` | Meter percent, reset times, `source`, and `observed_by_session` on push rows | Per-session cost or token totals |
| `data/<agent>/sessions_usages.db` | Tokens, USD, model, execution folder, entrypoint, cache statistics | **A quota percent, under any name** |

The two scopes join on **`ts`** (a time join); no field is duplicated across them. Downstream projects must read this rule from here rather than infer it. Nothing in this repository estimates or apportions a per-session percentage, and nothing will: any such figure is derived downstream, with its uncertainty stated there.

`observed_by_session` on an account row names the session that was rendering when the reading was taken — a free liveness signal — **not** the session whose usage it is.

Codex has a sessions database since 2026-10-08 with one table in use, `requests`: one row per turn, read from Codex's own `~/.codex/sessions/` files (§10). Codex sends no telemetry and has no status-line session snapshot.

Besides the databases, one small file: **`state/quota/claude`**, the latest Claude reading, read live by agent-statusline, auto-apply and this repo's `claude-quota.sh` (§4).

### 1.1 Writers

| Writer | Scheduled by | Upstream source | Writes |
|---|---|---|---|
| **Claude push** — `src/statusline_payload_reader.py`, run in the foreground by `bin/ingest-claude-statusline.sh` (about 28 ms per render) | Every Claude status-line render: agent-statusline's Claude provider pipes its raw stdin payload in | Statusline stdin (Sources §3.1) | `account_quotas` + `session_snapshots` (both `source: "claude_statusline"`) + `state/quota/claude` |
| **Claude poller** — `src/claude_quota_api_poller.py` | Job `claude-quota` (job-runner), 60 s tick, Mac and VM | `GET /api/oauth/usage` (Sources §3.5) | `account_quotas` (`source: "claude_api"`) or `poll_errors` + `state/quota/claude` |
| **Codex poller** — `src/codex_quota_api_poller.py` | Job `codex-quota`, 60 s tick, Mac only | app-server `account/rateLimits/read` + `account/usage/read` (Sources §4.3) | codex `account_quotas` (`source: "codex_app_server"`) or `poll_errors` |
| **Codex plan-history poller** — `src/codex_plan_history_poller.py` | Job `codex-plan-history`, hourly tick, Mac only | ChatGPT backend `plan_limit_history?days=7` (Sources §4.4) | codex `account_quotas` (`source: "codex_plan_limit_history"`) or `poll_errors` |
| **Telemetry receiver** — `src/telemetry_receiver.py` | Its own LaunchAgent, always running (`KeepAlive`); Claude Code pushes to it | Claude Code's OpenTelemetry events (Sources §3.7) | `telemetry` (`source: "claude_otel"`, §9) |
| **Transcript reader** — `src/transcript_reader.py` | Job `transcripts`, every 5 min | Claude Code transcripts `~/.claude/projects/**/*.jsonl` (Sources §3.4) and Codex session files `~/.codex/{sessions,archived_sessions}/**/*.jsonl` (Sources §4.1) | `requests` + `scans` in each agent's sessions database (§10) |
| **Push** — `src/push_to_central.py` | Its own scheduled job on every machine, every 5 min | This machine's databases (new rows only) | Nothing locally except `state/push/watermarks.json`; sends rows to the VM (§11) |
| **Central receiver** — `src/receive_from_machine.py` | On the VM, over ssh, per push | Another machine's pushed rows | Both gates, into `central/<machine>/data/<agent>/` (§11) |
| **Codex status line** — agent-statusline's `providers/codex-statusline-command.sh`, not this project | Every render of the patched Codex TUI | Codex stdin | Displays its own stdin reading; writes nothing of ours |

### 1.2 What each writer captures, by unit

| Writer | Quota % | Tokens | Spend $ |
|---|---|---|---|
| Claude push | ✅ 5h + 7d, account-wide, whole numbers | ❌ (already exact in transcripts, Sources §3.4); prompt-cache statistics per session from 2026-09-30 | ✅ session cumulative, all models combined, list price — from the first deploy after 2026-09-29 |
| Claude poller | ✅ 5h + 7d, account-wide, whole numbers, full raw response | ❌ (the endpoint has none) | ❌ (`used_dollars` always `null`) |
| Codex poller | ✅ 5h + 7d, account-wide, whole numbers, full raw response | ✅ account-wide daily buckets + lifetime total | ❌ |
| Telemetry receiver | ❌ | ✅ **per API request**, per model, with `query_source` — including the requests transcripts never record — from 2026-09-30 | ✅ per request, list price |
| Codex plan-history poller | ✅ finished 5h + 7d windows of the last 7 days, **fractional** (basis points), with their real start and end, full raw response — from 2026-09-30 | ❌ | ❌ |
| Transcript reader | ❌ | ✅ **per API request**, per model, with session, execution folder and entrypoint — every request a transcript records, back to the oldest transcript kept (365 days) | ✅ per request, list price (`usage_db.PRICES`, exact, Sources §3.4) |

### 1.3 When each writer actually writes

| Writer | Writes a history row when | Skips when |
|---|---|---|
| Claude push | The render's stdin has `rate_limits` **and** the transcript's last 256 KB contain an `assistant` entry to date it (`observed_at`). Deduplicated per session since 2026-10-04: an account row only when `(observed_at, percents, resets)` differs from this session's last one, a session row only when `(session_cost_usd, prompt_cache, model_id)` does. A new API response with unchanged percents still adds a row. | No `rate_limits` on stdin (non-subscriber, or a window just expired); no `assistant` entry in that window: then **nothing** is written, state file included; a re-render of a reading this session already wrote |
| Claude poller | When the freshest reading (`state/quota/claude`, any source, any machine) is ≥ 110 s old (Mac) / 170 s (VM) while a Claude status line rendered here in the last 90 s; otherwise ≥ 300 s / 360 s. The Mac first takes the VM's reading if fresh enough, and hands the VM each of its own readings (`--peer`). A 429's Retry-After is waited out on both machines. A failed attempt goes to `poll_errors`. | Mac asleep (launchd fires once on wake) |
| Codex poller | Every tick while a Codex status line rendered in the last 90 s; otherwise when the last row is ≥ 5 min old | **Any Codex session file changed in the last 5 min** — the session file is then the fresher source (Sources §4.1); Mac asleep |
| Telemetry receiver | Whenever a Claude session started after the install sends a batch (every few seconds while requests happen), interactive **and** `claude -p` | Receiver down (Claude Code drops the batch, no disk buffer); sessions started before the install |
| Transcript reader | Every 5 min; a request once its first transcript line is ≥ 15 min old (a request's lines span ≤ 292 s), each `request_id` once | Nothing new since the last run (a byte offset per file); Mac asleep |
| Codex plan-history poller | 24 h after its last successful attempt, 1 h after a failed one (job-runner's statuses since 2026-10-10, the database before). A failed attempt goes to `poll_errors`. | Mac asleep; independent of the Codex poller's skip rules |

Coverage that follows from this:

| Situation | Claude captured by | Codex captured by |
|---|---|---|
| Interactive session, messages flowing | Push, each new reading | Nothing in our log — the session file has it |
| Session open, no message sent yet | Push (stdin has `rate_limits` from startup, Sources §3.1) | Poller, every 60 s |
| No status line anywhere | Poller, every 5 min | Poller, every 5 min |
| Headless `claude -p`, Agent SDK runs | Per-request tokens and $ via the transcript reader (and telemetry when the session loads `~/.claude/settings.json`); quota only through the next reading's meter movement | Only through the next reading's meter movement |
| Usage on other machines / clients | Next reading's meter movement | Next reading, plus `dailyUsageBuckets` |

---

## 2. Claude rows: `account_quotas` and `session_snapshots`

The JSON shapes below are what the `raw` column holds. `account_quotas` holds every Claude meter reading: push rows and poller rows, disambiguated by `source`, with typed columns extracted (`five_hour_pct`, `five_hour_resets_ts`, `seven_day_pct`, `seven_day_resets_ts`, `observed_by_session`, `written_ts` = the old `ts`). It continues `data/claude/account.jsonl` (172,389 lines on 2026-10-08, migrated verbatim) and, before 2026-09-30, `data/claude-quota-history.jsonl`. **Rows still come in several shapes** (§2.1 push rows, §2.2 poller rows and their older formats) — readers of `raw` must sniff the shape; the typed columns already do.

`session_snapshots` holds the push path's session rows (§2.1), keyed by `session_id` (a column: the old files carried it in their name only). Telemetry rows (§9) are in `telemetry`.

### 2.1 Push rows — `source: "claude_statusline"`

About 89% of account rows **[verified]**. From 2026-09-30, every render that writes an account row also writes a session row for the rendering session (when its `session_id` is a plain UUID-like token), with the same `observed_at`:

```json
account_quotas.raw:
{"ts": 1790774229, "iso": "2026-09-30T13:17:09Z", "source": "claude_statusline", "observed_at": 1790773293,
 "five_hour_pct": 50, "seven_day_pct": 9, "five_hour_resets_at": "1790784600", "seven_day_resets_at": "1791226800",
 "observed_by_session": "8b301a30-..."}

session_snapshots.raw (session_id 8b301a30-...):
{"ts": 1790774229, "iso": "2026-09-30T13:17:09Z", "source": "claude_statusline", "observed_at": 1790773293,
 "model_id": "claude-haiku-4-5-20251001", "session_cost_usd": 0.0360382, "prompt_cache": {"warm": true, "requests": 2, "misses": 0, ...}}
```

Account row:

| Field | Unit | Granularity | Meaning |
|---|---|---|---|
| `ts`, `iso` | epoch s / ISO | — | Append time. **Not** when the reading was true |
| `observed_at` | epoch s | — | Timestamp of the transcript's last `assistant` entry (API response): when Claude Code's in-memory reading became true, or slightly before, since an unlogged `quota_check` request can also refresh it. **The only trustworthy time key**, and the join key to session rows. Before 2026-10-04 it was the last entry of any type, so older rows can be dated later than the reading (§5.2); pre-fix rows are also one hour late during DST (§5.3) |
| `five_hour_pct`, `seven_day_pct` | % | Account-wide | Level within the current window, resets to 0 at window end. Written as received; always whole in practice (§5.1). A window missing from stdin is written as 0: that is what it is, since stdin omits `five_hour` when no 5-hour window is open and the poller's API then reports `utilization` 0 **[verified 2026-10-02]** |
| `five_hour_resets_at`, `seven_day_resets_at` | epoch s, as a string | Account-wide | Window end; stable while the window runs, so it doubles as a window id. `null` when absent |
| `observed_by_session` | — | — | The session that was rendering when the reading was taken. **Who observed it, not whose usage it is.** `null` when the payload had none. Absent on rows before 2026-09-29's deploy |

Session row (never a percent):

| Field | Unit | Granularity | Meaning |
|---|---|---|---|
| `ts`, `iso`, `source`, `observed_at` | — | — | As on the account row written in the same render |
| `model_id` | — | Per session, current model | `model.id` from stdin; absent on rows migrated from before 2026-09-30 |
| `session_cost_usd` | USD, list price | Per session, all models | Cumulative since the session started (or its last `/clear`). The delta between two rows of one file, ordered by `observed_at`, is that session's spend in the interval |
| `prompt_cache` | object, raw | Per session, main conversation only | Claude Code's `prompt_cache` statistics exactly as on stdin (Sources §3.1): `warm`, `ttl`, `expires_at`, `requests`, `misses`, `expected_rebuilds`, `hit_ratio`, `cache_write_tokens`, `miss_recache_tokens`, `last_miss_at`, `last_miss_cause`, `miss_causes`, `recache_tokens_if_cold`. Cumulative for the session except the `last_*` / `warm` / `expires_at` snapshot fields. `null` before the session's first API response. `requests`, `hit_ratio` and `cache_write_tokens` match what the transcript's main-conversation messages give (178 / 0.98942 / 344,258 vs 178 / 0.98944 / 344,289 on a live session **[verified]**); the miss diagnostics and `ttl` / `expires_at` are persisted nowhere else. `hit_ratio` is a cache ratio, not a quota percent |

Session rows exist only from the 2026-09-29/30 deploys: rows written before carried no session fields and produce no session file (none is synthesised). A session row is about 300 bytes; a session rendering every 10 s adds about 2.6 MB a day to its file.

**Headless `claude -p` writes no push rows and no session-file push rows** — it never renders a status line (verified 2026-09-30, §8). Its session file, if any, holds only telemetry rows (§9).

### 2.2 Poller rows — `source: "claude_api"`

12,848 rows (11%) **[verified]**.

```json
{"ts": ..., "iso": ..., "source": "claude_api",
 "api": {"five_hour": {"utilization": 15, "resets_at": "2026-09-29T21:00:00.165686+00:00",
                       "limit_dollars": null, "used_dollars": null, "remaining_dollars": null, "locked_reason": null},
         "seven_day": {...}, "extra_usage": {...}, ...},
 "api_headers": {"Date": ..., "request-id": ..., ...},
 "error": null}
```

- `api` is the **full raw response**, unfiltered; `error` is always `null` in `data/` (a failed attempt's row, with `error` saying which stage failed and why, is in `logs/claude-poll-errors.jsonl`, §1).
- **The poller is unreliable: 8,516 of 12,848 attempts failed** (as of 2026-09-29, counted when failures were still rows here) — 6,239 SSL/network, 2,165 HTTP 429, 112 missing Keychain token **[verified]**. The push path does ~92% of the real work.
- **Some failures before 2026-09-30 left no row.** `http.client.RemoteDisconnected` (the server closing the connection) is an `OSError`, not a `URLError`, so it escaped the poller's handlers: the run crashed and wrote nothing. 7 such crashes are in `logs/quota-poll.err`, the last on 2026-09-29 **[verified]**. Since the 2026-09-30 fix, any `OSError` is logged as a `network` error row (in the error log since 2026-10-04).
- No `observed_at`: `ts` is the reading time.
- Older shapes: rows before 2026-08-30 have no `source` key (treat as `"claude_api"`; none were left in the live file on 2026-10-04); rows before 2026-08-26 carry `token_deltas` / `baseline` instead of `api_headers`. Those token deltas were never from the API — the old poller computed them from transcripts, and they were dropped as redundant.

---

## 3. Codex rows: `account_quotas` in `data/codex/account_quotas.db`

The JSON shapes below are what `raw` holds. It continues `data/codex/account.jsonl` (6,884 lines on 2026-10-08, migrated verbatim) and, before 2026-09-30, `data/codex-quota-history.jsonl`. Two writers, disambiguated by `source`. The typed columns map `primary` / `secondary` by `windowDurationMins` (300 → five_hour, 10080 → seven_day); plan-history rows have no current reading, so their percent columns are NULL and the `latest` view skips them.

### 3.1 Poller rows — `source: "codex_app_server"`

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

Both objects are the **full raw RPC results**. `dailyUsageBuckets` is the only absolute account-wide token signal we record for either agent (Sources §4.3).

### 3.2 Plan-history rows — `source: "codex_plan_limit_history"`

About one row a day, from 2026-09-30.

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

---

## 4. `state/quota/*` — the latest reading

Not history; one line, overwritten; fields separated by the ASCII FS character (`\034`).

`state/quota/claude` (this project's) has six fields: `five_pct, five_reset, week_pct, week_reset, source, observed_at`, resets as epoch seconds. Written only by the account gate, for every new Claude reading; it is the `latest` view of `account_quotas.db` kept as a file, because agent-statusline (every render), auto-apply and smart-orchestrator read it. The gate compares their reading's `observed_at` with the stored one and overwrite only if newer, so the freshest reading wins regardless of write order and every open session converges on the same number. Percentages are **rounded** here, because the display does integer arithmetic. `source` is `claude_statusline` (push) or `claude_api` (poller), the same names as the rows' `source`; information only: agent-statusline reads the file whatever the source. Until 2026-10-04 it was `X` / `P`, and a push reading with no transcript date was stamped *now*, so an idle session's frozen reading could hold the file.

`~/opt/agent-statusline/state/quota/codex` (agent-statusline's, listed for completeness; absent on 2026-10-08) had **four** fields: `five_pct, five_reset, week_pct, week_reset`, with the resets as the TUI's display strings (e.g. `16:05`, `11:05 on 28 Sep`), not epochs, and no `source` or `observed_at`. It is written only by the Codex status line, at most once per 60 s, with freshness tracked in a sidecar `state/quota/codex.timestamp` file rather than by comparing readings. agent-statusline's `state/heartbeat/{claude,codex}` are touched on every render; this project's pollers read their mtime to tell a status line is live.

---

## 5. Traps every consumer must handle

### 5.1 Resolution is 1%

Every percent we record is a whole number in practice — 0 fractional among 255,156 push values and 11,314 poller values **[verified]** — because the upstream quota is whole-percent (Sources §3.6). 1% of a 5-hour window ≈ $0.32 on Claude, ≈ $0.21 on Codex; 1% of a week ≈ $2.8 / $1.3. The only finer source is Codex's `plan_limit_history`, which we do not capture (§7).

### 5.2 Stale readings, and the envelope rule

**Within one quota window the true percentage never decreases, so keep only the *first* reading of each new running maximum, and discard everything at or below it.** The name comes from `adhoc_quotas_analysis/quota_model.py`'s `envelope()`.

It is a correctness fix, not an optimisation. Idle renderers re-push readings taken much earlier, so the same minute can hold:

```text
five_hour_pct 18, observed_at 1790170589   <- real
five_hour_pct  0, observed_at 1790078292   <- a day-old reading re-pushed by an idle renderer
```

**[verified]**

- **Key readings by `observed_at`, never by line order or by `ts`.**
- Apply the envelope **per window**, keyed by `resets_at`.
- **100,158 usable Claude rows collapse to 687** over 30 days — 146×, about 30 rows a day **[verified]**.
- **Push rows before 2026-10-04 can be dated late.** Their `observed_at` was the last transcript entry of any type, so an attachment or user message after the last API response moved it forward: session `bc36b32e`'s frozen reading, taken at 12:58:48Z on 2026-09-30, is dated 13:37:46Z **[verified]**. They also repeat every ~10 s while a session stays open (96% of push rows were exact repeats of that session's previous one). The envelope rule handles both; from 2026-10-04, readings are dated by the last `assistant` entry and repeats are not written.

### 5.3 Push `observed_at` is one hour too late during daylight-saving time — fixed in code 2026-09-30

Until the fix, the push script (then `agent-statusline`'s `src/statusline/push-claude-quota.sh`) converted the transcript timestamp with jq 1.6's `fromdateiso8601`, which on this Mac returns `2026-09-30T11:00:00Z` as 1790769600 instead of 1790766000 — **+3,600 s** whenever the local zone is in DST, correct otherwise **[verified]**. Push rows written during DST by a pre-fix deploy therefore have `observed_at` an hour late: ordering among push rows is preserved, but they look an hour fresher than poller rows. The code now converts by plain calendar arithmetic, exact in every timezone (`tests/test_push_claude_quota.sh`). **When comparing push rows with poller rows, subtract 3,600 s from push rows written during DST before the first deploy after 2026-09-30.**

### 5.4 Codex's fake idle countdown

Codex rows with `usedPercent == 0` and `resetsAt ≈ ts + window span` mean *no window is open*, not a fresh window. Drop them (Sources §5.1).

### 5.5 Codex gaps during active use

The Codex poller deliberately skips while a session file is fresh (§1.3), so the Codex log is thinnest exactly when usage is highest. For those stretches, read the session files' `token_count` events (Sources §4.1).

### 5.6 No schema version

The `raw` column of both account databases carries several row shapes (§2.2's older formats, §2.1's added fields, §3's two sources), migrated as-is rather than normalised. Readers must sniff the shape; a missing key means "older row", never zero.

---

### 5.7 Recent data, and retention

- **Recent rows: filter on `ts`** (indexed in every table) instead of scanning. The latest Claude reading is `SELECT * FROM latest`.
- **Per-folder usage: read `usage_5m`** (or `usage_requests`), not `requests` alone: only the views add the requests telemetry sees and transcripts never record (prompt suggestions, compaction, web search, titles: about 4.6% of USD), and prefer Claude Code's own cost to the price table. A slot is `final` once it ended before the last scan's `complete_through_ts`; until then, requests younger than 15 min are still missing.
- **Retention: none — rows are kept indefinitely.** Revisit if `data/claude/` passes ~1 GB (it was 178 MB after the migration).

### 5.8 No Claude reading at all while nobody uses Claude Code on a machine (accepted, 2026-10-10)

The Claude poller borrows each machine's Claude Code login, whose access token lasts 8 h and is renewed only by Claude Code itself (`USAGE_DATA_SOURCES.md` §3.5). If Claude Code runs on neither the Mac nor the VM for more than 8 h (several days away, say), both pollers get 401 and **no Claude reading is stored until Claude Code runs again on one of them**; after the refresh token expires too (weeks), only a new login helps. What this loses: usage made meanwhile elsewhere (claude.ai, the phone, cloud sessions) still shows up in the first reading afterwards, as a total, but *when* it happened is lost. Readers must treat such a stretch as "unknown", not as "unchanged". The maximizer's own Claude runs on the VM renew the VM's token as a side effect.

**Decision (the user, 2026-10-10): accept the gap.** Rejected: a keepalive message every few hours, because any message opens a 5-hour window at an arbitrary time (`USAGE_DATA_SOURCES.md` §5.2). Not tried yet: whether a Claude Code command that sends no message (e.g. `claude auth status`) renews an expired token; if it does, a keepalive job without that drawback becomes possible (`TODO.md`).

## 6. Change history

| Date | Change | Effect on the data |
|---|---|---|
| 2026-08-26 | Claude poller stops logging `token_deltas` / `baseline`, adds `api_headers` | Older rows have the old shape |
| 2026-08-30 | `source` key added | Older rows have none; treat as `"claude"` (`"claude_api"` since 2026-10-04) |
| 2026-08-31 | Combined `data/quota-log.jsonl` split per provider; push path added | See `adhoc_quotas_analysis/AGENTS.md` "Naming history" |
| 2026-09-29 | Push stops rounding the percent before logging | No practical effect: upstream values are whole (§5.1). Only matters if Anthropic ever sends fractions |
| 2026-09-29 | Push adds `session_id` + `session_cost_usd` | Per-session spend over time, interactive sessions only |
| 2026-09-30 | Push `observed_at` converted by calendar arithmetic instead of jq's `fromdateiso8601` | Earlier push rows written during DST are +3,600 s (§5.3) |
| 2026-09-30 | Claude poller catches every `OSError` | Server disconnects become `network` error rows instead of silent gaps |
| 2026-09-30 | Push adds the raw `prompt_cache` object | Session cache statistics and miss diagnostics, persisted nowhere else |
| 2026-09-30 | New Codex plan-history poller | Fractional per-window history and real window ends, daily |
| 2026-09-30 | Telemetry receiver + `env` keys in `~/.claude/settings.json` | Per-request Claude usage, including requests absent from transcripts, for sessions started after the install |
| **2026-09-30** | **Per-agent, per-scope layout**: `data/claude-quota-history.jsonl` → `data/claude/account.jsonl`, `data/codex-quota-history.jsonl` → `data/codex/account.jsonl`, new `data/claude/<session-id>.jsonl`; `data/claude-telemetry.jsonl` folded into the session files. Push rows' `session_id` → `observed_by_session`; `session_cost_usd` and `prompt_cache` moved to session rows; session rows gain `model_id`. Migrated by `adhoc_quotas_analysis/split_by_scope.py`, originals in `data/_archive/`. **Percent is account-scope only from here on (§1).** | Rows without session fields are byte-identical to the originals; row counts reconcile exactly. `adhoc_quotas_analysis/quota_model.py` and `window_gaps.py` reproduce their previous output byte for byte |
| 2026-09-30 | Provider scripts deployed under `~/opt/agent-statusline/providers/`, symlinked from `~/.claude/` and `~/.codex/` | The old real-file copy had gone stale unnoticed |
| **2026-09-30** | **Repo split**: usage tracking moves from `agent-statusline` into this repo, `agent-usage-tracker`. Runtime `~/opt/agent-statusline/{data,state/quota/claude,state/poll}` → `~/opt/agent-usage-tracker/…` (moved, not rewritten); LaunchAgents renamed `com.jeanlescut.agent-usage-tracker{,.otel}`. The push script becomes `bin/ingest-claude-statusline.sh` and takes the raw statusline payload on stdin instead of 9 arguments | None: row shapes, `source` values (`claude_statusline` included) and the scope rule are unchanged. Only paths moved |
| 2026-09-30 | Push token fields `session_input_tokens` / `session_output_tokens` removed before ever being deployed | They were mislabelled: the source field is context size, not a session total (Sources §3.1) |
| **2026-10-04** | **Push dated by the last `assistant` entry, deduplicated per session.** `observed_at` comes from the last API response in the transcript's last 256 KB instead of the last timestamped entry of any type in its last 20 lines; no such entry → nothing written, state file included (it used to get *now*). Rows are appended only when they differ from this session's previous one (§1). State-file `source` renamed `X` → `statusline`, `P` → `API` | Push row density drops by ~99% (one row per API response per session instead of one per ~10 s render: session `bc36b32e`'s 12,163 rows held 27 distinct readings). Older push rows keep their duplicates and may be dated late (§5.2); history is not rewritten |
| 2026-10-04 | Claude poller polls every 120 s instead of every 60 s tick while a status line is on screen | At 60 s the endpoint refused every other request with a 429 and `Retry-After: 0` (358 of 778 polls on 2026-10-03/04), so poller error rows should drop sharply while the number of successful readings stays about the same |
| **2026-10-04** | **Poller `source` values say what they read**: `"claude"` → `"claude_api"` (`GET /api/oauth/usage`), `"codex"` → `"codex_app_server"` (`codex app-server` JSON-RPC), and the latest-reading file's source → `claude_api` / `claude_statusline`. Existing rows renamed in place by `adhoc_quotas_analysis/rename_sources.py` (only the `source` value changes; originals in `data/_archive/*.pre-rename`) | Readers match the new names only: no row in `data/` or the error logs carries `"claude"` or `"codex"` any more. `claude_statusline` and `codex_plan_limit_history` are unchanged |
| **2026-10-04** | **Failed poll attempts leave `data/`.** All three pollers write a failed attempt to `logs/claude-poll-errors.jsonl` / `logs/codex-poll-errors.jsonl` instead of `account.jsonl`. The existing error rows were moved there, byte-identical, by `adhoc_quotas_analysis/split_errors.py`; the original account files are in `data/_archive/` | Readers of `data/` no longer filter `error != null`: every row there is a reading. Row counts in `data/` drop accordingly (Claude poller rows by about two thirds) |
| **2026-10-07** | **Claude plan upgraded from Pro to Max 5x** (about 16:51Z). Not a change in what is captured: the same fields, sources and files. The 7-day meter dropped from 60% to 0% mid-window (`claude_api` row at 2026-10-07T16:51:04Z; `seven_day` still resets 2026-10-12) | **A Claude percent after this point is not comparable to one before it**: 1% now stands for about 5× as many tokens. Split any percent series, budget or %-to-tokens conversion at 2026-10-07T16:51Z. `adhoc_quotas_analysis/CONCLUSIONS.md` numbers are Pro-only. Tokens and USD are unaffected. `~/.claude.json` still said `organizationType: claude_pro` right after the upgrade |
| **2026-10-08** | **JSONL files → SQLite databases, one gate per database.** `data/<agent>/account.jsonl` and `logs/<agent>-poll-errors.jsonl` → `data/<agent>/account_quotas.db` (`account_quotas`, `poll_errors`); `data/claude/<session-id>.jsonl` → `data/claude/sessions_usages.db` (`session_snapshots`, `telemetry`). Migrated by `adhoc_quotas_analysis/migrate_to_sqlite.py` through the gates: 228,838 lines, 228,544 distinct, every distinct line found again by its hash, 0 rejected, a second run inserted nothing; originals in `data/_archive/pre-sqlite-20261008T090432Z/` plus a `.tar.gz` of `data/`, `logs/`, `state/`. New: the transcript reader (`requests`, `scans`; backfill 416 files, 969 MB, 18,151 requests in 12.4 s) and the views `latest`, `usage_requests`, `usage_5m`. Pollers renamed (`claude_quota_api_poller.py`, `codex_quota_api_poller.py`, `codex_plan_history_poller.py`, `run_pollers.py`), the receiver to `telemetry_receiver.py`; `state/ingest/` and `state/poll/` dropped (the databases answer those questions) | Every row is still there, its JSON verbatim in `raw`; readers switch from files to `SELECT raw ... ORDER BY rowid` (as `adhoc_quotas_analysis/account_rows.py` and agent-quota-maximizer's `s1_ingest.py` do). `quota_model.py` printed the same output from the databases as from the JSONL. The 294 exact duplicate lines collapse to one row each. `ts` is now when a reading was true (`observed_at` for push rows); the old `ts` is `written_ts`. `state/quota/claude` is unchanged and written by the account gate |
| **2026-10-08** | **Central store on the VM.** Every machine pushes its new rows every 5 min (`push_to_central.py`); the VM stores them through the gates in `central/<machine>/data/<agent>/` (`receive_from_machine.py`). The tracker also runs on the VM (systemd `--user`): its transcripts, telemetry and Claude poller; the Codex pollers skip there (no Codex). First push from the Mac: 247,283 rows in 58 s; per-table counts identical on both sides | New `central/` tree on the VM. `account_quotas` copies are history as sent (no redundant-reading dedup). The VM's own Claude poller needs Claude Code logged in on the VM (its credentials were empty on 2026-10-08) |
| **2026-10-08** | **Codex turns in `data/codex/sessions_usages.db`.** The transcript reader also reads `~/.codex/sessions` and `archived_sessions`: one `requests` row per `token_count` event (request_id `<thread>:<running total>`), folder and model from the session, entrypoint from `session_meta.source`. Backfill: 77 files, 2,871 turns | Monthly tokens per model identical to ccusage 20.0.26; `usd` only for `gpt-5.6-sol` ($4/$20, cached 0.1x: ccusage's price, which reproduces its totals), NULL for other Codex models |
| **2026-10-10** | **One name per machine.** The Mac had no fixed `HostName`, so its network-derived hostname drifted on 2026-10-08: `chan-lescut-macbook-pro-1` until 16:38 UTC, `Chan-Lescut-MacBook-Pro` (18 requests, 16:59 → 17:03), then `chan-lescut-macbook-pro`; the VM held two central folders for it. The Mac now has a fixed `HostName`, `usage_db.machine_name()` follows job-runner's rule, and `adhoc_quotas_analysis/merge_machine_names.py` relabelled 18,990 Claude + 2,871 Codex requests (Mac and VM) and merged `central/chan-lescut-macbook-pro-1/` into `central/chan-lescut-macbook-pro/` (every old row found after the merge; the old folder and a tar backup are under `central/_archive/`) | `requests.machine` has one value per machine; the Mac's whole history is in one central folder |
| 2026-10-10 | A Claude poller's 429 makes only its own machine wait (job `WAIT`, no longer `WAIT … all`) | The endpoint counts 429s per login token, i.e. per machine (`USAGE_DATA_SOURCES.md` §3.5): the Mac no longer stops polling for up to an hour because the VM was refused |
| **2026-10-10** | **Scheduling moves to job-runner** (separate project): one trigger and one entrypoint per job (`claude-quota`, `codex-quota`, `codex-plan-history`, `transcripts`, `push`); `run_pollers.py` and the pollers' own interval code go. The Claude poller's timing now reads the age of `state/quota/claude` (any source, any machine) instead of its own last poll, and a 429's Retry-After holds both machines. **Readings cross machines**: the Mac stores the VM's fresh reading in its own database instead of polling, and hands the VM each reading it polls (same `raw`, same `row_hash`), so each machine's `account_quotas` (and its `central/<machine>/` copy) can hold rows taken on the other. The Codex pollers no longer run on the VM | A Claude `claude_api` row is no longer proof that this machine called the API; fewer `claude_api` rows while status-line readings are fresh |

---

## 7. Available upstream but not captured

From Sources §2, the data that exists nowhere else and that we do not record yet:

| Data | Source | Why it matters |
|---|---|---|
| Codex usage per day × model × client, relative | Backend analytics (Sources §4.4) | The only per-client split; relative only |
| Claude quota status beyond the percent (`status`, `representative-claim`, overage) | `/v1/messages` headers (Sources §3.6) | Only reachable through a billed request of our own |

Deliberately not captured because it already persists elsewhere: Codex per-turn and per-session tokens and quota (session files; to be read like Claude's transcripts, `TODO.md` step 7). Claude's per-request tokens and spend are captured since 2026-10-08 (§10), because the execution folder and the interactive flag are needed per 5-minute slot.

---

## 8. How to re-verify

| Claim | How |
|---|---|
| Row shapes and counts | `python3 -c "import sqlite3,json,collections;d=sqlite3.connect('data/claude/account_quotas.db');print(collections.Counter(tuple(sorted(json.loads(r))) for (r,) in d.execute('SELECT raw FROM account_quotas')).most_common())"` |
| Poller failure rate | `SELECT source, COUNT(*) FROM poll_errors GROUP BY source` against the same in `account_quotas` |
| Nothing lost in the 2026-10-08 migration | `python3 adhoc_quotas_analysis/migrate_to_sqlite.py --logs data/_archive/pre-sqlite-20261008T090432Z/logs` after copying the archived JSONL back into a scratch runtime (`--runtime`): every line must be found, 0 inserted |
| Envelope compression | Apply `quota_model.py`'s `envelope()` and compare row counts |
| Fractional percent | `SELECT COUNT(*) FROM account_quotas WHERE five_hour_pct != CAST(five_hour_pct AS INT)` |
| Writer behaviour | `bash tests/run.sh`: `test_ingest_claude_statusline.sh` (push path), `test_account_gate.sh` and `test_session_gate.sh` (the gates and views), `test_transcript_reader.sh`, `test_migrate_to_sqlite.sh` |
| Scope rule holds | No percent-like key path (`pct\|percent\|utiliz\|basis_points`) in any `raw` of `sessions_usages.db`; no `account_quotas.raw` with `session_cost_usd`, `prompt_cache` or a bare `session_id`. Both gates refuse such rows |
| Transcript totals are exact | `python3 adhoc_quotas_analysis/verify_token_costs.py` (USAGE_DATA_SOURCES.md §3.4) |

When any of this changes, update this file and its "Last verified" line.

---

## 9. Telemetry rows: `telemetry` in `data/claude/sessions_usages.db`

One row per Claude API request (and per API error / refusal / exhausted retry), from 2026-09-30, with typed columns (`ts` = event time, `event`, `session_id`, `request_id`, `model`, `query_source`, the four token counts, `cost_usd`, `received_ts`) and the JSON below in `raw`, tagged `source: "claude_otel"`. Events without a usable session id are kept with `session_id` NULL (until 2026-10-08: `data/_unattributed/claude-otel.jsonl`; there were none). A batch Claude Code re-sends is stored again (its `received_at` differs), so count requests through `usage_requests`, which takes one event per `request_id`. Written through the sessions gate by `src/telemetry_receiver.py`, a local receiver on `127.0.0.1:4318` run by the `com.jeanlescut.agent-usage-tracker.otel` LaunchAgent. Nothing polls: Claude Code sends the events itself, because `install.sh` adds these keys to the `env` object of `~/.claude/settings.json` (`src/claude_telemetry_env.json`; `uninstall.sh` removes them again):

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
| `event` | — | — | `api_request`, `api_error`, `api_refusal` or `api_retries_exhausted`. Every other telemetry event (prompts, tools, hooks) is dropped on arrival and never written |
| `attributes.*_tokens` | tokens | **Per request** | That request alone (a delta). `cache_creation_tokens` is not split into 5m / 1h |
| `attributes.cost_usd` | USD, list price | Per request | Claude Code's own figure; `cost_usd_micros` is the same as an integer |
| `attributes.query_source` | — | Per request | What the request was for. Seen: `repl_main_thread` (the conversation), `generate_session_title`, `prompt_suggestion`, `sdk` (`claude -p`) |
| `attributes.session.id` | — | Per session | Joins to the transcript's file name, to the session file's name, and to account push rows' `observed_by_session` |
| `attributes.request_id` | — | Per request | Joins to the transcript's `requestId` for the requests the transcript has |
| `time_unix_nano`, `received_at` | ns / s epoch | — | When the event was recorded, and when the receiver wrote it |
| `resource.*` | — | Per process | Claude Code version and host |

What it shows that nothing else does **[verified 2026-09-30, one interactive session]**: the four `api_request` events summed to exactly the session's `cost-state` (1,267 / 286 / 94,492 / 11,946 tokens, $0.0360382), while the transcript held only the two `repl_main_thread` requests — `generate_session_title` and `prompt_suggestion` exist only here. A headless call's single event matched its `-p` result to the dollar ($0.0210743).

Attribution attributes include the account's `user.email`; the file stays local, like everything else under `data/`.

---

## 10. Transcript requests: `requests` in `data/<agent>/sessions_usages.db`, and the views

One row per Claude API request found in a transcript (`~/.claude/projects/**/*.jsonl`, subagents included), from 2026-10-08, back to the oldest transcript kept. Written through the sessions gate by `src/transcript_reader.py`, every 5 minutes. No `raw`: the transcripts are the raw material, and the table can be rebuilt from them.

| Column | Meaning |
|---|---|
| `ts`, `dt` | The request's first transcript line |
| `request_id` | `requestId` (else `message.id`); UNIQUE, so a resumed session's copied requests are skipped |
| `machine` | The machine's name, `usage_db.machine_name()`: job-runner's rule (Mac: `LocalHostName` in lower case; Linux: short hostname) |
| `session_id`, `folder`, `entrypoint` | `sessionId`, `cwd` (the execution folder), `entrypoint` (`cli` = interactive; `sdk-cli`, `sdk-py`, … = headless) |
| `model` | `message.model` |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens` | From the request's line with the **largest** `output_tokens` (a streamed reply repeats the request over several lines with growing output; the first line undercounts output about 2×) |
| `usd` | List price from `usage_db.PRICES`; NULL for a model not in the table |

**Codex rows** (`data/codex/sessions_usages.db`, since 2026-10-08) use the same columns: one row per turn (`token_count` event), `request_id` = `<thread id>:<running total>` (a repeated event or an archived copy is skipped), `session_id` = the thread id, `folder` and `model` from `session_meta` / the latest `turn_context`, `entrypoint` = `session_meta.source` (`cli`, `vscode`, `exec`; `subagent` for a subagent thread), `input_tokens` without the cached part, `cache_read_tokens` = `cached_input_tokens`, `output_tokens` including reasoning. A turn is stored as soon as its event is written (no 15-min wait). `usd` is set only for `gpt-5.6-sol`. Note `interactive` in `usage_5m` is `entrypoint = 'cli'`, so Codex's VS Code threads count as not interactive.

A request is stored once it is complete: when its first line is ≥ 15 min old (its lines span ≤ 292 s, measured 2026-10-08). Each run adds a `scans` row; its `complete_through_ts` (run time − 15 min) means every request that started earlier is in the table.

| View | One row per | Use |
|---|---|---|
| `latest` (account database) | — | The freshest reading by `ts`, whatever wrote it |
| `usage_requests` | API request, once | Transcript requests with `usd` from telemetry's `cost_usd` when present (`usd_from = 'telemetry'`), else the price table; plus the requests only telemetry has (`seen_in = 'telemetry'`), placed in their session's folder |
| `usage_5m` | 5-minute slot × folder × `interactive` | `requests`, `usd` and token sums; `final` = the slot ended before the last scan's `complete_through_ts` |

---

## 11. The central store on the VM `H-Frank-1`

```
~/opt/agent-usage-tracker/central/
├── chan-lescut-macbook-pro/data/{claude,codex}/{account_quotas,sessions_usages}.db
└── H-Frank-1/data/claude/{account_quotas,sessions_usages}.db
```

One folder per machine, named by job-runner's rule (`usage_db.machine_name()`: the Mac's `LocalHostName` in lower case, the VM's short hostname; the Mac's network-derived hostname drifted on 2026-10-08, see §6), each a full copy of that machine's databases, with the same tables and views (`latest`, `usage_requests`, `usage_5m`). Written only by `receive_from_machine.py` through the gates, from the rows each machine's `push_to_central.py` sends every 5 minutes. The VM's own data reaches `central/H-Frank-1/` the same way, without ssh.

| Property | Detail |
|---|---|
| Freshness | At most about 5 min behind each machine while it is online; a machine that is asleep or offline catches up on its next push. Per-folder $ adds the transcript reader's 15 min |
| Completeness | Every row of every table, as written on its machine (history as sent: no redundant-reading dedup). A re-sent row is a no-op |
| Not copied | `state/quota/claude` and the other state files |
| Reading across machines | Attach the databases you need (`ATTACH '.../central/<machine>/data/claude/sessions_usages.db'`) and union their views; open with `PRAGMA query_only = ON` (§1) |
| Quota on the VM | Account-wide, so any machine's readings will do: `latest` in each copy. The VM's own Claude poller works only while Claude Code is logged in there |

