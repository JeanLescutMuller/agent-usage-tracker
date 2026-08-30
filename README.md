# agent-quota-tracker

Empirically reverse-engineers how coding-agent usage-limit percentages
(Claude Code's `five_hour` / `seven_day` utilization, and — since
2026-08-30 — Codex's structurally identical `primary` / `secondary`
rate limits) relate to actual usage, since neither vendor publishes the
formula. Two pollers, one shared log: `poll_claude.py` hits Anthropic's
`GET /api/oauth/usage`; `poll_codex.py` speaks the JSON-RPC protocol
Codex's own TUI statusline uses (`codex app-server --stdio`), since Codex
has no plain HTTP equivalent. See `AGENTS.md` for the schema and the
asymmetries between the two sources (Codex reports real dollar figures
directly for some calls; Claude's are always inferred by regression).

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

Deployed to `~/opt/agent-quota-tracker/`, scheduled via a single launchd
LaunchAgent (`com.jeanlescut.agent-quota-tracker`, ticking every 60s — see
`install.sh`) that runs `poll_all.py`, which in turn runs each poller as
its own subprocess (so one crashing can't stop the other).

A 60s tick isn't a 60s poll rate: both pollers self-throttle most ticks away
(see each script's own module docstring), settling to roughly the old flat
5-minute cadence while idle. `poll_claude.py` additionally speeds back up to
every tick while a heartbeat file agent-statusline (a separate project on
this machine) touches on every statusline render says a Claude Code session
is live right now — that project used to poll this same endpoint itself,
and running that independently of this poller is exactly what caused 429s
during busy multi-session hours; it now reads this project's log instead
(see `AGENTS.md`'s "Cross-project dependency" notes in both repos).
`poll_codex.py` has no such live-session signal wired up and always settles
to ~5 minutes.

`poll_claude.py` fetches the full raw `/api/oauth/usage` response
(unfiltered — every field, including ones currently `null` on this
account) plus its HTTP response headers. `poll_codex.py` has no HTTP
endpoint to hit — Codex exposes rate-limit/usage data only via a
JSON-RPC method on `codex app-server` (the same protocol its own TUI
statusline uses), so it spawns `codex app-server --stdio`, does the
`initialize` handshake, then calls `account/rateLimits/read` and
`account/usage/read`. Both append one record each to the same
`data/utilization-log.jsonl`, disambiguated by a `source` field.

This side **must** be polled: neither endpoint has history. A missed
reading is a permanently lost one — there is no way to ask "what was my
utilization at 3pm yesterday" after the fact. The self-throttling only
skips ticks it judges unnecessary; it never disables polling outright.

### `recompute_token_events.py` — not scheduled, run by hand

Claude-only (Codex's local session files don't store token counts, so
there's no equivalent scan possible there — see `AGENTS.md`). Rebuilds
`data/token-events.jsonl` from scratch by scanning every
`*.jsonl` transcript Claude Code itself writes under
`~/.claude/projects/`. One record per individual assistant message that
carries token usage, full fidelity — model, reasoning effort,
session/cwd/sidechain identity, the cache_creation 5-minute/1-hour
split, thinking tokens, service tier, speed, stop reason, request/message
ids — the raw `usage` object kept verbatim, not reduced to a few named
counters, so a field Anthropic adds later shows up automatically instead
of silently being dropped. Deliberately excludes message *content* (tool
inputs/outputs, text) — irrelevant to usage/metering analysis, and it
would duplicate potentially sensitive conversation content into a
second, less-protected file for no analytical benefit.

This side is **not** logged on a timer, on purpose: it's fully
recomputable at any time from Claude Code's own transcripts, which this
machine now keeps for `cleanupPeriodDays: 365` (set via `bootstrap-home`,
see `~/dev/bootstrap-home/files/claude_settings.json` — originally 30
days, extended specifically to support this project). Since the source
is durable and a full rebuild only takes a couple of seconds even at
~14,000 events, there's no reason to also store an incremental copy —
that would just be logging something that doesn't need logging. Run it
whenever you're about to analyze the data, so it reflects everything up
to that moment.

## Setup

```bash
./install.sh
```

Idempotent — deploys all three scripts to `~/opt/agent-quota-tracker/`,
writes/refreshes the LaunchAgent plist, and (re)loads it via
`launchctl bootstrap`. Self-migrating: detects any prior layout (see
`AGENTS.md`'s naming history) and moves it to the current name
automatically, carrying `data/utilization-log.jsonl` forward.

```bash
python3 ~/opt/agent-quota-tracker/recompute_token_events.py
```

Run this before any analysis session — it's what populates/refreshes
`data/token-events.jsonl`.

## Data files

Both live under `~/opt/agent-quota-tracker/data/` (the deployed
runtime location, not this source checkout — see the dev-wide convention
in `~/dev/CLAUDE.md`).

**`utilization-log.jsonl`** — one record per poll tick, shared by both
pollers, disambiguated by `source` (added 2026-08-30; rows before that
have no `source` key — they're all Claude). Claude rows:
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

Codex rows (since 2026-08-30):
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

**`token-events.jsonl`** — one record per assistant message with usage:
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
