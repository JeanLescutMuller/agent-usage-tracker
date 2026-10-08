# Conclusions

Established findings from the quota research: how and when quota windows start and end, and how to convert between quota %, tokens and USD for Claude Code and Codex. Every claim here comes with the evidence it rests on and its limits. The deep-dive history and gotchas stay in `AGENTS.md`, and the research notebook is `analysis.ipynb`.

This file is the reference model for `~/dev/agent-quota-maximizer/`. Section 1 is a self-contained cheat sheet, and the rest is the evidence behind it.

- **Reproduce:** every number comes from two read-only scripts in this directory. `python3 window_gaps.py` covers Claude window timing, and `python3 quota_model.py` covers the budgets, conversions and Codex window timing. Neither makes a network call.
- **Data:** Claude readings from 2026-08-24 to 2026-09-21 (poll plus statusline-push rows in `data/claude-quota-history.jsonl`) and 13,776 unique local Claude Code messages. Codex polls from 2026-08-30 to 2026-09-21 and 2,452 local `token_count` events from 2026-08-27 to 2026-09-16.
- **Plans:** Claude Pro (`organizationType: claude_pro`) and ChatGPT Plus for Codex (`planType: plus`). The numbers below are specific to these plans.
- **Claude plan changed on 2026-10-07 (about 16:51Z): Pro → Max 5x.** Every Claude percent budget and %-to-tokens/USD conversion below is for Pro and must not be applied to readings after that point; 1% of Max 5x is roughly 5× larger. Not re-measured yet.
- **USD:** always means API-equivalent cost at public list prices, never a billed amount. Neither provider reports a dollar figure for subscription quotas (section 4.4).

## 1. Cheat sheet

### 1.1 Window rules

| | Claude 5-hour | Claude 7-day | Codex 5-hour | Codex 7-day |
|---|---|---|---|---|
| What opens a window | The first request after the previous window expired | Nothing. Fixed weekly schedule | The first request after the previous window expired | The first request after the previous window expired |
| Start → end | start = first request, rounded to a 10-minute boundary; end = start + 5 h | End is every Monday 19:00 UTC; start = end − 7 d | start = first request (to the second); end = start + 5 h | start = first request; end = start + 7 d |
| Between windows (idle) | No window. The API returns `resets_at: null` and 0% | Still anchored to the Monday lattice. After a reset the API returns `null` until the first request, then the lattice end reappears | No window. The API returns a fake countdown: 0% and `resetsAt` = now + 5 h, moving with the clock | Same fake countdown: 0% and `resetsAt` = now + 7 d |
| Idle gaps observed | 26 of 31 consecutive pairs; median 11.5 h, max 213.8 h | None in the schedule | 21 of 28 pairs; median 10.0 h, max 103.2 h | 1 of 4 pairs (13.8 h) |
| Early resets observed | None | None | 1 (08-27, together with the 7-day window) | 3 (08-27, 08-31, 09-12) |
| Does a 7-day reset also reset the 5-hour window? | Not applicable | No: the running 5-hour window keeps its % and end (seen twice) | Not applicable | No at a natural weekly expiry (seen once); yes at the 08-27 server-side early reset, which re-anchored both |
| Idle start time banked? | Not applicable. The window starts when you start | No. A late first message still ends at the fixed Monday reset | Not applicable | Not applicable |

### 1.2 Conversions

All USD figures are at list prices: Claude from platform.claude.com, Codex at OpenAI's standard `gpt-5.6-sol` rate of $5/$30 per million tokens with cached input at 0.1×.

