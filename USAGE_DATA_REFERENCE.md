# Usage data reference — what `agent-usage-tracker` captures, how, and where

**Canonical description of the usage data this repository records: which writer captures what, when, from which upstream source, in which file, with which shape and which traps.** For the inventory of everything that exists upstream — including what we do *not* capture — see the companion file `USAGE_DATA_SOURCES.md`, whose section numbers are cited here as "Sources §n". Other projects link to these two files rather than restating them.

Markers: **[verified]** was measured on this machine's data; **[docs]** comes from official documentation.

*Last verified: 2026-09-30 (paths updated for the repo split the same day). Row counts are as of 2026-09-29: 113,002 Claude rows, 4,006 Codex rows.*

---

## 1. Overview

All data lives under `~/opt/agent-usage-tracker/` (the deployed runtime; the code lives in `~/dev/agent-usage-tracker`). Until the 2026-09-30 repo split it lived under `~/opt/agent-statusline/`, in the same layout. The status line is a separate project, `agent-statusline`: it feeds this one by piping each Claude render's raw stdin payload into `bin/ingest-claude-statusline.sh`, and displays this project's `state/quota/claude` (README.md's "Contract with agent-statusline").

**Usage data is split by agent and by scope** (since 2026-09-30):

```
data/
├── claude/
│   ├── account.jsonl          account scope: the meter
│   └── <session-id>.jsonl     session scope: one file per Claude session
├── codex/
│   └── account.jsonl          account scope: the meter (no Codex session files - see below)
├── _unattributed/             telemetry events that carried no usable session id
└── _archive/                  the pre-2026-09-30 logs, kept after the migration
```

**The scope rule — percent is account-scope only.** The quota meter is one account-level number: when two sessions spend at once, there is no per-session percentage — not a hidden one, an undefined one. So:

| File | Carries | Never carries |
|---|---|---|
| `data/<agent>/account.jsonl` | Meter percent, reset times, `observed_at`, `source`, and `observed_by_session` on push rows | Per-session cost or token totals |
| `data/<agent>/<session-id>.jsonl` | Tokens, USD, model, cache statistics, `observed_at`, `source` | **A quota percent, under any name** |

The two scopes join on **`observed_at`** (a time join); no field is duplicated across them. Downstream projects must read this rule from here rather than infer it. Nothing in this repository estimates or apportions a per-session percentage, and nothing will: any such figure is derived downstream, with its uncertainty stated there.

`observed_by_session` on an account row names the session that was rendering when the reading was taken — a free liveness signal — **not** the session whose usage it is. It replaced the push rows' `session_id` so it cannot be misread as attribution.

Codex has no session files: nothing we capture is Codex-session-scoped. Its per-turn and per-thread tokens stay in Codex's own `~/.codex/sessions/` files (Sources §4.1).

Two kinds of file in total, easy to conflate:

| Kind | Files | Purpose | Read live? |
|---|---|---|---|
| **History logs** | `data/<agent>/account.jsonl`, `data/claude/<session-id>.jsonl` | Append-only raw record, kept for research (`adhoc_quotas_analysis/`) and for downstream projects | No |
| **Latest-reading state** | `state/quota/claude` (this project), and agent-statusline's own `state/quota/codex` | One reading per provider, FS-delimited (formats differ, §4) | Yes, by the status line |

### 1.1 Writers

| Writer | Scheduled by | Upstream source | Writes |
|---|---|---|---|
| **Claude push** — `bin/ingest-claude-statusline.sh` | Every Claude status-line render: agent-statusline's Claude provider pipes its raw stdin payload in | Statusline stdin (Sources §3.1) | `claude/account.jsonl` + `claude/<session-id>.jsonl` (both `source: "claude_statusline"`) + `state/quota/claude` (tag `X`) |
| **Claude poller** — `src/quota_polling/poll_claude.py` | LaunchAgent, 60 s tick, via `poll_all.py` | `GET /api/oauth/usage` (Sources §3.5) | `claude/account.jsonl` (`source: "claude"`) + `state/quota/claude` (tag `P`) |
| **Codex poller** — `src/quota_polling/poll_codex.py` | Same LaunchAgent tick | app-server `account/rateLimits/read` + `account/usage/read` (Sources §4.3) | `codex/account.jsonl` (`source: "codex"`) |
| **Codex plan-history poller** — `src/quota_polling/poll_codex_plan_history.py` | Same LaunchAgent tick | ChatGPT backend `plan_limit_history?days=7` (Sources §4.4) | `codex/account.jsonl` (`source: "codex_plan_limit_history"`) + `state/poll/codex_plan_limit_history` (last attempt) |
| **Telemetry receiver** — `src/telemetry/otlp_receiver.py` | Its own LaunchAgent, always running (`KeepAlive`); Claude Code pushes to it | Claude Code's OpenTelemetry events (Sources §3.7) | `claude/<session-id>.jsonl` (`source: "claude_otel"`, §9) |
| **Codex status line** — agent-statusline's `providers/codex-statusline-command.sh`, not this project | Every render of the patched Codex TUI | Codex stdin | `~/opt/agent-statusline/state/quota/codex` only, at most once per 60 s |

### 1.2 What each writer captures, by unit

| Writer | Quota % | Tokens | Spend $ |
|---|---|---|---|
| Claude push | ✅ 5h + 7d, account-wide, whole numbers | ❌ (already exact in transcripts, Sources §3.4); prompt-cache statistics per session from 2026-09-30 | ✅ session cumulative, all models combined, list price — from the first deploy after 2026-09-29 |
| Claude poller | ✅ 5h + 7d, account-wide, whole numbers, full raw response | ❌ (the endpoint has none) | ❌ (`used_dollars` always `null`) |
| Codex poller | ✅ 5h + 7d, account-wide, whole numbers, full raw response | ✅ account-wide daily buckets + lifetime total | ❌ |
| Telemetry receiver | ❌ | ✅ **per API request**, per model, with `query_source` — including the requests transcripts never record — from 2026-09-30 | ✅ per request, list price |
| Codex plan-history poller | ✅ finished 5h + 7d windows of the last 7 days, **fractional** (basis points), with their real start and end, full raw response — from 2026-09-30 | ❌ | ❌ |

### 1.3 When each writer actually writes

| Writer | Writes a history row when | Skips when |
|---|---|---|
| Claude push | The render's stdin has `rate_limits` **and** the transcript's last lines give a timestamp for `observed_at`. No dedup: every such render adds a row. | No `rate_limits` on stdin (non-subscriber, or a window just expired); no usable transcript timestamp — the state file is then still updated, with `observed_at` = now |
| Claude poller | Every tick while any Claude status line rendered in the last 90 s; otherwise when the last row is ≥ 5 min old. Errors are logged as rows too. | Mac asleep (launchd fires once on wake) |
| Codex poller | Every tick while a Codex status line rendered in the last 90 s; otherwise when the last row is ≥ 5 min old | **Any Codex session file changed in the last 5 min** — the session file is then the fresher source (Sources §4.1); Mac asleep |
| Telemetry receiver | Whenever a Claude session started after the install sends a batch (every few seconds while requests happen), interactive **and** `claude -p` | Receiver down (Claude Code drops the batch, no disk buffer); sessions started before the install |
| Codex plan-history poller | 24 h after its last successful attempt, 1 h after a failed one. Errors are logged as rows. | Mac asleep; independent of the Codex poller's skip rules |

Coverage that follows from this:

| Situation | Claude captured by | Codex captured by |
|---|---|---|
| Interactive session, messages flowing | Push, every render | Nothing in our log — the session file has it |
| Session open, no message sent yet | Push (stdin has `rate_limits` from startup, Sources §3.1) | Poller, every 60 s |
| No status line anywhere | Poller, every 5 min | Poller, every 5 min |
| Headless `claude -p` / `codex exec` | Per-request tokens and $ via telemetry; quota only through the next reading's meter movement | Only through the next reading's meter movement |
| Usage on other machines / clients | Next reading's meter movement | Next reading, plus `dailyUsageBuckets` |

---

## 2. `data/claude/account.jsonl` and `data/claude/<session-id>.jsonl`

`account.jsonl` holds every Claude meter reading: push rows and poller rows, disambiguated by `source`. It is the continuation of the pre-2026-09-30 `data/claude-quota-history.jsonl` (32.6 MB, 113,002 lines over 30 days as of 2026-09-29 **[verified]**), migrated row for row: every row that carried no session field is **byte-identical** to the original. **Account rows still come in several shapes** (§2.1 push rows, §2.2 poller rows and their older formats) — readers must still sniff the shape.

Session files hold the push path's session rows (§2.1) and the telemetry receiver's per-request rows (§9), one file per Claude `session_id`.

### 2.1 Push rows — `source: "claude_statusline"`

About 89% of account rows **[verified]**. From 2026-09-30, every render that writes an account row also writes a session row to the rendering session's own file (when its `session_id` is a plain UUID-like token), with the same `observed_at`:

```json
account.jsonl:
{"ts": 1790774229, "iso": "2026-09-30T13:17:09Z", "source": "claude_statusline", "observed_at": 1790773293,
 "five_hour_pct": 50, "seven_day_pct": 9, "five_hour_resets_at": "1790784600", "seven_day_resets_at": "1791226800",
 "observed_by_session": "8b301a30-..."}

8b301a30-....jsonl:
{"ts": 1790774229, "iso": "2026-09-30T13:17:09Z", "source": "claude_statusline", "observed_at": 1790773293,
 "model_id": "claude-haiku-4-5-20251001", "session_cost_usd": 0.0360382, "prompt_cache": {"warm": true, "requests": 2, "misses": 0, ...}}
```

Account row:

| Field | Unit | Granularity | Meaning |
|---|---|---|---|
| `ts`, `iso` | epoch s / ISO | — | Append time. **Not** when the reading was true |
| `observed_at` | epoch s | — | Timestamp of the transcript's last message: when Claude Code's in-memory reading became true. **The only trustworthy time key**, and the join key to session rows; pre-fix rows are one hour late during DST (§5.3) |
| `five_hour_pct`, `seven_day_pct` | % | Account-wide | Level within the current window, resets to 0 at window end. Written as received; always whole in practice (§5.1) |
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

### 2.2 Poller rows — `source: "claude"`

12,848 rows (11%) **[verified]**.

```json
{"ts": ..., "iso": ..., "source": "claude",
 "api": {"five_hour": {"utilization": 15, "resets_at": "2026-09-29T21:00:00.165686+00:00",
                       "limit_dollars": null, "used_dollars": null, "remaining_dollars": null, "locked_reason": null},
         "seven_day": {...}, "extra_usage": {...}, ...},
 "api_headers": {"Date": ..., "request-id": ..., ...},
 "error": null}
```

- `api` is the **full raw response**, unfiltered; `error` is `null` on success, otherwise says which stage failed and why.
- **The poller is unreliable: 8,516 of 12,848 rows carry an error** — 6,239 SSL/network, 2,165 HTTP 429, 112 missing Keychain token **[verified]**. The push path does ~92% of the real work.
- **Some failures before 2026-09-30 left no row.** `http.client.RemoteDisconnected` (the server closing the connection) is an `OSError`, not a `URLError`, so it escaped the poller's handlers: the run crashed and wrote nothing. 7 such crashes are in `logs/quota-poll.err`, the last on 2026-09-29 **[verified]**. Since the 2026-09-30 fix, any `OSError` is logged as a `network` error row.
- No `observed_at`: `ts` is the reading time.
- Older shapes: rows before 2026-08-30 have no `source` key (treat as `"claude"`); rows before 2026-08-26 carry `token_deltas` / `baseline` instead of `api_headers`. Those token deltas were never from the API — the old poller computed them from transcripts, and they were dropped as redundant.

---

## 3. `data/codex/account.jsonl`

The continuation of the pre-2026-09-30 `data/codex-quota-history.jsonl` (13.4 MB, 4,006 lines over 24 days as of 2026-09-29, ~3,349 bytes/line **[verified]**), migrated **byte-identical**. Two writers, disambiguated by `source`. Every row is account-scope; there are no Codex session files.

### 3.1 Poller rows — `source: "codex"`

```text
ts, iso, source: "codex", error
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

`state/quota/claude` (this project's) has six fields: `five_pct, five_reset, week_pct, week_reset, source, observed_at`, resets as epoch seconds. Both Claude writers compare their reading's `observed_at` with the stored one and overwrite only if newer, so the freshest reading wins regardless of write order and every open session converges on the same number. Percentages are **rounded** here, because the display does integer arithmetic. `source` is `X` (push) or `P` (poller).

`~/opt/agent-statusline/state/quota/codex` (agent-statusline's, listed for completeness) has **four** fields: `five_pct, five_reset, week_pct, week_reset`, with the resets as the TUI's display strings (e.g. `16:05`, `11:05 on 28 Sep`), not epochs, and no `source` or `observed_at`. It is written only by the Codex status line, at most once per 60 s, with freshness tracked in a sidecar `state/quota/codex.timestamp` file rather than by comparing readings. agent-statusline's `state/heartbeat/{claude,codex}` are touched on every render; this project's pollers read their mtime to tell a status line is live.

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

### 5.3 Push `observed_at` is one hour too late during daylight-saving time — fixed in code 2026-09-30

Until the fix, the push script (then `agent-statusline`'s `src/statusline/push-claude-quota.sh`) converted the transcript timestamp with jq 1.6's `fromdateiso8601`, which on this Mac returns `2026-09-30T11:00:00Z` as 1790769600 instead of 1790766000 — **+3,600 s** whenever the local zone is in DST, correct otherwise **[verified]**. Push rows written during DST by a pre-fix deploy therefore have `observed_at` an hour late: ordering among push rows is preserved, but they look an hour fresher than poller rows. The code now converts by plain calendar arithmetic, exact in every timezone (`tests/test_push_claude_quota.sh`). **When comparing push rows with poller rows, subtract 3,600 s from push rows written during DST before the first deploy after 2026-09-30.**

### 5.4 Codex's fake idle countdown

Codex rows with `usedPercent == 0` and `resetsAt ≈ ts + window span` mean *no window is open*, not a fresh window. Drop them (Sources §5.1).

### 5.5 Codex gaps during active use

The Codex poller deliberately skips while a session file is fresh (§1.3), so the Codex log is thinnest exactly when usage is highest. For those stretches, read the session files' `token_count` events (Sources §4.1).

### 5.6 No schema version

Both account files carry several row shapes (§2.2's older formats, §2.1's added fields, §3's two sources), migrated as-is rather than normalised. Readers must sniff the shape; a missing key means "older row", never zero.

---

### 5.7 Listing session files, and retention

- **Any glob over `data/<agent>/*.jsonl` must exclude `account.jsonl` explicitly** — it shares the directory with the session files.
- A session file is named by its session id only; its time span is in its rows. To find recent sessions, **filter by file mtime** (a file is appended to while its session is active) instead of listing and reading every file.
- **Retention: none — session files are kept indefinitely**, like the account files. This month of data is the only evidence base this repository and its consumers have, and files are small (≈ 156 Claude sessions a month, most a few hundred KB). Revisit if `data/claude/` passes ~1 GB.

## 6. Change history

| Date | Change | Effect on the data |
|---|---|---|
| 2026-08-26 | Claude poller stops logging `token_deltas` / `baseline`, adds `api_headers` | Older rows have the old shape |
| 2026-08-30 | `source` key added | Older rows have none; treat as `"claude"` |
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

---

## 7. Available upstream but not captured

From Sources §2, the data that exists nowhere else and that we do not record yet:

| Data | Source | Why it matters |
|---|---|---|
| Codex usage per day × model × client, relative | Backend analytics (Sources §4.4) | The only per-client split; relative only |
| Claude quota status beyond the percent (`status`, `representative-claim`, overage) | `/v1/messages` headers (Sources §3.6) | Only reachable through a billed request of our own |

Deliberately not captured because it already persists elsewhere: Claude per-message and per-session tokens and spend (transcripts, including `cost-state`), Codex per-turn and per-session tokens and quota (session files).

---

## 8. How to re-verify

| Claim | How |
|---|---|
| Row shapes and counts | `python3 -c "import json,collections;c=collections.Counter(tuple(sorted(json.loads(l))) for l in open('data/claude/account.jsonl'));print(c.most_common())"` |
| Poller failure rate | Count rows where `error` is non-null |
| Envelope compression | Apply `quota_model.py`'s `envelope()` and compare row counts |
| Fractional percent | Scan `five_hour_pct`, `seven_day_pct`, `api.*.utilization`, `usedPercent` for non-integers |
| Writer behaviour | `bash tests/run.sh`; `tests/test_ingest_claude_statusline.sh` covers the push path, `tests/test_split_by_scope.sh` the migration |
| Scope rule holds | For every `data/<agent>/*.jsonl` except `account.jsonl`, no key path matching `pct\|percent\|utiliz\|basis_points`; no `account.jsonl` row with `session_cost_usd`, `prompt_cache` or a bare `session_id` |

When any of this changes, update this file and its "Last verified" line.

---

## 9. Telemetry rows in `data/claude/<session-id>.jsonl`

One row per Claude API request (and per API error / refusal / exhausted retry), from 2026-09-30, appended to the file of the session that made it (`attributes["session.id"]`), next to that session's push rows. Rows are tagged `source: "claude_otel"`, with `observed_at` = the event time in epoch seconds (the join key). Events without a usable session id go to `data/_unattributed/claude-otel.jsonl`. Written by `src/telemetry/otlp_receiver.py`, a local receiver on `127.0.0.1:4318` run by the `com.jeanlescut.agent-usage-tracker.otel` LaunchAgent. Nothing polls: Claude Code sends the events itself, because `install.sh` adds these keys to the `env` object of `~/.claude/settings.json` (`src/telemetry/claude_telemetry_env.json`; `uninstall.sh` removes them again):

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

