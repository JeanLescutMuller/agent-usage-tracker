# agent-quota-tracker — project notes

This file (`AGENTS.md`, the cross-agent-tool convention — `CLAUDE.md` is a
symlink to it for Claude Code) is for whichever coding-agent session picks
this project up next, regardless of which agent it is. `README.md` is the
human-facing doc (schemas, setup, cache explainer) — read it too. This file
adds the *why*, the gotchas, and where the investigation currently stands,
so a fresh session doesn't have to re-derive any of it.

## What this project is

**As of 2026-08-30 this project tracks Claude Code and Codex.** Two
pollers write to the same `data/utilization-log.jsonl`, disambiguated by
a `source` field: `poll_claude.py` (renamed from `poll.py` — hits
Anthropic's `GET /api/oauth/usage`, see below) and `poll_codex.py` (new —
Codex has no plain HTTP usage endpoint; it speaks JSON-RPC to `codex
app-server --stdio` instead, the same protocol Codex's own TUI
statusline uses to render its rate-limit display — confirmed by reading
`~/.codex/statusline-command.sh` and grepping `~/.codex/logs_2.sqlite`
for `rpc.method="account/rateLimits/read"`, then live-tested end to end).
`poll_all.py` runs both as subprocesses and is what the LaunchAgent
actually schedules — see `poll_all.py`'s docstring for why subprocess
isolation over a plain import. Rows from before 2026-08-30 have no
`source` field at all; they're all Claude readings (see `analysis.ipynb`'s
loader, which defaults a missing `source` to `"claude"`).

Codex's shape maps directly onto Claude's: `account/rateLimits/read`
returns `rateLimits.primary`/`.secondary`, each `{usedPercent, resetsAt,
windowDurationMins}` — 300 min (5-hour) and 10,080 min (7-day)
respectively, i.e. structurally the same two-tier limit Claude Code
exposes as `five_hour`/`seven_day`. `poll_codex.py` also logs
`account/usage/read`'s result (`codex_usage` field) — lifetime/peak/streak
token summary and a daily-bucket history; useful context, not yet used
for any regression (see "Natural next steps" below).

**Correction (2026-08-30):** an earlier pass through this file claimed
Codex's local session rollout files don't store token counts at all, and
concluded any token/cost detail had to come from a per-thread RPC call.
That was wrong — verified empirically the same day. Codex's local
session rollout files (`~/.codex/sessions/**/*.jsonl`) **do** carry an
`event_msg` entry of `type: "token_count"` after essentially every turn
in a session with actual activity: `info.total_token_usage` /
`.last_token_usage` (input/cached/cache_write/output/reasoning/total
tokens, cumulative and per-turn) *plus* a `rate_limits` snapshot
(`primary`/`secondary` `used_percent`, `resets_at`) at that same instant
— i.e. a local, durable, Codex analogue of what `recompute_token_events.py`
scans for Claude. Confirmed across 45 local session files: 19 with actual
turns all had `token_count` events (1,344 total, back to 2026-08-27); the
other 26 are just empty stubs (opened, zero turns) — nothing missing
there, correctly. `recompute_codex_events.py` scans these directly — no
RPC / API call needed, same "only log what has no other durable source"
principle as the Claude side.

**One asymmetry that's still real, though:** Codex's `account/usage/read`,
called *with* a `threadId`, separately reports real
`estimatedUsageUsdMicros`/`estimatedUsageCreditsMicros` per thread —
actual dollar-equivalent spend, not an inferred model. The local
`token_count` events give token *counts*, not dollars, same limitation
Claude's transcripts have. Claude's API never populates its analogous
`*_dollars` fields (see "Payload surface" in the notebook) — this
project had to *infer* a dollar-cost weighting by regression instead.
Codex may let this project validate that hypothesis directly rather than
infer it — but only via that per-thread RPC call, a genuine Codex API
call unlike the free local-file scan above, so don't reach for it until
the token-count-only regression (buildable right now, see Natural next
steps) has actually been tried and found wanting.

Anthropic's `GET https://api.anthropic.com/api/oauth/usage` (the endpoint
behind `/status` and this machine's statusline) returns only a pre-computed
`five_hour` / `seven_day` utilization percentage — no raw token counts, no
formula. This project empirically reverse-engineers that formula by logging
the percentage on a timer and correlating it against real token usage
recomputed from Claude Code's own transcripts.

