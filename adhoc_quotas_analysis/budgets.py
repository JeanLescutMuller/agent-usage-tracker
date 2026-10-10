"""Q2. How many $ make 1% of a window, and how many 5-hour windows fit in a week? Per plan.

  B         a window's budget: the $ that would take it to 100%. Fitted per window: pct = 100 * usd / B,
            through the origin, usd = Claude Code's own $ spent since the window's start, at each reading
            that first showed a new highest percent (load.windows' steps) at MIN_PCT or more
  ratio     how many 5-hour windows fit in a week: within one 5-hour window, (5-hour % gained) / (7-day %
            gained). It needs no $ at all, so it covers every window since 08-24

Read-only, no network: `python3 adhoc_quotas_analysis/budgets.py`. Reads the readings (Category 1) and the
slots (Category 3). A window's $ is known only where Claude Code reported it (telemetry, since 2026-09-30):
a window whose tokens are more than MAX_UNPRICED without `usd` gets no B. Nothing here prices tokens.
"""
import bisect
import collections

import load
from load import fmt

MIN_PCT = 5          # below, the 1% resolution of the percent is too coarse
MIN_PEAK = 30        # windows summarised: those that reached this percent
MAX_UNPRICED = 0.05  # share of a window's tokens allowed without Claude Code's $

l_readings = load.readings()
l_slots = load.slots()
l_mid = [d_s["ts"] + 150 for d_s in l_slots]   # a slot counts as spent at its middle


def spent(lo: float, hi: float) -> dict:
    """usd, tokens and unpriced tokens of the slots whose middle is in [lo, hi), plus tokens per column."""
    d_t = collections.Counter()
    for d_s in l_slots[bisect.bisect_left(l_mid, lo):bisect.bisect_left(l_mid, hi)]:
        d_t["usd"] += d_s["usd"] or 0
        d_t["unpriced"] += d_s["tokens_without_usd"]
        d_t["requests"] += d_s["requests"]
        for c in load.TOKEN_COLUMNS:
            d_t[c] += d_s[c]
    d_t["tokens"] = sum(d_t[c] for c in load.TOKEN_COLUMNS)
    return d_t


def fit(d_w: dict) -> dict:
    """The window's B and R², or why it has none."""
    l_xy = [(spent(d_w["start"], ts)["usd"], pct) for ts, pct in d_w["steps"] if pct >= MIN_PCT]
    d_all = spent(d_w["start"], max([ts for ts, _ in d_w["steps"]] or [d_w["start"]]))
    d_f = dict(d_w, usd=d_all["usd"], tokens=d_all["tokens"],
               unpriced=d_all["unpriced"] / d_all["tokens"] if d_all["tokens"] else 0,
               cache_read=d_all["cache_read_tokens"] / d_all["tokens"] if d_all["tokens"] else 0, b=None, r2=None)
    if d_f["unpriced"] > MAX_UNPRICED:
        d_f["why"] = f"{100 * d_f['unpriced']:.0f}% of tokens without $"
    elif len(l_xy) < 3 or not any(x for x, _ in l_xy):
        d_f["why"] = "fewer than 3 readings at 5%+"
    else:
        sxy, sxx = sum(x * y for x, y in l_xy), sum(x * x for x, _ in l_xy)
        mean = sum(y for _, y in l_xy) / len(l_xy)
        sst = sum((y - mean) ** 2 for _, y in l_xy)
        d_f["b"] = 100 * sxx / sxy
        d_f["r2"] = 1 - sum((y - x * sxy / sxx) ** 2 for x, y in l_xy) / sst if sst else None
    return d_f


def table(kind: str) -> list[dict]:
    l_f = [fit(d_w) for d_w in load.windows(l_readings, kind)]
    print(f"\n=== {kind} windows: B, the $ at 100% ===")
    print("plan   start        end          peak %    usd $   tokens M  cache read %       B $     R2   ")
    for d_f in l_f:
        if d_f["peak"] < MIN_PCT or (d_f["tokens"] and d_f["unpriced"] == 1):
            continue
        print(f"{d_f['plan']:6} {fmt(d_f['start'])}  {fmt(d_f['end'])}  {d_f['peak']:6.0f}  {d_f['usd']:7.2f}  "
              f"{d_f['tokens'] / 1e6:8.1f}  {100 * d_f['cache_read']:12.0f}  "
              + (f"{d_f['b']:8.2f}  {d_f['r2']:5.2f}" if d_f["b"] else f"{'-':>8}  {'-':>5}  {d_f['why']}"))
    l_none = [d_f for d_f in l_f if d_f["peak"] >= MIN_PCT and d_f["tokens"] and d_f["unpriced"] == 1]
    if l_none:
        print(f"and {len(l_none)} windows ({fmt(l_none[0]['start'])} -> {fmt(l_none[-1]['end'])}) without any $ "
              "from Claude Code: no B")
    return l_f