| Conversion | Claude (Pro) | Codex (Plus) |
|---|---|---|
| 100% of the 5-hour window, in USD | **$32** (middle 80% of windows: $20–39) | **$20.5** (range $16.7–26.3) |
| 1% of the 5-hour window | $0.32 | $0.205 |
| 100% of the 7-day window, expressed in 5-hour windows | **8.85×** after the promo (it was 9.9× during the promo) | **6.2×** (range 6.0–7.4) |
| 100% of the 7-day window, in USD | ≈ $283 (8.85 × $32; the direct weekly fit gives $314) | ≈ $127 (6.2 × $20.5) |
| 1% of the 7-day window | ≈ $2.83–3.14 | ≈ $1.27 |
| One full 5-hour window, as a share of the week | ≈ 11.3% | ≈ 16% |
| Tokens per 1% of the 5-hour window | Table in section 3.4 | Table in section 4.3 |

### 1.3 Prediction recipe for the maximizer

0. **The two meters are independent.** A weekly reset clears only the weekly meter: a 5-hour window running across it keeps its % and its end time, for both agents. The one exception seen is a Codex server-side early reset. So a 5-hour window at 96% at 18:58 on a Monday stays at 96% after the 19:00 weekly reset; only the weekly budget is fresh.

1. **Within a window, % is linear in cumulative USD.** Per window the fit of % against cumulative list-price USD has R² ≥ 0.9 in 21 of 29 Claude windows. So once the current window is at 20% or more, estimate this window's own budget live as `B_now = usd_since_window_start / pct * 100`, and trust it over the priors above.
2. **Across windows the budget varies by about ±30%.** Before the current window has enough signal, use the prior of $32 (Claude) or $20.5 (Codex) with a middle-80% band of $20–39 (Claude). Part of that variation comes from the token mix: with per-category weights (section 3.2) the model explains 73% of the across-window variance instead of 54%.
3. **Derive the weekly figures from the 5-hour ones.** The meter ratio is measured without any cost model and is the most stable quantity here: 7-day % ≈ 5-hour % / 8.85 for Claude and / 6.2 for Codex.
4. **Claude's weekly reset is fixed** (Monday 19:00 UTC), so time left in the week is known exactly. Codex's weekly reset depends on when that week's first request happened, and can also be reset early by the server.
5. **Opening a Claude 5-hour window is a choice.** A window starts at the first request, so scheduled work can decide when a window opens. For example, starting one at 07:00 UTC means a fresh window is already running when organic work starts.
6. **Watch for unseen usage on Codex.** Some Codex usage never appears locally (section 4.5), so the Codex % can rise with no local tokens. On Claude, every % step since 09-13 was matched by a local message.

## 2. Window mechanics: evidence

### 2.1 Claude 5-hour windows are activity-triggered, so idle gaps exist

A window is one distinct `resets_at` value, its start is `end - 5h`, and the gap between two consecutive windows is `next.start - prev.end`. There are 32 windows and 31 consecutive pairs.

| Gap between windows | Pairs |
|---|---|
| 0 (back-to-back) | 3 |
| Up to 20 min (indistinguishable from the 10-minute rounding of `resets_at`) | 2 |
| 0.67–2 h | 5 |
| 2–6 h | 4 |
| 6–12 h | 4 |
| 12–24 h | 10 |
| Over 24 h (26.7 h, 69.5 h, 213.8 h) | 3 |
| Negative (overlapping windows) | 0 |

Why this confirms the mechanism rather than just showing gaps:

- **The server says so directly.** 20 of the 26 idle gaps contain at least one poll reading with `five_hour.resets_at: null` and `utilization: 0` (1,863 such readings in total). The other 6 have no poll reading inside them (3 have no readings of any kind, 3 only have statusline push rows), so they rest on the local-message evidence below.
- **Nothing is sent during a gap.** None of the 26 idle gaps contains a local transcript message. Conversely, all 15,840 local messages since the first window fall inside a known 5-hour window.
- **The next window starts at the next message, not at the previous end.** For every one of the 31 pairs, the first local message after the previous window's end lands 0–14.4 minutes after the next window's computed start. That spread is the 10-minute rounding of `resets_at` plus the response time: the transcript timestamp is when the response finished, while the window opens when the request was sent.
- **The 3 back-to-back pairs are coincidence, not a rule.** In each of them (08-27 16:20, 08-31 19:40, 09-15 14:20) a message simply arrived within minutes of the previous expiry, which is what continuous work looks like.
- **Only 24% of elapsed time is covered by a 5-hour window** (160 h of 674 h). The 213.8 h gap (09-04 → 09-13) has no local messages inside it, so it is real idleness, not a logging hole, even though few quota readings exist for that stretch.
- **The % can slightly exceed 100.** Peaks of 103% were observed, because the request in flight when the cap is hit still completes.