**This is no longer just a working hypothesis — see Findings below.**
Increment-based regression (not the flawed cumulative method used
earlier) shows dollar-weighted cost explains utilization far better than
raw token counts (R²=0.77 vs 0.28). The origin of the hypothesis was
third-party reverse-engineering
([claudecodecamp.com](https://www.claudecodecamp.com/p/i-tried-to-reverse-engineer-claude-code-s-usage-limits)),
tested here against this account's own usage using Anthropic's own list
pricing.

## Repo ↔ deploy layout (standard convention, see `~/dev/CLAUDE.md` / `~/.claude/CLAUDE.md`)

- `~/dev/agent-quota-tracker/` — this git repo, source of truth for code. **Never edit the deployed copy directly.**
- `~/opt/agent-quota-tracker/` — deployed runtime copy: `poll_claude.py`, `poll_codex.py`, `poll_all.py`, `recompute_token_events.py`, `recompute_codex_events.py`, the LaunchAgent plist, and `data/` (the actual logs — gitignored, lives only here). Edit code in `~/dev`, then re-run `./install.sh` to redeploy.
- `~/Library/LaunchAgents/com.jeanlescut.agent-quota-tracker.plist` — symlink only, points into `~/opt/.../`. Never a real file there.
- LaunchAgent ticks `poll_all.py` every 60s (`StartInterval=60`, changed from 300 on 2026-08-30), which runs `poll_claude.py` then `poll_codex.py` as subprocesses — but a tick isn't necessarily a poll. Both pollers self-throttle most ticks away, settling to roughly the old flat 5-minute cadence while idle (see each script's own module docstring); `poll_claude.py` additionally speeds back up to every tick while agent-statusline (a separate project) reports a live Claude Code statusline, via a heartbeat file it touches on every render. This exists because agent-statusline used to poll the same Anthropic endpoint itself on a 60s cache TTL, and running that opportunistically across every open session independently caused 429s during busy multi-session hours — agent-statusline now reads this project's log instead (see its own `AGENTS.md`'s "Cross-project dependency").
- `poll_codex.py` self-throttles the *opposite* direction (added 2026-08-30): it **skips** a tick outright if any `~/.codex/sessions/**/*.jsonl` file was modified in the last 5 minutes, because an active Codex session already writes its own `rate_limits` snapshot to that file on every turn (the `token_count` event — see "Correction" above and `recompute_codex_events.py`), fresher than a poll would get anyway. Once no session file is that fresh, it falls back to the same flat ~5-minute cadence the Claude side uses while idle. Don't confuse the two directions: Claude speeds up when active because polling is the *only* source of truth there; Codex skips when active because polling would be redundant with a free local source. One consequence worth knowing before you go looking for a bug: `data/utilization-log.jsonl`'s `source: "codex"` rows will show gaps during exactly the periods of heaviest Codex use — that's by design, not lost coverage; the trajectory for those periods lives in `data/codex-token-events.jsonl` instead (not yet merged into `analysis.ipynb` — see Natural next steps).

## Cross-project dependency

agent-statusline (a separate repo/project, `~/dev/agent-statusline`) reads
this project's `data/utilization-log.jsonl` for the Claude quota it shows in
the statusline, instead of polling Anthropic's usage endpoint itself (see
its own `AGENTS.md`'s "Cross-project dependency" section) — this project is
the single fixed-cadence poller for that endpoint on this machine now; a
second independent poller caused 429s during busy multi-session hours.