l_f5, l_f7 = table("5h"), table("7d")

# --- how many 5-hour windows fit in a week ----------------------------------------------------------------------
print("\n=== 5-hour windows per week: (5-hour % gained) / (7-day % gained) within each 5-hour window ===")
l_w7 = load.windows(l_readings, "7d")


def pct7_at(ts: float, plan: str) -> float | None:
    """The 7-day percent reached by ts, in the 7-day window running then."""
    for d_w in l_w7:
        if d_w["plan"] == plan and d_w["start"] <= ts < d_w["end"]:
            return max([pct for t, pct in d_w["steps"] if t <= ts] or [0])
    return None


d_ratio = collections.defaultdict(list)
for d_w in l_f5:
    if len(d_w["steps"]) < 2:
        continue
    (t0, p0), (t1, p1) = d_w["steps"][0], d_w["steps"][-1]
    q0, q1 = pct7_at(t0, d_w["plan"]), pct7_at(t1, d_w["plan"])
    if q0 is None or q1 is None or p1 - p0 < 10 or q1 <= q0:
        continue
    d_ratio[d_w["plan"]].append((p1 - p0, q1 - q0))
    print(f"  {d_w['plan']:6} {fmt(d_w['start'])}  5h +{p1 - p0:3.0f}  7d +{q1 - q0:3.0f}  ratio {(p1 - p0) / (q1 - q0):5.1f}")

# A 7-day meter rising 5 points or more within 15 min (the 5-hour one would take about 40 points to do that)
# breaks the ratio of its 5-hour window: list those jumps.
for d_w in l_w7:
    for (t0, p0), (t1, p1) in zip(d_w["steps"], d_w["steps"][1:]):
        if p1 - p0 >= 5 and t1 - t0 <= 900:
            print(f"  7-day jump {d_w['plan']} {fmt(t0)} {p0:.0f}% -> {fmt(t1)} {p1:.0f}%: its 5-hour window's ratio is off")

# --- summary ----------------------------------------------------------------------------------------------------
print(f"\n=== summary per plan (windows that reached {MIN_PEAK}%, with B) ===")
for plan in [name for _, name in load.PLANS]:
    l_b5 = [d_f for d_f in l_f5 if d_f["plan"] == plan and d_f["b"] and d_f["peak"] >= MIN_PEAK]
    l_b7 = [d_f["b"] for d_f in l_f7 if d_f["plan"] == plan and d_f["b"] and d_f["peak"] >= MIN_PEAK]
    l_r = d_ratio[plan]
    ratio = sum(a for a, _ in l_r) / sum(b for _, b in l_r) if l_r else None
    print(f"  {plan}: 5-hour B ${load.quantiles([d_f['b'] for d_f in l_b5])} (n={len(l_b5)}), "
          f"1% = ${(load.quantiles([d_f['b'] / 100 for d_f in l_b5]).split()[0]) if l_b5 else '-'}; "
          f"7-day B ${load.quantiles(l_b7)} (n={len(l_b7)}); "
          f"5-hour windows per week: median {load.quantiles([a / b for a, b in l_r])}, "
          f"all gains summed {f'{ratio:.2f}' if ratio else '-'} (n={len(l_r)})")
    if l_b5:
        # What 1% holds at this plan's average mix: each column's tokens per $ spent, times the $ in 1%.
        d_t = collections.Counter()
        for d_f in l_b5:
            for k, v in spent(d_f["start"], d_f["end"]).items():
                d_t[k] += v
        one = sorted(d_f["b"] for d_f in l_b5)[len(l_b5) // 2] / 100
        print("      1% at the average mix: " + ", ".join(
            f"{c.replace('_tokens', '')} {d_t[c] / d_t['usd'] * one / 1e3:,.0f}K" for c in load.TOKEN_COLUMNS if d_t[c]))