### 2.2 Claude 7-day windows: a fixed weekly lattice, with a "no window" state on top

Five windows were observed (08-17 → 09-21), which gives four transitions.

- **The schedule has no gaps.** For the 7-day meter, `resets_at` fell on Monday 19:00 UTC every time (08-24, 08-31, 09-07, 09-14, 09-21), with consecutive ends exactly 7.0000 days apart. So start = `end - 7d` always equals the previous end.
- **It is not activity-triggered like the 5-hour window.** If a 7-day window opened at its first message, its end would be first message + 7 days. Two transitions can tell the two models apart, and both reject that:

| Transition | First local message after the reset | End if activity-triggered | Observed end |
|---|---|---|---|
| 08-24 → 08-31 | 08-25 07:32 | 09-01 07:32 | 08-31 19:00 |
| 09-07 → 09-14 | 09-13 15:24 (6 days after the reset) | 09-20 15:24 | 09-14 19:00 |

The other two transitions had a message within 14 minutes of the reset, so they cannot tell the models apart.

- **The server does report a "no window" state in between.** Right after each reset, `seven_day.resets_at` is `null` with `utilization: 0` until the first message arrives. Then the window reappears with the lattice end, not a new one. This was observed twice: for 12.49 h after the 08-24 reset (120 null readings, ending at the first message at 07:34) and for 0.24 h after the 08-31 reset (7 null readings, ending at the first message at 19:14). The 09-07 reset falls in a data hole (no readings from 09-07 15:24 to 09-13 15:24), so only the consequence was seen there: the first reading on 09-13 already carries the 09-14 lattice end.
- **Idle time at the start of a week is not banked.** The week's end does not move, so a first message late in the week gets a much shorter effective window. On 09-13 the first message came 27.6 h before the 09-14 19:00 reset, which was followed by a fresh 0% meter.

### 2.3 Codex windows: activity-triggered for both meters, with a rolling countdown when idle

- **Idle readings are placeholders, not windows.** When no window is active, Codex does not return `null` the way Claude does. It returns `usedPercent: 0` with `resetsAt` = now + span. 2,370 of the 5-hour poll readings are exactly this, with the reset time moving with the clock. Windows must therefore be detected only from readings above 0%; otherwise every idle poll looks like a new window (a naive pass found 2,387 fake 5-hour windows).
- **The first request pins both windows.** On 09-12 the readings at 11:05 and 11:24 show a moving weekly `resetsAt` (09-19 11:05, then 09-19 11:24). A message arrives, and from 11:29 on the weekly end stays at 09-19 11:24 for the rest of the week, while the 5-hour end becomes 16:24, anchored to the same moment. Unlike Claude, the Codex 7-day window has no fixed lattice.
- **5-hour gaps:** 29 windows with usage, 28 consecutive pairs: 21 idle gaps of more than 20 min (median 10.0 h, max 103.2 h), 6 back-to-back and 1 early reset.
- **Early resets happen.** In each case the server replaced a running window with a new one before its scheduled end:

| First seen | Old window | New window starts | Change |
|---|---|---|---|
| 08-27 19:45 | 5h ending 08-27 19:30 at 100%, and 7d ending 09-01 14:10 at 39–41% | 08-27 16:50, both meters | 7-day 39% → 7%; the new windows are backdated to 16:50 |
| 08-31 19:24 | 7d ending 09-03 16:50 at 81% (74% on the last session reading) | 08-31 19:20 | Already back to a 0% rolling countdown when first polled on 08-30 09:16 |
| 09-12 11:29 | 7d ending 09-15 09:10 at 1–2% | 09-12 11:20 | Already back to a 0% rolling countdown by 09-12 08:12 |

