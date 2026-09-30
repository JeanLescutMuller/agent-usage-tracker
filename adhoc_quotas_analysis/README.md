# quota tracking

> **2026-09-30: this directory moved to its own repo, `agent-usage-tracker`**, split back out of `agent-statusline` together with the pollers, the telemetry receiver and the usage-data docs. Paths below are updated; where older text still says "`agent-statusline`" or "the parent repo", read `agent-usage-tracker` for anything about usage tracking. The status line itself (`providers/`, the heartbeat files, `state/quota/codex`) stays in `agent-statusline`; the push script `src/statusline/push-claude-quota.sh` is now `../bin/ingest-claude-statusline.sh`, fed the raw statusline payload on stdin. See the repo root's `README.md` ("Contract with agent-statusline").

Part of `agent-statusline` since 2026-08-31 (folded in from the former
standalone `agent-quota-tracker` repo — see the parent README.md's "Quota
tracking" section for how this fits into the rest of that repo, and this
directory's own `AGENTS.md` for the full investigation history). This file
covers what's specific to the pollers and the research notebook.

Empirically reverse-engineers how coding-agent quota percentages
(Claude Code's `five_hour` / `seven_day` utilization, and — since
2026-08-30 — Codex's structurally identical `primary` / `secondary`
rate limits) relate to actual usage, since neither vendor publishes the
formula. Two pollers plus a free push, writing to two per-provider logs
(Claude's poller and push share one, Codex's poller has its own — see
"Data files" below): `poll_claude.py` hits
Anthropic's `GET /api/oauth/usage`; `poll_codex.py` speaks the JSON-RPC
protocol Codex's own TUI statusline uses (`codex app-server --stdio`), since
Codex has no plain HTTP equivalent; `../bin/ingest-claude-statusline.sh`
(same repo, runs on every Claude render) appends a reading for free from
`rate_limits` already present on the statusline's own stdin payload — no
network call, and unlike the poller, never rate-limited. See `AGENTS.md` for
the full schema and the asymmetries between all three sources (Codex reports
real dollar figures directly for some calls; Claude's are always inferred by
regression; the push rows carry a reduced shape).

## How the meter works

**The meter is a dollar meter.** Utilization is (very close to) your
usage priced at official API rates, divided by a fixed allowance — not a
raw token count. (The statistical case for this — regression R², a
weighting-free ratio test, confidence intervals — is in
[Analysis](#analysis) below; this section just states the resulting
model and the numbers you'd actually use.)

**1 percentage point of the 5-hour meter ≈ $0.73** of API-equivalent
spend (snapshot from 2026-08-27 — `analysis.ipynb` always has the
current live number). Tokens needed to move it 1pp, Sonnet 5 (Opus 5
needs 2.5× fewer — same price ratio, see below):

| token type | price | tokens per 1pp |
|---|---|---|
| output (incl. thinking) | $10/MTok | 72,800 |
| cache write, 1-hour | $4/MTok | 182,000 |
| cache write, 5-minute | $2.50/MTok | 291,000 |
| input (fresh) | $2/MTok | 364,000 |
| cache read | $0.20/MTok | 3,640,000 |

Official API pricing ratios (confirmed,
`platform.claude.com/docs/en/about-claude/pricing`): Opus 5 = 2.5×
Sonnet 5 = 5× Haiku 4.5, on both input and output tokens. Cache
multipliers are uniform across models, relative to each model's own
input price: 5-min write 1.25×, 1-hour write 2×, cache read 0.1×.

**Where quota actually goes**, across all logged events: cache reads
**59.5%** of total quota (from 97.3% of all tokens — cheap per-token, but
there's a lot of them), cache writes 28.3% (96% of that is 1-hour, not
5-minute), output only **12.2%**. Over half your allowance is spent
*re-reading conversation history*, not generating anything new — the
direct, measured case for `/clear` and `/compact`.

**⚠️ Every weekly (`seven_day`) number is currently inflated 50%.**
Anthropic has run a "Claude Code weekly limits" promotion since
2026-05-13, extended three times, currently ending **2026-08-31 23:59 PT**
([clau.de/cc-50-promo](https://clau.de/cc-50-promo)) — it raises the
*weekly* allowance only, explicitly not the 5-hour one. So the standard
(post-promo) weekly:5-hour ratio is **~6.5×**, not the ~9.8× measured
while the promo is active. See [Analysis](#analysis) for the natural
experiment this expiry sets up.

## What "cache" means here

Several `usage` fields (`cache_creation_input_tokens`,
`cache_read_input_tokens`, the `cache_creation` 5m/1h split) refer to
Anthropic's **prompt caching**: when a request repeats content Claude
already processed recently (Claude Code's system prompt, tool
definitions, earlier conversation turns), Anthropic can skip
re-processing it and charge a small fraction of the normal input price
instead. There are two roles a chunk of input can play:

- **Cache write** (`cache_creation_input_tokens`) — the *first* time some
  content is sent, it gets stored server-side for later reuse. Priced at
  a *premium* over normal input (1.25× for a 5-minute cache, 2× for a
  1-hour cache) — you're paying extra to make the *next* request cheaper.
- **Cache read** (`cache_read_input_tokens`) — a *later* request reuses
  that already-stored content instead of resending/reprocessing it.
  Priced at 0.1× normal input — a 90% discount.

Claude Code relies on this heavily: every turn resends the full running
conversation, so without caching, cost and processing time would grow
with every message. In practice this shows up as huge
`cache_read_input_tokens` numbers (hundreds of thousands per message is
normal) alongside comparatively tiny `cache_creation_input_tokens` —
you're mostly *reading* an already-cached conversation, occasionally
*extending* it (writing new cache) as the conversation grows.

This is exactly why precise dollar-cost accounting needs the *duration*
split, not just a total: the same number of cache-write tokens costs a
different amount depending on whether it went into a 5-minute or
1-hour cache, and only the raw event log (not an aggregated total)
records which.

## Architecture

Three scripts, because the things being tracked have completely
different persistence properties, and two independent sources shouldn't
share a failure mode:

### `poll_claude.py` + `poll_codex.py` — the parts that run on a timer

Source lives at `../src/quota_polling/` from this directory - split out on
2026-08-31 (see `AGENTS.md`'s "Naming history") once being deployed,
LaunchAgent-scheduled, unattended production code made them a genuinely
different kind of thing from the not-scheduled, run-by-hand tooling that
stayed in this directory. Deployed to
`~/opt/agent-usage-tracker/src/quota_polling/`, scheduled via
`agent-usage-tracker`'s launchd LaunchAgent (`com.jeanlescut.agent-usage-tracker`,
ticking every 60s — see the parent repo's `install.sh`) that runs
`poll_all.py`, which in turn runs each poller as its own subprocess (so one
crashing can't stop the other).

A 60s tick isn't a 60s poll rate: both pollers self-throttle most ticks away
(see each script's own module docstring). `poll_claude.py` has two tiers;
`poll_codex.py` has three, because unlike Claude's in-memory-only
`rate_limits`, Codex's is already durably written to a local session file on
every turn - a source of truth polling can never beat, not just a cache to
fall back on:

| Tier | Claude | Codex |
|---|---|---|
| **Local data is fresher than any poll** | *(no equivalent - Claude's rate-limit state is never written to disk on its own, see below)* | A local session `.jsonl` under `~/.codex/sessions/` was modified within the last 300s (a turn just completed, writing a fresh `rate_limits` snapshot via the `token_count` event) → **skip the poll entirely**. |
| **Someone's watching, but no fresher local signal** | `heartbeat/claude` is fresh (any render, whether or not you're actively prompting) → poll every tick (~60s) | `heartbeat/codex` is fresh and the tier above didn't already skip (idle session left open) → poll every tick (~60s) - must equal the LaunchAgent's own tick exactly, not just be close to it (see `poll_codex.py`'s docstring for why) |
| **Idle backstop** | Poll only if the last logged reading is ≥300s old (~5 min cadence) | Same, ≥300s |

Both heartbeat files are the same mechanism (`../providers/claude-statusline-command.sh`
/ `../providers/codex-statusline-command.sh`, same repo, touched on every
render) and exist for the same reason: quota can drift from other
sessions/devices on the account that neither poller's own local signals can
see, and it matters more while a human is actually looking at the number.

The two pollers differ in *why* polling is needed at all, not just in
cadence. Claude speeds up when watched because polling used to be its
*only* source of truth — the statusline side used to poll this same
endpoint itself, and running that independently of this poller is exactly
what caused 429s during busy multi-session hours. As of the 2026-08-31
merge, the statusline side pushes its own `source: "claude_statusline"`
reading directly instead (see `AGENTS.md`'s "Quota tracking" summary at the
parent README.md and the schema in this project's `AGENTS.md`) — this
poller's `source: "claude"` rows are now a fallback for the gap that push
path can't cover, not the primary signal. Codex never needed an equivalent
push: the data's already durable on disk via the local session file, so
writing it *again* into `codex/account.jsonl` from the statusline
would just be a second copy of something that already exists - the
heartbeat-driven speedup
above is the whole fix needed on the Codex side.

`poll_claude.py` fetches the full raw `/api/oauth/usage` response
(unfiltered — every field, including ones currently `null` on this
account) plus its HTTP response headers. `poll_codex.py` has no HTTP
endpoint to hit — Codex exposes rate-limit/usage data only via a
JSON-RPC method on `codex app-server` (the same protocol its own TUI
statusline uses), so it spawns `codex app-server --stdio`, does the
`initialize` handshake, then calls `account/rateLimits/read` and
`account/usage/read`. Each appends to its own per-provider file -
`poll_claude.py` to `data/claude/account.jsonl` (shared with the
statusline push path, disambiguated there by a `source` field),
`poll_codex.py` to `data/codex/account.jsonl` - split from a single
combined `data/quota-log.jsonl` on 2026-08-31 (see `AGENTS.md`'s "Naming
history").

This side **must** be polled: neither endpoint has history. A missed
reading is a permanently lost one — there is no way to ask "what was my
utilization at 3pm yesterday" after the fact. The self-throttling only
skips ticks it judges unnecessary; it never disables polling outright.

### Claude token events — recomputed inline in `analysis.ipynb`, not a script

Used to be a standalone `recompute_token_events.py` you ran by hand before
opening the notebook; folded directly into `analysis.ipynb`'s own cells
2026-09-14 (see `AGENTS.md`'s "Naming history"), since nothing besides the
notebook ever consumed it. The notebook rebuilds `claude-token-events.jsonl`
(written beside it, in this same directory - see "Data files" below) from
scratch on every run, by scanning every `*.jsonl` transcript Claude Code
itself writes under `~/.claude/projects/`. One record per assistant message
with token usage — just `ts`/`model`/`usage`, the fields the notebook
actually reads (trimmed from a richer field set the old script kept "for
later" and nothing ever read - see the notebook's own recompute cell for
the current shape). Deliberately excludes message *content* (tool
inputs/outputs, text) — irrelevant to usage/metering analysis, and it
would duplicate potentially sensitive conversation content into a
second, less-protected file for no analytical benefit.

This side is **not** logged on a timer, on purpose: it's fully
recomputable at any time from Claude Code's own transcripts, which this
machine now keeps for `cleanupPeriodDays: 365` (set via `bootstrap-home`,
see `~/dev/bootstrap-home/files/claude_settings.json` — originally 30
days, extended specifically to support this project). Since the source
is durable and a full rebuild only takes a couple of seconds even at
~24,000 events, there's no reason to also store an incremental copy —
that would just be logging something that doesn't need logging. Running
the notebook top to bottom always reflects everything up to that moment.

`recompute_codex_events.py` (still a standalone script — Codex-side data
isn't loaded into the notebook yet, see the "Codex" section near its end)
is the Codex analogue, same rationale and shape: it rebuilds
`codex-token-events.jsonl` (also in this directory) from scratch by
scanning every `*.jsonl` rollout under `~/.codex/sessions/` for
`token_count` events — one record per turn, carrying the full token-usage
breakdown *and* the `rate_limits` snapshot logged alongside it. Pure
local-file parsing, no API/RPC call — this is exactly the data
`poll_codex.py` skips fetching itself while a session is live (see the
table above).

## Setup

```bash
cd ../  # this is adhoc_quotas_analysis/ - install.sh lives at the agent-usage-tracker repo root
./install.sh
```

Idempotent — deploys `poll_claude.py`/`poll_codex.py`/`poll_all.py` to
`~/opt/agent-usage-tracker/src/quota_polling/`, writes/refreshes the
`com.jeanlescut.agent-usage-tracker` LaunchAgent plist (pointed at
`src/quota_polling/poll_all.py`), and (re)loads it via `launchctl
bootstrap`. Assumes a bare machine - no self-migration from a standalone
`agent-quota-tracker` deployment or any other prior layout (see
`AGENTS.md`'s "Naming history" for what those layouts were). If you're
moving from one, run the parent repo's `uninstall.sh` first, then
`install.sh` fresh - see the root `README.md`'s "Usage" section.

`split_quota_log.py` and `recompute_codex_events.py` are **not**
deployed by `install.sh` - they're ad-hoc, run-by-hand tooling, not
scheduled, so per this machine's own `~/dev` vs `~/opt` convention
(`~/AGENTS.md`: `~/opt/` is for what a scheduler runs unattended, not
anything a human runs by hand) they stay in this `~/dev/agent-usage-tracker`
checkout and run from here directly, even though they read/write this
project's live `~/opt/agent-usage-tracker/data/`. There's nothing to run for
the Claude side any more — just open `analysis.ipynb` in this directory
and run it top to bottom; its own cells recompute `claude-token-events.jsonl`
before loading it (see "Claude token events" above).

## Data files

The two quota logs live under `~/opt/agent-usage-tracker/data/` (the deployed
runtime, not this source checkout — see the dev-wide convention in
`~/AGENTS.md`). The two token-event files, by contrast, live directly in
this directory (`~/dev/agent-usage-tracker/adhoc_quotas_analysis/`, not
`~/opt/` at all) — deliberate, since unlike the quota logs they're fully
recomputable at any time (`claude-token-events.jsonl` by `analysis.ipynb`
itself, `codex-token-events.jsonl` by `recompute_codex_events.py` — see
"Claude token events" above), so they don't need the same durable,
unrecoverable-if-lost treatment, and nothing that produces them is
deployed anywhere.

**`claude/account.jsonl`** and **`codex/account.jsonl`**
(`data/`) — one
record per poll tick (plus, for Claude, one per statusline push - see
below), split by provider into these two files since 2026-08-31 (a single
combined `quota-log.jsonl` before that - see `AGENTS.md`'s "Naming
history"). Rows carry a `source` field (added 2026-08-30; rows before that
have no `source` key — they're all Claude, and predate the Codex poller
entirely) so a row's origin is still identifiable within
`claude/account.jsonl`, which two writers share. Claude poll rows,
in `claude/account.jsonl`:
```jsonc
{
  "ts": 1787736614,                       // epoch seconds
  "iso": "2026-08-26T09:30:14Z",
  "source": "claude",
  "api": { /* full raw /api/oauth/usage response, or null on failure */ },
  // ^ 17 top-level keys. Besides five_hour/seven_day (each with
  //   utilization, resets_at, and always-null limit/used/remaining_dollars):
  //     "limits": [ {kind:"session"|"weekly_all", group, percent,
  //                  severity:"normal"|"warning"|"critical",
  //                  resets_at, scope, is_active} ]   // percent mirrors the
  //                  // flat blocks exactly; severity/is_active are extra
  //     "spend":   {enabled, percent, severity, balance, cap, limit,
  //                 auto_reload, can_purchase_credits, disclaimer,
  //                 used:{amount_minor, currency:"USD", exponent:2}}
  //     "extra_usage": {is_enabled, credits_ever_enabled, used_credits, ...}
  //     "nimbus_quill": a limit block, always 0.0/null on this account
  //     plus several never-populated keys (seven_day_opus, tangelo, ...)
  "api_headers": {                        // or null on failure
    "date": "...", "request-id": "...",
    "anthropic-organization-id": "...", "anthropic-workspace-id": "...",
    "server-timing": "..."
  },
  "error": null                           // null on success; otherwise why the
                                          // reading is missing, e.g.
  // {"stage":"keychain","type":"CalledProcessError"}          not logged in
  // {"stage":"http","type":"HTTPError","status":401,"detail":"Unauthorized"}
  // {"stage":"network","type":"URLError","detail":"timed out"}
  // {"stage":"parse","type":"JSONDecodeError","detail":"..."}
}
```
The `error` field was added 2026-08-26; rows before that have no `error`
key at all (treat absent as "unknown reason"). The keychain stage records
only the exception *type*, never its message — that stage handles the
credential blob and a message could echo part of it into the log.
Rows written before 2026-08-26 have an older schema (`token_deltas` /
`baseline` fields inline, no `api_headers`) — handle both shapes if
reading the full file history. Rows where the Keychain read or the HTTP
call failed have `api: null` (~11% of rows so far) — every consumer must
skip those rather than assume a payload is present.

Codex rows, in `codex/account.jsonl` (since 2026-08-30):
```jsonc
{
  "ts": 1788081319, "iso": "2026-08-30T09:15:19Z",
  "source": "codex",
  "codex_rate_limits": {                  // raw account/rateLimits/read result, or null on failure
    "rateLimits": {
      "primary":   {"usedPercent": 0, "windowDurationMins": 300,   "resetsAt": 1788099318},
      "secondary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1788686118},
      "credits": {"hasCredits": false, "unlimited": false, "balance": "0"},
      "planType": "plus", "rateLimitReachedType": null
    },
    "rateLimitResetCredits": { /* Codex's own equivalent of Anthropic's +50% promo - named, dated reset grants */ }
  },
  "codex_usage": {                        // raw account/usage/read result, or null on failure
    "summary": {"lifetimeTokens": 263867715, "peakDailyTokens": 84197579,
                "longestRunningTurnSec": 3635, "currentStreakDays": 20, "longestStreakDays": 20},
    "dailyUsageBuckets": [ {"startDate": "2026-08-10", "tokens": 144710}, /* ... */ ],
    "threadUsage": null                   // only populated when called with a threadId - see AGENTS.md
  },
  "error": null
}
```
`primary`/`secondary` map onto Claude's `five_hour`/`seven_day` — same
two-tier shape, 300 min and 10,080 min (7-day) windows respectively.
`poll_codex.py`'s `error` field uses the same `{stage, type, detail?}`
convention as `poll_claude.py`, with stages `spawn` (the `codex` binary
couldn't be started), `timeout`, `rpc` (a JSON-RPC error response, e.g.
not logged in), and `parse`.

Claude push rows, also in `claude/account.jsonl` (since the 2026-08-31
agent-statusline merge), written by `../bin/ingest-claude-statusline.sh`
on real Claude Code renders, not on a timer:
```jsonc
{
  "ts": 1788174515, "iso": "2026-08-31T10:01:55Z",
  "source": "claude_statusline",
  "observed_at": 1788174005,              // the transcript's own last message
                                           // timestamp, NOT this row's append time -
                                           // when Claude Code's in-memory rate_limits
                                           // state actually became true
  "five_hour_pct": 42, "seven_day_pct": 55,
  "five_hour_resets_at": "2026-08-31T15:00:00Z",   // or null
  "seven_day_resets_at": "2026-09-07T00:00:00Z"    // or null
}
```
Deliberately a reduced shape - no raw API response, no headers, no `error`
(a push that can't compute `observed_at` just doesn't happen, silently -
there's nothing to log). No `codex_statusline` equivalent: Codex's provider
adapter never reads this shared log at all (see the parent README.md's
"Architecture" section), so there's no analogous free push for it yet.

**`claude-token-events.jsonl`** (this directory, not `data/` — see above) — one record per assistant message with usage:
```jsonc
{
  "ts": 1787434410, "iso": "2026-08-22T21:33:30.296Z",
  "model": "claude-sonnet-5", "effort": "high",
  "session_id": "...", "is_sidechain": false, "cwd": "...",
  "git_branch": "...", "cc_version": "...", "entrypoint": "cli",
  "user_type": "external", "uuid": "...", "parent_uuid": "...",
  "request_id": "...", "message_id": "...",
  "stop_reason": "end_turn", "stop_sequence": null, "stop_details": null,
  "diagnostics": null,
  "usage": {
    "input_tokens": 2, "output_tokens": 170,
    "cache_creation_input_tokens": 2266, "cache_read_input_tokens": 73538,
    "cache_creation": {"ephemeral_1h_input_tokens": 2266, "ephemeral_5m_input_tokens": 0},
    "output_tokens_details": {"thinking_tokens": 0},
    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
    "service_tier": "standard", "speed": "standard",
    "inference_geo": "not_available", "iterations": [ /* ... */ ]
  },
  "transcript_file": "/Users/jeanlescut/.claude/projects/.../session.jsonl"
}
```

## Analysis

`analysis.ipynb` loads both data files. It has two parts: an earlier
*descriptive* section (plots cumulative token cost against observed
utilization percentage, one line per reset window) and a later
*quantitative* section (increment-based regression, the meter-ratio
test, budget estimates with bootstrap CIs) that produced every number
below and in "How the meter works" above. Read the quantitative
section's own warning before trusting the descriptive plots' apparent
"budget" — the cumulative method they use has a known bug, explained
below.

**The regression.** Confirmed 2026-08-27 by regressing the *change* in
utilization between consecutive polls against candidate predictors, fit
through the origin (n=58 five-hour intervals as of that snapshot — grows
every time the notebook re-runs):

| predictor | R² |
|---|---|
| **dollar cost** | **0.85** |
| output tokens | 0.65 |
| request count | 0.49 |
| raw token count | 0.24 |
| cache-read tokens alone | 0.18 |

Dollar cost wins because token types are priced wildly unevenly — an
output token is **50× the price of a cache-read token** for Sonnet 5 —
and raw counts treat them as identical. From this fit: 5-hour budget ≈
$72.80, 95% CI [$68.09, $79.01]; 1pp of the 5-hour meter ≈ $0.73 (used
above).

**The allowance is constant across windows.** Both the 5-hour and 7-day
meters watch the same usage, so `Δ5h% / Δ7d%` equals the ratio of their
two allowances with the token-weighting cancelled out entirely — a test
that needs no cost model at all. That ratio holds flat at ~9.8× even in a
window that ran with the weekly meter at 79–81% (`warning`), so the
5-hour allowance is **not** throttled by weekly exhaustion.

An earlier in-project reading of a ~40% swing between windows (implied
budgets $57.5–$59.7 in one window vs $80.5–$82.1 in another) was a bug
in the analysis method, not a real effect: dividing *cumulative* cost by
*cumulative* utilization lets a one-time misattribution offset near a
window's start persist through every later reading — which is exactly
why it looked stable *within* a window and different *between* them.
Differencing *consecutive* polls instead removes the offset; superseded
2026-08-27. A single-dimension reweighting test done under the old
cumulative method demanded impossible negative weights (1h-cache −0.67×,
cache-read −0.23×) — a symptom of the broken method, not evidence about
pricing.

**The promo-expiry natural experiment.** The +50% weekly-limits promo
([clau.de/cc-50-promo](https://clau.de/cc-50-promo)) ends 2026-08-31
23:59 PT, a dated external change to the weekly budget with the 5-hour
side held fixed. If polling covers that boundary, `Δ5h%/Δ7d%` should
drop from ~9.8× toward the standard ~6.5×, and it's worth checking
whether the drop is instant (mid weekly-window, since the promo dies
~12h into the current one) or waits for the next weekly reset
(2026-09-07) — informative about how Anthropic implements a limit
change mid-cycle beyond just this promo.

**Predictability**: poor per-message (±53% typical error over a single
5-minute interval), improving to ±20–22% over ~40 minutes of usage as
independent noise averages down — good enough per-session, useless
per-turn. Two explanations were tested and ruled out for the
per-interval noise: integer rounding of the reported percentage (~4% of
the residual variance) and timing misalignment across the poll boundary
(near-zero lag-1 autocorrelation on back-to-back polls, n=41 pairs). The
remaining source is unidentified. The budget estimate itself is solid
(±7-8% CI).

**Structurally unmeasured, not just unmeasured today**: effort level
(100% of logged events are `effort: "high"` — no basis for a coefficient
until some sessions are deliberately run at lower effort) and the Opus
2.5× pricing ratio (assumed from list price, never tested — no window
yet has enough Opus volume). The `*_dollars` fields on every limit
block, and the `spend` block's real money type
(`{amount_minor, currency, exponent}`), are the strongest structural
hint that the meter is dollar-denominated — but they're gated behind
paid usage credits and are null in every payload logged so far;
`analysis.ipynb` re-checks them on every run and reports loudly if that
ever changes.
