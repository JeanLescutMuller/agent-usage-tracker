# claude-utilization-cost-tracker

Empirically studies how Anthropic's Claude Code utilization percentage
(`five_hour` / `seven_day`, the numbers behind `/status` and this
machine's statusline) relates to actual token usage per model — since
Anthropic doesn't publish the formula, this project measures it instead
of guessing.

## Why this exists

`GET https://api.anthropic.com/api/oauth/usage` (the same endpoint the
`/usage` command and this machine's `~/.claude/statusline-usage-fetch.sh`
call) returns only a pre-computed percentage — no raw token counts, no
formula, nothing to reconstruct the calculation from. Official docs
(support.claude.com) are deliberately vague: they only say "Opus costs
several times more per turn than Sonnet, and Sonnet more than Haiku," no
numbers. The leading third-party reverse-engineering effort
([claudecodecamp.com](https://www.claudecodecamp.com/p/i-tried-to-reverse-engineer-claude-code-s-usage-limits))
hypothesizes the meter is dollar-cost-weighted (using something close to
Anthropic's own API list pricing), but that's unconfirmed.

This project logs the one thing that can't be reconstructed after the
fact (the live percentage reading) and correlates it against token usage
recomputed from data Claude Code already keeps durably on disk, to test
that hypothesis against this account's own usage.

## Architecture

Two very different halves, because the two things being tracked have
completely different persistence properties:

### `poll.py` — the only part that runs on a timer

Deployed to `~/opt/claude-utilization-cost-tracker/`, scheduled via a launchd
LaunchAgent (`com.jeanlescut.claude-utilization-cost-tracker`, every 5
minutes — see `install.sh`). Each run fetches the full raw
`/api/oauth/usage` response (unfiltered — every field, including ones
currently `null` on this account) plus its HTTP response headers, and
appends one record to `data/utilization-log.jsonl`.

This side **must** be polled: the endpoint has no history. A missed
5-minute window is a permanently lost reading — there is no way to ask
"what was my utilization at 3pm yesterday" after the fact.

### `recompute_token_events.py` — not scheduled, run by hand

Rebuilds `data/token-events.jsonl` from scratch by scanning every
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

Idempotent — deploys both scripts to `~/opt/claude-utilization-cost-tracker/`,
writes/refreshes the LaunchAgent plist, and (re)loads it via
`launchctl bootstrap`. Self-migrating: detects either prior layout
(oldest: `~/opt/utilization-tracker`, label
`com.jeanlescut.utilization-tracker`; pre-2026-08-26 rename:
`~/opt/claude-utilization-tracker`, label
`com.jeanlescut.claude-utilization-tracker`) and moves it to the current
name automatically, carrying `data/utilization-log.jsonl` forward.

```bash
python3 ~/opt/claude-utilization-cost-tracker/recompute_token_events.py
```

Run this before any analysis session — it's what populates/refreshes
`data/token-events.jsonl`.

## Data files

Both live under `~/opt/claude-utilization-cost-tracker/data/` (the deployed
runtime location, not this source checkout — see the dev-wide convention
in `~/dev/CLAUDE.md`).

**`utilization-log.jsonl`** — one record per poll tick:
```jsonc
{
  "ts": 1787736614,                       // epoch seconds
  "iso": "2026-08-26T09:30:14Z",
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

`analysis.ipynb` loads both files and plots cumulative token cost against
observed utilization percentage, one line per reset window, for both the
`five_hour` and `seven_day` limits — the visual check for whether the
relationship is linear (and how consistent the implied "budget" is
across windows).

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
1-hour cache, and only the raw event log (not the old aggregated one)
records which.

## Findings so far (2026-08-26, see conversation history for full detail)

- Official API pricing ratios (confirmed, `platform.claude.com/docs/en/about-claude/pricing`):
  Opus 5 = 2.5× Sonnet 5 = 5× Haiku 4.5, on both input and output tokens.
  Cache multipliers are uniform across models, relative to each model's
  own input price: 5-min write 1.25×, 1-hour write 2×, cache read 0.1×.
- With the exact cache-duration split, 4 closed `five_hour` windows
  implied a budget of **$57.54–$73.07** (Sonnet-only) — much tighter than
  the **$19–$82** range produced by the old aggregated-only logger, which
  had to bound rather than compute the cache-write cost.
- A `five_hour` window has since saturated at **100%** (2026-08-26,
  severity `critical`) — the first ceiling observation.
- **The dollar-cost hypothesis now has quantitative support.** Regressing
  the change in utilization between consecutive polls against candidate
  predictors (fit through the origin): dollar cost R²=0.77 vs raw token
  count R²=0.28. Implied allowance ≈ **$73.57 per 5-hour window**, 95% CI
  [$66.32, $83.27].
- **The allowance is the same in every window.** Because both meters watch
  the same usage, `Δ5h% / Δ7d%` equals `B_7d / B_5h` with the token
  weighting cancelled out — a test that needs no cost model. That ratio is
  ~10.1× and holds even in a window that ran with the weekly meter at
  79–81%, so the 5-hour allowance is not throttled by weekly exhaustion.
  An earlier reading of a ~40% between-window difference turned out to be
  an artefact of dividing cumulative cost by cumulative utilization; see
  `analysis.ipynb`'s "Quantitative test" section.
- The `*_dollars` fields on every limit block, and the `spend` block's
  real money type (`{amount_minor, currency, exponent}`), are the
  strongest structural hint that the meter is dollar-denominated — but
  they are gated behind paid usage credits and are null in every payload
  logged. `analysis.ipynb` re-checks them on every run and reports
  loudly if that ever changes.
- Still missing for a fully precise mapping: confirmation of the
  weighting formula itself (still a hypothesis), more closed windows
  (only 4 so far), and enough Opus 5 volume for a per-model ratio.