The cause cannot be determined from this data. It could be a server-side reset (OpenAI has reset limits account-wide after incidents), a plan event, or near-empty windows being dropped. A planner must tolerate a Codex weekly meter that suddenly returns to 0%.

### 2.4 A 7-day reset does not reset the 5-hour window

Check: for every weekly reset that was actually reached, take the last reading before it and the first after it, keeping only cases where a 5-hour window was running across the reset. There are three such cases. At the other natural weekly resets, no 5-hour window was active within 40 minutes.

| Agent | Weekly reset (UTC) | Just before: 5h % / 5h end / 7d % | Just after: 5h % / 5h end / 7d % |
|---|---|---|---|
| Claude | 08-24 19:00 | 53% / 19:20 / 81% | 53% / 19:20 / 0% |
| Claude | 08-31 19:00 | 96% / 19:40 / 88% | 96% / 19:40 / 0% (later 100%, same end) |
| Codex | 09-07 19:20 | 1% / 09-08 00:14 / 46% | 2% / 09-08 00:14 / 0% |

- **The 5-hour window is untouched in every case.** Its % carries on, and its end time is the same to the minute. Only the weekly meter goes to 0%.
- **Usage in the running 5-hour window is not carried into the new week.** On Claude, after the 08-31 reset the 5-hour meter still read 96–100% while the weekly meter restarted at 0%. The part of that 5-hour window spent before 19:00 was charged to the old week only.
- **Nor does the running 5-hour window pin the new week.** Right after a weekly reset, Claude's weekly meter reports `null` (section 2.2) and Codex's the idle rolling countdown (0%, now + 7 d), even while a 5-hour window is active. The new week is pinned by the next request: at 19:14 on 08-31 for Claude (on the lattice), and at 21:50 on 09-07 for Codex.
- **Exception: Codex server-side early resets.** On 08-27 (section 2.3) both meters were replaced together. The 5-hour window running since 14:30 at 100% and the weekly at 39–41% were replaced by windows backdated to 16:50, at 44% and 7%. The other two early weekly resets (08-30, 09-12) happened while no 5-hour window was active, so they show nothing either way.
- **Limits:** three clean cases, two for Claude and one for Codex, and the Codex one is at only 1–2%. The converse (a 5-hour reset leaving the weekly meter alone) holds everywhere in the data except that same Codex 08-27 early reset: the weekly end and % never changed at a 5-hour boundary. Reproduce with `quota_model.py`'s last section.

## 3. Claude: converting %, tokens and USD

### 3.1 The 5-hour budget in USD

Method: for each window, start = `resets_at - 5h` (exact, section 2.1). For every quota reading, cumulative list-price USD is summed over all local messages since the window start, and `pct = 100 * usd / B` is fitted through the origin. Readings are reduced to the per-window running maximum first (see section 5), and only readings at 5% or more are used.

- **Result:** B has a median of **$32.0** over the 23 windows that peaked at 30% or more. The middle 80% (p10–p90) is **$20.5–38.8**, and within a window the fit has R² ≥ 0.9 in 21 of 29 windows.
- **Stable across the promo end:** the median is $32.0 over the 13 windows before 09-01 and $32.2 over the 10 windows after. The +50% promo only ever applied to the weekly meter.
- **List prices hold across models:** windows with 43–81% Opus spend had B of $27–32, the same as pure-Sonnet windows. The official 2.5× Opus/Sonnet price ratio therefore matches the meter.
- **Low-budget windows exist:** 09-04 ($19.7), 09-13 ($18.4, $18.5), 09-16 07:00 ($23.7) and 09-01 ($14.6, low R²). The 7-day meter moved in step with the 5-hour one in those windows, and every % step was matched by local messages. So this is not unseen usage: the meter really charged more per list-price dollar. The token mix explains part of it (section 3.2); 09-04 remains unexplained.
- **No peak-hour surcharge detected.** Using % steps since 09-13, the cost per 1% was $0.343 on weekdays 12–18 UTC and $0.383 on other weekday hours. Weekends came out lower ($0.19–0.23), but that is driven by the single cheap Saturday window on 09-13, so it is not evidence of a weekend effect.

