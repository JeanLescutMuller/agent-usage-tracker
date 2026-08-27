# claude-utilization-cost-tracker — project notes for Claude Code

This file is for whichever Claude Code session picks this project up next.
`README.md` is the human-facing doc (schemas, setup, cache explainer) — read
it too. This file adds the *why*, the gotchas, and where the investigation
currently stands, so a fresh session doesn't have to re-derive any of it.

## What this project is

Anthropic's `GET https://api.anthropic.com/api/oauth/usage` (the endpoint
behind `/status` and this machine's statusline) returns only a pre-computed
`five_hour` / `seven_day` utilization percentage — no raw token counts, no
formula. This project empirically reverse-engineers that formula by logging
the percentage on a timer and correlating it against real token usage
recomputed from Claude Code's own transcripts.

Working hypothesis (from third-party reverse-engineering,
[claudecodecamp.com](https://www.claudecodecamp.com/p/i-tried-to-reverse-engineer-claude-code-s-usage-limits),
not yet confirmed): the meter is **dollar-cost-weighted**, using something
close to Anthropic's own list pricing. That's what `analysis.ipynb` tests.

## Repo ↔ deploy layout (standard convention, see `~/dev/CLAUDE.md` / `~/.claude/CLAUDE.md`)

- `~/dev/claude-utilization-cost-tracker/` — this git repo, source of truth for code. **Never edit the deployed copy directly.**
- `~/opt/claude-utilization-cost-tracker/` — deployed runtime copy: `poll.py`, `recompute_token_events.py`, the LaunchAgent plist, and `data/` (the actual logs — gitignored, lives only here). Edit code in `~/dev`, then re-run `./install.sh` to redeploy.
- `~/Library/LaunchAgents/com.jeanlescut.claude-utilization-cost-tracker.plist` — symlink only, points into `~/opt/.../`. Never a real file there.
- LaunchAgent runs `poll.py` every 5 minutes (`StartInterval=300`).

## Naming history (in case anything still references an old name)

Renamed twice, both on 2026-08-26:
1. `utilization-tracker` / `com.jeanlescut.utilization-tracker` (original)
2. → `claude-utilization-tracker` / `com.jeanlescut.claude-utilization-tracker`
3. → `claude-utilization-cost-tracker` / `com.jeanlescut.claude-utilization-cost-tracker` (current — renamed to fold in the $-cost-weighting hypothesis, which the plain "utilization-tracker" name didn't capture)

`install.sh` self-migrates from *either* prior layout automatically
(`migrate_legacy()` helper, called twice) — carries `data/` forward, boots
out the old LaunchAgent label, removes the old folder. Idempotent, safe to
re-run any time. If you ever see a third rename, add a third
`migrate_legacy` call rather than deleting the old ones — each is a no-op
once its source folder is gone, so there's no cost to keeping them.

## Architecture: why the two scripts are so different

This is the single most important design decision in this repo — don't
"simplify" it back to one incremental logger without re-reading this.

- **`poll.py`** — runs on the LaunchAgent timer. Does exactly one thing:
  append the full raw `/api/oauth/usage` response + selected HTTP headers
  to `data/utilization-log.jsonl`. This **must** be polled: the endpoint
  has no history, so a missed 5-minute tick is a permanently lost reading.
- **`recompute_token_events.py`** — **not** scheduled, run by hand (or at
  the top of `analysis.ipynb`'s workflow). Fully rebuilds
  `data/token-events.jsonl` from scratch every time, by scanning every
  `*.jsonl` under `~/.claude/projects/`. Stateless — no byte offsets, no
  incremental state, just overwrite-on-demand via a `.tmp` + atomic
  `.replace()`.

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

- `data/utilization-log.jsonl` — one record per poll tick, `{ts, iso, api, api_headers}`. Rows written before 2026-08-26 have an older schema (`token_deltas`/`baseline` fields, no `api_headers`) — handle both shapes if reading full history.
- `data/token-events.jsonl` — one record per assistant message with usage, full fidelity (model, effort, session/cwd/sidechain identity, verbatim `usage` object including the `cache_creation` 5m/1h split and `output_tokens_details.thinking_tokens`). Deliberately excludes message content.

As of 2026-08-26: 318 utilization poll rows (284 with a usable payload —
34 were failed fetches), 14,514 token events (back to 2026-07-30, though
polling didn't start until 2026-08-24 — so `seven_day` correlation has
sparse coverage for the first several days of that range). Note the ~11%
failed-fetch rate: `poll.py` writes a row with `api: null` when the
Keychain read or the HTTP call fails, and every consumer must skip those.

## Known gotchas / bugs already fixed (don't reintroduce)

- **Timestamp parsing must use `calendar.timegm(time.strptime(...))`, not
  `time.mktime(...)`.** Transcript ISO timestamps are UTC (`Z` suffix);
  `time.mktime` silently interprets its input as *local* time and produces
  wrong epoch values. This was a real bug, caught and fixed before
  deployment.
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

## Findings so far (2026-08-26)

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
python3 ~/opt/claude-utilization-cost-tracker/recompute_token_events.py

# then open analysis.ipynb and re-run all cells
```

Natural next steps, roughly in order of value:
0. **Explain the cross-window budget discrepancy** ($58 vs $81 implied,
   see Findings). Cheapest discriminating test: check whether
   `B_implied` correlates with the `seven_day` level at the time — if the
   weekly-throttle hypothesis holds, windows running under a `warning`
   weekly should show a systematically smaller 5-hour budget. There are
   only 4 closed windows, so this is suggestive-at-best until more
   accumulate, but it costs nothing to compute on each new one.
1. Let the LaunchAgent keep accumulating — more closed `five_hour` windows
   (especially further saturated ones) tighten the implied budget
   estimate.
2. Deliberately run some real Opus-5 volume in a window that closes, to
   get a usable Opus-vs-Sonnet cost ratio to test against the official
   2.5× pricing ratio.
3. Deliberately run a few sessions at low/medium effort to get a real
   effort-level cost comparison (currently 100% high-effort data).
4. Once there are enough closed windows, consider an actual linear fit
   (not just visual inspection) to quantify how well $-cost predicts
   utilization %, and whether raw tokens fits comparably well or clearly
   worse.