The coupling goes the other way too: `poll_claude.py` reads
`~/opt/agent-statusline/state/providers/claude.heartbeat`'s mtime to know
whether a Claude Code statusline is rendering somewhere right now, and polls
faster while it is (see `poll_claude.py`'s module docstring). Both
directions are soft dependencies — if the other project isn't installed,
each side just falls back to its older, flatter behavior (agent-statusline
shows stale/no quota data; this project's Claude poller never detects
"live" and settles to its flat idle cadence).

## Naming history (in case anything still references an old name)

Renamed three times, most recently 2026-08-27:
1. `utilization-tracker` / `com.jeanlescut.utilization-tracker` (original, pre-2026-08-26)
2. → `claude-utilization-tracker` / `com.jeanlescut.claude-utilization-tracker` (2026-08-26)
3. → `claude-utilization-cost-tracker` / `com.jeanlescut.claude-utilization-cost-tracker` (2026-08-26, later same day — folded in the $-cost-weighting hypothesis, which the plain "utilization-tracker" name didn't capture)
4. → `agent-quota-tracker` / `com.jeanlescut.agent-quota-tracker` (current, 2026-08-27 — dropped the Claude-specific name: this project is meant to track other coding agents' quotas too, starting with Codex. Naming got ahead of implementation at the time of this rename — the Codex poller itself landed 2026-08-30, see "What this project is" above)

`install.sh` self-migrates from *any* prior layout automatically
(`migrate_legacy()` helper, called once per prior name) — carries `data/`
forward, boots out the old LaunchAgent label, removes the old folder.
Idempotent, safe to re-run any time. If you ever see a fifth rename, add a
fifth `migrate_legacy` call rather than deleting the old ones — each is a
no-op once its source folder is gone, so there's no cost to keeping them.

## Architecture: why the scripts are split this way

This is the single most important design decision in this repo — don't
"simplify" it back to one incremental logger without re-reading this.

- **`poll_claude.py`** — runs on the LaunchAgent tick (via `poll_all.py`,
  see below), but self-throttles most ticks away (see its module docstring):
  it polls every tick while agent-statusline's heartbeat file says a Claude
  Code statusline is live, settling to roughly every 5 minutes once idle.
  When it does poll, it does exactly one thing: append the full raw
  `/api/oauth/usage` response + selected HTTP headers to
  `data/utilization-log.jsonl`, tagged `source: "claude"`. This **must** be
  polled: the endpoint has no history, so a missed reading is permanently
  lost - the self-throttling only skips ticks it judges unnecessary, it
  never disables polling outright.
- **`poll_codex.py`** — same rationale, same file, `source: "codex"`. Also
  settles to roughly every 5 minutes idle, but the self-throttle direction
  is inverted from the Claude side: it **skips** a tick if any local
  `~/.codex/sessions/**/*.jsonl` file was modified in the last 5 minutes,
  since an active session already writes a fresher `rate_limits` snapshot
  there itself (see "Repo ↔ deploy layout" above and its own module
  docstring). Spawns `codex app-server --stdio` and speaks JSON-RPC
  (`initialize`, `account/rateLimits/read`, `account/usage/read`) instead
  of a plain HTTP GET, since that's the only interface Codex exposes for
  this. Logs the raw `account/*` results under
  `codex_rate_limits`/`codex_usage` keys, alongside the same `error`
  convention as `poll_claude.py`.
- **`poll_all.py`** — the actual LaunchAgent target. Runs the two pollers
  above as subprocesses (not imports), so one crashing can't stop the
  other. Each poller stays fully runnable standalone by hand.
- **`recompute_token_events.py`** — **not** scheduled, run by hand (or at
  the top of `analysis.ipynb`'s workflow). Fully rebuilds
  `data/token-events.jsonl` from scratch every time, by scanning every
  `*.jsonl` under `~/.claude/projects/`. Stateless — no byte offsets, no
  incremental state, just overwrite-on-demand via a `.tmp` + atomic
  `.replace()`.
- **`recompute_codex_events.py`** — the Codex analogue, same rationale
  and same stateless-rebuild shape. Scans every `*.jsonl` under
  `~/.codex/sessions/` for `event_msg` entries of `type: "token_count"`
  and writes one record per event to `data/codex-token-events.jsonl`.
  Pure local-file parsing, **no RPC / Codex API call** — those events
  already carry both the token-usage breakdown and a `rate_limits`
  snapshot (see the correction note in "What this project is" above for
  why an earlier version of this file wrongly said this data didn't
  exist locally).

**Standing rule for this project: only log data that has no other durable
source.** Token usage is *not* logged on a timer, on purpose, because it's
fully recomputable from Claude Code's own transcripts — and those
transcripts are durable on this machine because `cleanupPeriodDays` was
raised from the default 30 to **365** specifically to support this project
(owned by `bootstrap-home`'s `modules/claude_config.sh` / `files/
claude_settings.json` — see the `claude-config-ownership` skill; don't
hand-edit `~/.claude/settings.json`'s `cleanupPeriodDays` outside that
tool). An earlier iteration of this project *did* log token deltas
incrementally as a durability safety net — that was deliberately removed
once `cleanupPeriodDays=365` made it redundant (see git log:
"Stop logging token usage - it's now fully recomputable"). If a future
session is tempted to re-add incremental token logging "just in case,"
check `cleanupPeriodDays` is still 365 first — if it's been dropped back to
30, that's the actual thing to fix, not this architecture.

## Data files

Full JSON schema examples are in `README.md`. Quick summary:

- `data/utilization-log.jsonl` — one record per poll tick, shared by both pollers. Claude rows: `{ts, iso, source: "claude", api, api_headers, error}`. Codex rows (since 2026-08-30): `{ts, iso, source: "codex", codex_rate_limits, codex_usage, error}`. Rows written before 2026-08-30 have no `source` key at all — treat missing as `"claude"`. Rows written before 2026-08-26 have an even older schema (`token_deltas`/`baseline` fields, no `api_headers`) — handle all shapes if reading full history.
- `data/token-events.jsonl` — one record per assistant message with usage, full fidelity (model, effort, session/cwd/sidechain identity, verbatim `usage` object including the `cache_creation` 5m/1h split and `output_tokens_details.thinking_tokens`). Deliberately excludes message content. Claude-only (see "Architecture" above).
- `data/codex-token-events.jsonl` — Codex analogue, one record per local `token_count` event (roughly one per turn), full fidelity (session id/cwd, `total_token_usage`/`last_token_usage`, and the `rate_limits` snapshot logged alongside it). Built by `recompute_codex_events.py` from local session files only — no API call (see "Architecture" above).

As of 2026-08-27: 440 utilization poll rows (390 with a usable payload —
~11% were failed fetches, now with a recorded reason — see the `error`
field below), 15,278 token events (back to 2026-07-30, though polling
didn't start until 2026-08-24 — so `seven_day` correlation has sparse
coverage for the first several days of that range). These counts grow
continuously; re-check with `wc -l` rather than trusting this snapshot.
`poll_claude.py` writes a row with `api: null` (and `poll_codex.py` a row
with `codex_rate_limits: null`) when the fetch fails, and every consumer
must skip those.

## Known gotchas / bugs already fixed (don't reintroduce)

- **Timestamp parsing must use `calendar.timegm(time.strptime(...))`, not
  `time.mktime(...)`.** Transcript ISO timestamps are UTC (`Z` suffix);
  `time.mktime` silently interprets its input as *local* time and produces
  wrong epoch values. This was a real bug, caught and fixed before
  deployment.
- **`~/.codex/sessions/YYYY/MM/DD/` is bucketed by *local* time, not
  UTC** — the inverse gotcha from the one above. Confirmed empirically: a
  file named `...T16-33-14...` contains a `session_meta.timestamp` of
  `...T14:33:14Z`, a 2h Zurich/UTC (CEST) offset. `poll_codex.py`'s
  `_codex_session_recently_active()` uses `time.localtime()` for exactly
  this reason — swapping in `time.gmtime()` would check the wrong day
  directory near a UTC/local midnight mismatch.
- **`detect_windows()` in `analysis.ipynb`**: `resets_at` is a fixed
  boundary set when a window opens, not a rolling "+5h from now" — cluster
  consecutive readings that report the *same* `resets_at` (5-minute
  tolerance) to find one window. The **last** cluster for each limit
  (`five_hour`/`seven_day`) is almost certainly still open/in-progress —
  its "peak so far" is not a final, comparable number. The notebook
  already marks this (`is_open`) and labels plot legends accordingly —
  keep that distinction if you extend the plots.
- **`five_hour` windows are NOT contiguous — a window's start is
  `end - 5h`, never the previous window's end.** The 5-hour window is
  *activity-triggered*: it opens on the first message sent after the
  previous one expired, so `resets_at` = that moment + 5h. Between
  windows there are genuine idle gaps where the API reports
  `resets_at: null, utilization: 0.0` and no window exists at all (four
  such gaps so far, 0.3h–13.7h long). `analysis.ipynb` originally chained
  `start = prev_end`, which back-dated window starts by up to ~13.7h;
  fixed 2026-08-26 to `end - span`. It made no numerical difference *on
  this data* — an idle gap is idle precisely because nothing was sent, and
  all four contain exactly 0 token events — but it would bite immediately
  if usage ever arrives from a client that doesn't write local
  transcripts. `seven_day` is different: it really is a fixed rolling
  schedule, so there both rules agree to within a minute.
- **Poll coverage is much worse than the 5-minute interval suggests: ~46%
  of elapsed time has no reading at all.** Two distinct causes, and they
  need different fixes: (a) rows with `api: null` — the LaunchAgent fired
  but the fetch failed (39 of 341 rows, ~11%). **Fixed 2026-08-26:**
  `poll.py` now records an `error` object (`stage` keychain/http/network/
  parse, plus HTTP `status`) instead of discarding the reason. Rows logged
  before that have no `error` key. Once a few failures accumulate, check
  whether they're 401s (token expiry — the poller would need a re-login
  path) or transient network, because the fix differs. (b) No row written at all —
  27 gaps of 10–106 min. This machine is a laptop that sleeps constantly
  (`pmset -g log` shows DarkWake/Sleep cycles every ~15 min), and launchd
  `StartInterval` does not fire during sleep, nor does it replay missed
  ticks; it fires once on wake. `RunAtLoad` was added 2026-08-26 so login
  and every `./install.sh` produce an immediate reading — that helps at
  boot, but it does **not** address sleep gaps, so don't expect coverage
  to jump. Mostly benign, since no local usage
  happens while asleep either — but it cost us the exact 68%→100%
  crossing of the one saturated window (28-min gap), and a window that
  opens and closes entirely inside a gap is invisible.
- **Utilization is integer-quantized, so low-% readings are near-useless
  for inferring a budget.** `B_implied = cum_cost / (util/100)` at 3%
  carries a ±17% quantization error; at 53% it's ±1%. Only take implied
  budget from high-utilization readings — mixing in the low ones is what
  produced part of the old wide range.
- **Cache-write duration matters for cost, not just cache-write amount.**
  `cache_creation_input_tokens` alone isn't enough — a 5-minute cache write
  is priced at 1.25× base input, a 1-hour write at 2×. Always use the
  `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`
  split (present in `token-events.jsonl`), not just the aggregate. Using
  only the aggregate is what produced a much wider, less useful implied
  5-hour budget range early in this project's history ($19–$82 vs.
  $57.54–$73.07 once the split was captured).

## What "cache" means (if you need to re-explain it to the user)

Two roles a chunk of input can play under Anthropic's prompt caching:
- **Cache write** (`cache_creation_input_tokens`) — first time content is
  sent; stored server-side. Priced at a *premium* (1.25× base input for a
  5-min TTL, 2× for 1-hour).
- **Cache read** (`cache_read_input_tokens`) — a later request reuses
  already-cached content instead of resending it. Priced at 0.1× base
  input (90% discount).

Claude Code resends the full running conversation every turn, so
`cache_read_input_tokens` dominates in any real session (can be hundreds
of thousands of tokens) while genuinely fresh `input_tokens` stays small.
Confirmed empirically on this account's own longest session (2,292
messages): `cache_read_input_tokens` grew from ~21K at message 1 to
~220K–276K by the end (~13×), while `input_tokens` stayed flat at 2
throughout. Practical implication for the user: **the same-sized new
message costs measurably more, later in a long session**, purely from
re-reading a bigger history — not because the new content itself grew.
This is exactly what `/clear` and `/compact` exist to reset/shrink.

## Findings so far (2026-08-26, latest numbers refreshed 2026-08-27)

**Current numbers (2026-08-27, n=58 five-hour / n=32 seven-day intervals —
grows every time `analysis.ipynb` re-runs; treat everything below as a
snapshot and re-run for the live figures):**
- `dollar_cost` predicts Δ5h at **R²=0.85** (vs `raw_tokens` 0.24,
  `output_tokens` 0.65, `n_requests` 0.49, `cache_read_tokens` 0.18).
- 5-hour budget ≈ **$72.80**, 95% CI [$68.09, $79.01].
- Meter ratio `B_7d/B_5h` ≈ **9.8×** (still promo-inflated — standard is
  ~6.5×, see the promo bullet below). A **second** `five_hour` window has
  since saturated at 100% (2026-08-26 19:14, closed).
- **1pp of the 5-hour meter ≈ $0.73.** Sonnet-5 tokens needed to move it
  1pp: output (incl. thinking) 72.8K, cache write 1h 182K, cache write 5m
  291K, input 364K, cache read 3.64M. Opus needs 2.5× fewer of each (same
  price ratio); Haiku 5× more.
- **Quota share across all logged events**: cache reads 59.5% of $
  spent (from 97.3% of all tokens), cache writes 28.3% (96% of that is
  1-hour, not 5-minute), output 12.2%, input ~0%. More than half the
  allowance goes to *re-reading* conversation history, not generating
  anything — the direct case for `/clear`/`/compact`.
- **Predictability**: single 5-min interval ±53% typical relative error,
  falling to ±20-22% over ~40 min (independent-noise-like averaging).
  Tested and ruled out as the source: integer rounding (~4% of residual
  variance) and timing misalignment across poll boundaries (lag-1
  residual autocorrelation ≈ 0 on back-to-back polls, n=41 pairs). Source
  of the remaining ~20% per-interval error is still unidentified.
- All of the above (per-token table, quota-share table, predictability
  tests) now live in `analysis.ipynb`'s "Practical numbers" and
  "Predictability" sections, not just in conversation history.

**Original findings (2026-08-26, kept for provenance — see corrections above
where a later pass superseded one):**


- Official pricing (`platform.claude.com/docs/en/about-claude/pricing`,
  confirmed live): Opus 5 $5/$25 per MTok in/out, Sonnet 5 $2/$10, Haiku
  4.5 $1/$5 — Opus = 2.5× Sonnet = 5× Haiku, both directions. Cache
  multipliers (5m write 1.25×, 1h write 2×, read 0.1×) are uniform across
  models, relative to each model's own input price.
- With the full cache-duration split, 4 closed `five_hour` windows implied
  a Sonnet-only budget of **$57.54–$73.07** — much tighter than the
  $19–$82 range the old aggregated-only logger produced.
- **A `five_hour` window has now saturated at 100%** (2026-08-26
  08:50→13:50, severity `critical`, closed) — the first ceiling
  observation. Earlier notes saying no window came near 100% are
  superseded.
- **The budget does NOT vary between windows — that was an artefact.**
  An earlier pass reported implied budgets of $57.5–$59.7 in one window
  vs $80.5–$82.1 in another and treated it as a real ~40% difference,
  with weekly-throttling as the leading explanation. Both conclusions
  were wrong, for two separate reasons, and `analysis.ipynb`'s
  "Quantitative test" section now supersedes them:
  1. **The cumulative method manufactures the spread.** Dividing a
     window's cumulative cost by its cumulative utilization gives every
     later reading the *same* misattribution offset picked up near the
     window start — which is exactly why the implied budget looked stable
     *within* a window and different *between* them. It was one offset
     per window, not a moving budget. Always difference consecutive polls
     instead; that cancels the offset.
  2. **A weighting-free test kills the throttle hypothesis outright.**
     Both meters observe the same usage, so `Δ5h% / Δ7d% = B_7d / B_5h`
     with the weighting cancelled — no cost model needed. That ratio is
     **~10.1×**, essentially constant across every segment with enough
     7-day movement to be meaningful, *including* the 08-24 segment that
     ran with the weekly meter at 79–81% (`warning`). The 5-hour
     allowance is not reduced when the weekly is nearly exhausted.
  A corollary worth keeping: a single-dimension reweighting test done
  under the cumulative method demanded *negative* weights (1h-cache
  −0.67×, cache-read −0.23× with a negative budget). Those impossible
  values were a symptom of the broken method, not evidence about pricing.
- **Quantitative support for the dollar-cost hypothesis (first real
  measurement, 2026-08-26).** Regressing per-interval Δ5h on candidate
  predictors, fit through the origin:
  `dollar_cost` R²=0.77, `output_tokens` 0.57, `n_requests` 0.50,
  `raw_tokens` 0.28, `cache_read_tokens` 0.22. Dollar-weighted cost beats
  a raw token count by a wide margin — raw tokens price a cache read the
  same as an output token, and the data rejects that. Adding a fixed
  per-request term to the cost model improves R² by 0.0003, so there is
  no evidence of a meaningful per-request charge.
- **Current budget estimates**: 5-hour ≈ **$73.57**, 95% CI
  [$66.32, $83.27] (n=46 intervals). 7-day ≈ $603.77, CI [$516, $718]
  (n=22, much weaker — coarse quantisation). Prefer the meter-ratio
  figure (7d ≈ 10.1 × a 5h window ≈ $740) over the direct 7-day fit,
  since the ratio needs no cost model at all.
- **⚠️ All weekly figures above are inflated by an active promotion.**
  Anthropic's **+50% Claude Code weekly limits promo** has run since
  2026-05-13 and currently ends **2026-08-31 23:59 PT** (09-01 06:59
  UTC); it has already been extended three times (originally to 08-19).
  It raises the **weekly** limit only — the support article states
  outright that "5-hour usage limits are not affected". So:
  - the 5-hour results (`B_5h`, per-token table, the dollar fit) stand;
  - every weekly number is 1.5× the standard allowance — the measured
    ~10.1× ratio corresponds to a standard **~6.7×**, i.e. post-promo a
    week is worth about **6.7 saturated 5-hour windows, not ~10**;
  - the promo began ~103 days before our first poll, so it explains
    nothing that varies *within* this dataset, and is consistent with the
    flat ratio we measured.
  Source: `clau.de/cc-50-promo` →
  support.claude.com/en/articles/15910845.
- **The promo expiry is a free natural experiment — don't miss it.** When
  it lapses the weekly allowance drops by a third with the 5-hour side
  unchanged, so the ratio should fall ~10.1 → ~6.7. The current weekly
  window resets 08-31 19:00 UTC and the promo dies ~12h into it, so we
  can also see whether the allowance changes mid-window (reported weekly
  % should jump ~1.5× instantly for unchanged usage) or only from the
  09-07 reset. **Keep the LaunchAgent polling across 08-31/09-01** — this
  is a dated, externally-forced change, and a far stronger test of the
  framework than passively accumulating more windows.
- The `$-cost` view in `analysis.ipynb` visually aligns the `five_hour`
  windows into a noticeably tighter single trend line than the raw-token-
  count view — early qualitative support for the dollar-cost hypothesis,
  **not yet a confirmed fit** (no regression/statistical test run, just
  visual inspection of 4 windows).
- **Reasoning effort**: every event logged so far, on both models, has
  `effort: "high"` (13,919 Sonnet + 56 Opus, 100%) — this account has
  never run low/medium effort, so there's no empirical low-vs-high
  comparison available yet, only the mechanism (thinking tokens bill as
  output tokens, the most expensive tier — avg 477/turn for Sonnet-high,
  up to 31,278 in one turn = ~$0.31 just for thinking on that one turn).
  If the user wants this measured properly, they need to deliberately run
  some sessions at lower effort.
- Opus-5 volume is still thin but no longer zero in a *closed* window: the
  saturated 08-26 window carried 35 Opus events (~10% of its $ cost), and
  the currently-open one is ~45% Opus. Still not enough to *test* the 2.5×
  ratio — and note the cross-window budget discrepancy above is far too
  large for Opus mispricing to explain. `seven_day` has only 1 closed
  window so far.
- **Payload surface (audited across all 284 payloads, 2026-08-26).** 17
  top-level keys. Beyond `five_hour`/`seven_day`:
  - `limits[]` — **not new**, present since the first poll. Always exactly
    two entries, `kind` `session` and `weekly_all`. Its `percent` has
    never once disagreed with the matching legacy top-level `utilization`
    (0/284), so it's a mirror — but it adds `severity` and `is_active`,
    which the flat blocks don't carry. `is_active` is exactly
    complementary (one true, one false, in 284/284) and marks the
    currently *governing* limit. Observed severity brackets — `session`:
    `normal` ≤68%, `critical` =100%; `weekly_all`: `normal` ≤18%,
    `warning` 79–81%. Thresholds can only be bracketed, not read off.
    `scope` is always null.
  - `spend` — **the strongest structural evidence for the dollar
    hypothesis.** Carries a real money type,
    `used: {amount_minor, currency: "USD", exponent: 2}`, plus `balance`,
    `cap`, `limit`, `auto_reload`, `percent`, `severity`, and a
    `disclaimer` string pointing at the usage-credits support article.
    All-zero/null here because `enabled: false`.
  - `*_dollars` (`limit_dollars`/`used_dollars`/`remaining_dollars`) —
    present on **every** limit block, null in 284/284. They are gated
    behind paid usage credits: `spend.enabled`,
    `spend.can_purchase_credits`, `extra_usage.is_enabled` and
    `extra_usage.credits_ever_enabled` are all `false` on this account.
    **They will not populate on their own** — so this is not something to
    passively wait for. Enabling usage credits would make the backend
    report its own dollar figures directly and would settle the entire
    question; that's a spending decision for the user, not a code change.
    `analysis.ipynb`'s `payload_surface_report()` cell is the standing
    tripwire — it re-checks every run and says loudly if one populates.
  - `nimbus_quill` — structurally a *limit block* (same five keys as
    `five_hour`), constant `utilization: 0.0`, `resets_at: null`. So it's
    a limit that simply doesn't apply to this account, not an unknown kind
    of object.
  - `member_dashboard_available` — constant `false` (org/seat feature).
  - Still entirely unpopulated, meaning unconfirmed: `seven_day_opus`,
    `seven_day_sonnet`, `seven_day_oauth_apps`, `seven_day_cowork`,
    `seven_day_omelette`, `tangelo`, `iguana_necktie`, `cinder_cove`,
    `amber_ladder`, `omelette_promotional` — don't assume what they
    represent without more evidence.

## How to continue the investigation

```bash
# after code changes, or on a fresh machine:
./install.sh

# before any analysis session (data/token-events.jsonl is not kept incrementally):
python3 ~/opt/agent-quota-tracker/recompute_token_events.py

# then open analysis.ipynb and re-run all cells
```

Natural next steps, roughly in order of value — the cross-window
"$58 vs $81" discrepancy from earlier is **resolved** (see Findings: it
was a method bug, and the weekly-throttle hypothesis it motivated is
dead), so it's off this list:

0. **⏰ Don't miss the promo-expiry natural experiment.** The +50% weekly
   limits promo ends 2026-08-31 23:59 PT — a dated, externally-forced
   change to the weekly budget with the 5-hour side held fixed. Make sure
   the LaunchAgent is actually polling across that boundary (check
   `poll.err`/coverage on 09-01), then re-run `analysis.ipynb`'s meter-
   ratio cell and confirm `B_7d/B_5h` moved from ~9.8× toward ~6.5×. Also
   check whether it moved *instantly* (mid-window, since the current
   weekly window's promo dies ~12h after it opens) or only at the next
   weekly reset (09-07) — that tells you how Anthropic implements a
   limit change mid-cycle, which is useful beyond just this promo.
1. ✅ **`recompute_codex_events.py`** — prototyped 2026-08-30. Scans
   `~/.codex/sessions/**/*.jsonl` for `token_count` events (see
   "Architecture" above) — no RPC/API call, same free local-file
   approach as `recompute_token_events.py`. This is what lets the
   notebook's "Codex" section regress token usage against the polled
   `usedPercent` the same way the Claude section does. Next: actually
   wire its output into `analysis.ipynb` and run that regression.
2. **Validate the dollar-cost hypothesis directly on Codex**, using the
   *real* per-thread RPC this time (`account/usage/read` called with a
   `threadId`, enumerable from `~/.codex/sessions/`) — this is a genuine
   Codex API call, unlike #1, so only reach for it if the token-count
   regression from #1 leaves something unresolved that a real reported
   dollar figure would settle. `estimatedUsageUsdMicros`/
   `estimatedUsageCreditsMicros` per thread is real, not inferred — compare
   it against `usedPercent` movement the same way the Claude section
   regresses `dollar_cost` against `five_hour_pct`, except here you can
   check whether the reported $ *directly* predicts the meter, without
   needing a pricing table or a regression at all. If it does, that's
   strong independent confirmation of the whole dollar-cost framework
   this project is built on.
3. Deliberately run real Opus-5 volume in a window that closes, to get a
   tested (not assumed) Opus-vs-Sonnet cost ratio against the official
   2.5× pricing ratio — still no window has enough Opus volume for this.
4. Deliberately run a few sessions at low/medium effort — still 100%
   high-effort data, so this coefficient is entirely unmeasured.
5. Track down the ~20% unidentified residual variance in the per-interval
   regression (see Findings' Predictability bullet). Rounding and timing
   misalignment are ruled out; untested candidates include sub-minute
   reporting lag and effort-level variation (which needs #4 first).