### 3.2 Token-type weights: the meter is not exactly list-price USD

A non-negative least-squares fit of each window's peak % on the list-price USD of each token category, across 23 windows:

| Category | Weight relative to a single USD slope | Leave-one-out range | List-price USD per 1% of the 5-hour window |
|---|---|---|---|
| Input + output (input is ~0% of spend) | 2.10 | 1.29–3.28 | $0.159 |
| Cache write (5 min and 1 h) | 1.84 | 1.60–2.24 | $0.182 |
| Cache read | 0.40 | 0.17–0.57 | $0.826 |

- **Cache reads count about 4–5× less against the meter than list price implies,** relative to output and cache writes. Equivalently, a cache read counts about 0.02× of base input in meter terms, not 0.1×.
- **This explains about half of the across-window variance.** R² across windows rises from 0.54 (single USD slope) to 0.73. Windows light on cache reads have low B: 09-13 had a 35–37% cache-read share and B of about $18, while typical windows have a 55–65% share and B of about $32.
- **Individual weights are only loosely determined** with 23 windows (see the leave-one-out ranges). Use them as a prior and let live calibration (section 1.3) correct them.

### 3.3 The 7-day budget and the end of the promo

- **The meter ratio, measured without any cost model:** within each 5-hour window, Δ5h% / Δ7d% = B_7d / B_5h. Aggregated over windows it is **9.9** during the promo (08-24 → 08-31, 13 windows) and **8.85** after it (09-13 → 09-21, 12 windows, per-window range 6.5–11.2). One outlier (09-04, 31.7, while the 7-day meter was only sparsely polled) is excluded.
- **Direct weekly fits:** $380 and $320 for the two promo weeks, and $314 for the 09-14 → 09-21 week (R² 0.99, peak 47%). The 09-07 week gives $197, but that week's usage happened almost entirely in the cheap 09-13 windows (section 3.1). The 08-31 week only reached 10%, which is too little to fit.
- **The promo end cut the weekly allowance by about 11%, not by a third.** The +50% promo ended 2026-08-31 23:59 PT, and the expected ratio drop was 9.9 → 6.6. The observed drop was 9.9 → 8.85, with a flat 5-hour budget. The earlier "~6.7× post-promo" estimate is therefore wrong for this account. Why is unknown: a changed standard allowance, an extended promo, or a different promo mechanism.
- **Working value:** B_7d ≈ 8.85 × $32 ≈ **$283**, with the direct fit giving $314. A saturated 5-hour window uses about 11.3% of the week, so about 8.85 saturated windows fit in a week.

### 3.4 Tokens per 1% of the Claude 5-hour window

Two models are given. The single-USD model is the simpler one ($0.32 per 1%, section 3.1). The category model uses section 3.2's weights and fits across windows better but is noisier.

| Token type | Sonnet 5: single USD model | Sonnet 5: category model | Opus 5: single USD model | Haiku 4.5: single USD model |
|---|---|---|---|---|
| Output (thinking included) | 32 K | 16 K | 12.8 K | 64 K |
| Input (uncached) | 160 K | 80 K | 64 K | 320 K |
| Cache write, 5 min | 128 K | 73 K | 51 K | 256 K |
| Cache write, 1 h | 80 K | 45 K | 32 K | 160 K |
| Cache read | 1.6 M | 4.1 M | 640 K | 3.2 M |

