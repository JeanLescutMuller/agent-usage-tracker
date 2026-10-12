# Conclusions

The answers to the three questions of `README.md`, Claude only. Two kinds, never mixed:

| Kind | Where from | Re-run |
|---|---|---|
| **Re-measured** (§1–§3) | the scripts here, on the readings (Category 1) and the `slots` (Category 3); `$` = Claude Code's own | any time; the numbers below are from the runs of **2026-10-12 19:00 UTC** on the VM (both machines' slots), the end of the first full Max 5x week |
| **Frozen** (§4) | the scripts retired on 2026-10-11, on 2026-08-24 → 09-21 data; their `$` = tokens × list price, computed by the script, which this folder no longer does | not here: `git show 12956dc:adhoc_quotas_analysis/CONCLUSIONS.md` has the full evidence |

Plans (`load.PLANS`): **pro+promo** until 2026-09-01 06:59 UTC (Anthropic's +50% weekly promo), **pro** until 2026-10-07 16:51 UTC, **max5x** since. A percent of one plan is never compared with another's.

## 1. When does a window start and end? (`windows.py`)

| | 5-hour window | 7-day window |
|---|---|---|
| What opens it | the first request after the previous window ended | nothing: a fixed weekly time |
| Start → end | start = first request, rounded to 10 min; end = start + 5 h | end every **Monday 19:00 UTC**, 7.000 days apart (8 ends, 08-24 → 10-12); start = end − 7 d |
| Between windows | no window: the server reads 0% with no reset time | right after a reset, no reset time until the first request (08-24: 12.6 h, 134 readings; 08-31: 0.24 h); then the Monday end reappears |
| Idle time banked? | not applicable | no: in the week to 09-14, the first request came on 09-13 15:20, about 28 h before its end |
| The other meter's reset | — | a 7-day reset leaves a running 5-hour window alone: same %, same end (08-24 53%, 08-31 96%, 10-05 100%) |

Evidence for the 5-hour rule, 78 windows from 08-22 to 10-12:

| Measure | Value |
|---|---|
| Consecutive pairs (same plan) | 75: 18 back to back, 7 within 20 min (the 10-min rounding), **50 idle gaps**, 0 overlaps |
| Idle gaps | median 8.8 h, max 213.8 h; 41 of 50 hold a server "no window" reading, 9 have no reading at all |
| Time inside a 5-hour window | 31% (388 h of 1,234 h) |
| First activity after a window's end | at the next window's start (0–15 min), except below |

Exceptions, each one a finding:

- **7 windows opened with no activity in the slots** of either machine (run on the VM, which holds both): 09-25 17:10, 22:30, 09-26 03:50, **10-09 04:30**, **10-10 01:00**, **10-11 09:20** and **10-11 20:00** (first activity 30–370 min later). Something outside Claude Code's transcripts used the account: claude.ai, the apps, cloud sessions (`../FOR_READERS.md` rule 6).
- **One hole in the readings**, 08-23 01:40 → 08-24 14:20: 44 active slots, no reading, so the windows of that day are missing.
- **The plan change** (10-07 16:51) put both meters to 0%: a new 5-hour window, and a 7-day window that kept its Monday 10-12 19:00 end.

## 2. How many $ make 1% of a window? (`budgets.py`)

`B` = the $ that take a window to 100%, fitted per window on its readings (`README.md`). Claude Code reports a $ only since 2026-09-30, and not for every session, so only windows from 10-05 have one: 41 earlier 5-hour windows have none.

| Plan | 5-hour B: median [p10–p90] | 1% of 5 h | 7-day B | 5-hour windows per week: median (all gains summed) |
|---|---|---|---|---|
| pro+promo | — | — | — | 9.86 (9.81), 15 windows |
| pro | **$47.29** [$33.05–50.74], 6 windows (10-05 → 10-07) | $0.47 | $357 (one week, cut at 60% by the plan change) | **7.83** (8.46), 30 windows |
| max5x | **$194.17** [$179.47–202.21], 7 windows | **$1.94** | $1,469 (the first full week, 10-07 16:51 → 10-12 19:00, at 69%) | 6.50 (4.16), 8 windows |

- **Max 5x holds about 4.1× Pro, not 5×**, in Claude Code's $, on both meters: 5-hour B $194.17 / $47.29 = 4.11, 7-day B $1,469 / $357 = 4.11 (first measure on 2026-10-11, from 4 windows: 4.3×).
- **A week holds about 7.6 full 5-hour windows of $ on both plans**: 7-day B / 5-hour B = $1,469 / $194.17 = 7.57 on Max 5x, $357 / $47.29 = 7.55 on Pro (Pro's 7-day B is fitted on a week cut at 60%).
- **Within a window, the % follows the $ closely**: R² ≥ 0.95 in 9 of the 13 summarised windows, ≥ 0.88 in 12; one bad fit (max5x 10-12 08:10, R² −0.31, B $198 all the same).
- **One outlier: max5x 10-12 13:10 → B $134** (85% for $125): 1% cost about 30% fewer $ than in the other Max windows. Use outside Claude Code's transcripts in that window (claude.ai, the apps, cloud sessions, as in §1) would do this; not explained yet.
- **1% of a Max 5x 5-hour window, at the average mix**: 5.1 M cache-read tokens, 71 K cache-write, 18 K output and 18 K input tokens. Cache reads are 98% of all tokens.
- **The 7-day meter jumped twice**, faster than any 5-hour use explains: on 09-14 from 20% to 27% in 13 min, and on 10-08 from 7% to 18% in 9 min. With 10-10 12:10 (5-hour +74 for 7-day +30), they keep Max's "all gains summed" figure low (4.16, median 6.50); the B ratio above (7.57) does not depend on them.
- **Pro's 5-hour windows per week**: the frozen 8.85 (09-13 → 09-21) is 7.83 as a median, 8.46 summed, over the 30 windows from 09-01 to 10-07.
- **$32 (frozen, §4) and $47 (here) are not comparable.** The $32 is tokens × list price for Sonnet 5 and Opus 5 in 08/09. The $47 is Claude Code's own $, mostly Opus 5.5, in 10.

## 3. Where do the $ go? (`usage_split.py`)

The first full Max 5x week (10-07 16:51 → 10-12 19:00, peak 69%, $1,021.88, 2,707 M tokens):

| Project | $ | of $ |
|---|---|---|
| `~` (sessions started in the home folder) | 334.44 | 32.7% |
| trading-bot | 169.99 | 16.6% |
| multi-host-orchestrator | 164.57 | 16.1% |
| agent-usage-tracker | 112.53 | 11.0% |
| Bot_crypto_copytrade | 70.44 | 6.9% |
| `~/dev` (sessions started outside a repo) | 46.09 | 4.5% |
| job-runner | 41.83 | 4.1% |
| smart-orchestrator | 28.00 | 2.7% |
| 18 others | 53.99 | 5.3% |

| By kind of use, the same week | $ | tokens |
|---|---|---|
| development (interactive) | $1,021.70 | 2,702 M |
| test runs | $0.18 | 4.3 M, 3.9 M of them without $ |
| scheduled runs | $0.00 | 0.4 M, all without $ |

Test and scheduled runs send no telemetry, so their tokens have no $ (`TODO/0004`). By machine: the Mac $1,021.86, the VM $0.02 (one request).

## 4. Frozen findings (Pro and Codex, 2026-08/09)

Measured by the scripts retired on 2026-10-11, at **list price**: tokens × the public price. They stay true for what they measured, and are not re-measured here.

| Finding | Claude (Pro) | Codex (ChatGPT Plus) |
|---|---|---|
| 100% of the 5-hour window | $32 (p10–p90 $20.5–38.8, 23 windows; Sonnet 5 $2/$10, Opus 5 $5/$25) | $20.5 (8 windows; gpt-5.6-sol at $5/$30) |
| 5-hour windows per week | 9.9 with the promo, 8.85 after | 6.2 |
| What a 1% weighs | cache reads count 4–5× less than list price says; output and cache writes about 2× | cached input about 0.03× uncached input (list price 0.1×) |
| Window rules | as in §1 | both meters open at the first request (no Monday time); when idle the server shows 0% and a reset that moves with the clock; 3 early resets by the server (08-27, 08-31, 09-12) |
| Usage the local files miss | none seen (every % step had a local request) | yes: 2 windows reached 38–43% with no local tokens |
| Other | the promo end cut the week by about 11%, not a third; no peak-hour surcharge seen | — |

Neither provider reports a $ for a subscription quota (Claude's `*_dollars` fields are null; Codex's credits are 0).

## 5. Limits

- **Each machine's slots stay on it, except the Mac's, which the VM also holds**: run on the Mac, the scripts see the Mac's usage only; run on the VM, both (the numbers here). The VM's Claude use is small (41 slots, the last on 10-08).
- **A $ exists only where Claude Code sent telemetry**: none before 2026-09-30, and none for Agent SDK sessions or sessions started before telemetry was turned on. `B` is fitted only where at most 5% of a window's tokens lack one.
- **Resolution**: the percent moves in 1% steps, 5-hour ends snap to 10 min, and a slot is 5 min (its $ counts at its middle).
- **Small samples on Max 5x**: 7 windows at 30% or more, and one full week.