- **Pricing used:** Sonnet 5 costs $2/$10 per million tokens in/out, Opus 5 $5/$25 (2.5× Sonnet) and Haiku 4.5 $1/$5 (half of Sonnet). Cache writes cost 1.25× (5 min) or 2× (1 h) base input, and cache reads 0.1×. Opus 5.5 (from 2026-09) is $4/$20 with cache reads at 0.05×; this table and the full price list are verified against Claude Code's own per-request cost and ccusage in `../USAGE_DATA_SOURCES.md` §3.4.
- **Category model for other models:** divide the Sonnet category-model figures by 2.5 for Opus, or multiply them by 2 for Haiku.
- **Where the spend goes** (since 08-24, list-price USD): cache read 58.9%, cache write 26.5% (1-hour writes 24.3%), output 14.6%, input about 0%. By token count, cache reads are 97.3% of all tokens. By model: Sonnet 89%, Opus 11%, Haiku 0%.

## 4. Codex: converting %, tokens and USD

### 4.1 The 5-hour budget in USD

The method is the same as for Claude. Tokens come from `last_token_usage` in each deduplicated local `token_count` event, whose rate-limit snapshot is taken right after that turn. USD is at the `gpt-5.6-sol` standard list price of $5 per million uncached input, $0.50 cached input and $30 output. (That is an assumed reference: OpenAI's current promotional price is $4/$20, and 166 of the events are `codex-auto-review`, priced like Sol here.)

- **Result:** eight windows are fully explained by local usage (R² ≥ 0.97, peaks 49–100%): B = $23.6, 21.2, 17.3, 26.3, 20.8, 20.1, 19.3 and 16.7. The median is **$20.5** and the range $16.7–26.3.
- **Excluded windows:** the 08-27 16:50 window (created by the early reset) and 08-28 05:50/11:40 (locally explained B of $5–8, R² < 0.5) contain usage that is not in the local sessions (section 4.5).

### 4.2 The 7-day budget

- **Meter ratio:** Δ5h% / Δ7d% over each 5-hour window is **6.2** (median of 12 windows, range 6.0–7.4, and exactly 6.2 in all four windows that went from 0 to 100%). This is the most reliable Codex number.
- **B_7d ≈ 6.2 × $20.5 ≈ $127.** A saturated 5-hour window uses about 16% of the week.
- **Direct weekly fits are unreliable** ($40–116, low R²), because of the early resets and the unseen usage.

### 4.3 Tokens per 1% of the Codex 5-hour window

| Token type | Single USD model ($0.205 per 1%) | Raw-token fit (NNLS, 827 readings) |
|---|---|---|
| Uncached input | 41 K | 16.5 K |
| Cached input | 410 K | 546 K |
| Output (reasoning included) | 6.8 K | Not identifiable |

- **Output cannot be weighted from this data** because it is only 0.2% of Codex tokens. Use the single USD model for output-heavy work.
- **Cached input counts about 0.03× uncached input** in the raw-token fit, against 0.1× at list price. So cached input is cheap for Codex too.
- **Both models fit equally well** (R² 0.66 for the token fit, 0.64 for USD), so neither is clearly better.
- **Token and spend shares:** by tokens, uncached input is 3.0%, cached input 96.8% and output 0.2%. By list-price USD it is 21%, 69% and 10%.

### 4.4 No dollar figure is reported by either provider

- **Claude:** `limit_dollars`, `used_dollars` and `remaining_dollars` exist on every limit block but are `null` in every reading, because usage credits are disabled on this account (`spend.enabled: false`).
- **Codex:** `credits` is `{hasCredits: false, balance: "0"}`. The only real dollar field, the per-thread `estimatedUsageUsdMicros` from `account/usage/read` with a `threadId`, has never been fetched (`threadUsage` is empty in every row). Fetching it would validate section 4.1 directly (next step 2 in `AGENTS.md`).

### 4.5 Codex usage that local data does not see

- Two 5-hour windows reached 38% and 43% with no local tokens at all (09-02 08:20 and 09-13 09:10).
- Three more windows rose far faster than their local tokens explain (08-27 16:50, 08-28 05:50 and 08-28 11:40).
- The Debian VM has no Codex sessions, so this usage is from another Codex client on the same ChatGPT account, such as Codex cloud tasks, the IDE extension or the ChatGPT app.
- On Claude, none of the 490 % steps since 09-13 came without local usage in the preceding 15 minutes. The VM's Claude transcripts all predate 08-24.

## 5. Organic usage profile (Claude, since 08-24)

This is what the maximizer's organic-usage forecast can start from. All figures are list-price USD.

| Metric | Value |
|---|---|
| Days with any usage | 19 of 29 |
| Spend per active day | median $21, max $71 |
| Average spend by weekday | Mon $38, Tue $16, Wed $23, Thu $16, Fri $10, Sat $6, Sun $27 |
| Busiest UTC hours (average $/day) | 16:00 ($3.7), 09:00 ($2.3), 15:00 ($2.0), 13:00–14:00 ($1.4–1.5) |
| Quiet UTC hours | 23:00–06:00 (≈ $0) |
| Time inside a 5-hour window | 24% of all elapsed time |

In quota terms, an average active day is about 65% of one 5-hour window and about 7% of the week. Over the whole period, organic use has left a large part of the weekly quota unused (weekly peaks of 81%, 88%, 10%, 27% and 47%).

## 6. Limits and caveats

- **List-price USD is a proxy.** The meter tracks it within a window (R² ≥ 0.9) but not exactly across windows. Token-mix weights (section 3.2) explain part of the spread; the rest is unexplained.
- **Message deduplication is required.** Claude Code writes one transcript line per content block, each repeating the same `usage`. Summing lines without deduplicating by `message.id` doubles the cost (2.02× on this data).
- **Stale statusline readings.** Idle sessions keep pushing their last-known, older %. Within one window the true % never decreases, so both scripts keep only the first reading of each new maximum. Without that filter, fits on the dense push era fall apart (R² 0.27 instead of 0.93 on 09-14).
- **`observed_at` on push rows looks wrong.** It is up to 1 h *after* the row's own `ts`, which is impossible for a timestamp meant to be earlier. The scripts use `ts`. Worth checking `push-claude-quota.sh`.
- **Local transcripts only cover this Mac.** For Claude that looks complete (section 4.5). For Codex it is known not to be.
- **Resolution is coarse.** The % is an integer, Claude 5-hour ends snap to 10 minutes and 7-day ends to the hour. Gaps under about 20 minutes cannot be told from zero, and % steps of 1 on the 7-day meter make short-interval ratios noisy (the aggregated ratios are used).
- **Small samples on the 7-day meters.** There are 5 Claude weeks (only 1 fully post-promo with enough usage) and 4 Codex weeks disturbed by early resets. What fixes Claude's Monday 19:00 UTC anchor is not visible in this data.
- **Synthetic error messages are excluded.** Three `<synthetic>` "organization has disabled subscription access" stubs on 08-30 were rejected requests: they neither consume quota nor open a window.
- **Plans and prices can change.** Everything here is for Claude Pro and ChatGPT Plus in 2026-08/09. The Codex USD scale depends on which OpenAI price is taken as the reference.

## 7. Corrections to earlier notes

- **The Claude 5-hour budget is $32, not $72.80.** The $72.80 in `AGENTS.md` (and the derived "1pp ≈ $0.73" and per-token table) came from `analysis.ipynb`, which sums transcript lines without deduplicating messages and so counts every message about twice. The notebook's recompute cell still has this bug.
- **The Claude post-promo weekly ratio is about 8.85, not about 6.7.** The 6.7 figure was a prediction; the measurement did not confirm it.
- **`AGENTS.md` said four 5-hour gaps existed**, 0.3–13.7 h long. That was the state on 2026-08-26. With four weeks of data it is 26 gaps of more than 20 minutes, up to 213.8 h.
- **The 7-day window is on a fixed lattice**, not a "rolling schedule", and it has a "no window" state after each reset.
