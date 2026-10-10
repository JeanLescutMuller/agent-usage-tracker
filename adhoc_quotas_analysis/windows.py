"""Q1. When does a window start and end?

  5-hour: does a window open at the first request, so that idle gaps sit between windows?
  7-day:  is its end a fixed weekly time, or the first request + 7 days?
  both:   does a 7-day reset also reset a running 5-hour window?

Read-only, no network: `python3 adhoc_quotas_analysis/windows.py`. Reads the readings (Category 1) and,
for activity, the slots (Category 3, 5-min resolution, the machines this runtime holds). Its tables are
the evidence of CONCLUSIONS.md §1.
"""
import bisect
import datetime as dt
import statistics

import load
from load import fmt

GRID_NOISE_S = 1200   # a gap up to 20 min cannot be told from the 10-min rounding of the reset time

l_readings = load.readings()
# Activity: the start of every slot with at least one request.
l_active = sorted({d_s["ts"] for d_s in load.slots() if d_s["requests"]})


def first_active(lo: float) -> int | None:
    i = bisect.bisect_left(l_active, lo)
    return l_active[i] if i < len(l_active) else None


def n_active(lo: float, hi: float) -> int:
    """Slots with a request entirely inside [lo, hi)."""
    return max(0, bisect.bisect_right(l_active, hi - 300) - bisect.bisect_left(l_active, lo))


# --- 5-hour windows ---------------------------------------------------------------------------------------------
l_w5 = load.windows(l_readings, "5h")
l_null5 = [d_r["ts"] for d_r in l_readings if d_r["source"] == "claude_api" and d_r["end"]["5h"] is None]
print(f"=== 5-hour windows: {len(l_w5)}, {fmt(l_w5[0]['start'])} -> {fmt(l_w5[-1]['end'])} UTC "
      f"(machines with activity: {sorted({d_s['machine'] for d_s in load.slots()})}) ===")
print("plan   prev end     next start   gap h   active slots in gap  server 'no window' readings  first activity - next start (min)")
l_ts = [d_r["ts"] for d_r in l_readings]
between = lambda l_sorted, lo, hi: bisect.bisect_left(l_sorted, hi) - bisect.bisect_left(l_sorted, lo)
print("plan   prev end     next start   gap h  readings  server 'no window'  active slots  first activity - next start (min)")
l_gaps = []
for d_a, d_b in zip(l_w5, l_w5[1:]):
    if d_a["plan"] != d_b["plan"]:
        continue  # the plan change replaced the window: not a gap
    first = first_active(d_a["end"])
    d_g = dict(prev=d_a["end"], start=d_b["start"], gap=d_b["start"] - d_a["end"],
               readings=between(l_ts, d_a["end"], d_b["start"]), nulls=between(l_null5, d_a["end"], d_b["start"]),
               active=n_active(d_a["end"], d_b["start"]), lag=None if first is None else round((first - d_b["start"]) / 60))
    l_gaps.append(d_g)
    print(f"{d_b['plan']:6} {fmt(d_a['end'])}  {fmt(d_b['start'])}  {d_g['gap'] / 3600:6.2f}  {d_g['readings']:8}  "
          f"{d_g['nulls']:18}  {d_g['active']:12}  {'-' if d_g['lag'] is None else d_g['lag']:>8}")
l_idle = [d_g for d_g in l_gaps if d_g["gap"] > GRID_NOISE_S]
print(f"\npairs {len(l_gaps)}: back to back {sum(abs(d_g['gap']) < 60 for d_g in l_gaps)}, "
      f"within 20 min {sum(60 <= d_g['gap'] <= GRID_NOISE_S for d_g in l_gaps)}, idle gaps {len(l_idle)}, "
      f"overlaps {sum(d_g['gap'] < -GRID_NOISE_S for d_g in l_gaps)}")
if l_idle:
    l_h = [d_g["gap"] / 3600 for d_g in l_idle]
    print(f"idle gaps: median {statistics.median(l_h):.1f} h, max {max(l_h):.1f} h; "
          f"with a server 'no window' reading {sum(d_g['nulls'] > 0 for d_g in l_idle)}/{len(l_idle)}; "
          f"with no reading at all (a hole in the readings) {sum(d_g['readings'] == 0 for d_g in l_idle)}/{len(l_idle)}")
# A request opens the next window, so the first activity after a window's end should fall at the next start
# (0-15 min later: slot start, 10-min rounding, response time). Exceptions are listed.
for d_g in l_gaps:
    if d_g["active"]:
        print(f"  activity inside the gap {fmt(d_g['prev'])} -> {fmt(d_g['start'])} ({d_g['active']} slots, "
              f"{d_g['readings']} readings): a window the readings missed")
    elif d_g["lag"] is not None and not -10 <= d_g["lag"] <= 15:
        print(f"  the window starting {fmt(d_g['start'])} opened with no activity seen: the first came {d_g['lag']} min later "
              f"(usage the slots don't see: another machine, claude.ai)")
l_start = [d_w["start"] for d_w in l_w5]
l_after = [t for t in l_active if t >= l_start[0]]
# Outside: no window started before the slot ended, or the last one that did had ended before the slot began.
n_out = sum(1 for t in l_after if t >= l_w5[bisect.bisect_right(l_start, t + 299) - 1]["end"])
print(f"active slots since the first window: {len(l_after)}, outside every 5-hour window: {n_out}")
covered = sum(d_w["end"] - d_w["start"] for d_w in l_w5)
print(f"time inside a 5-hour window: {covered / 3600:.0f} h of {(l_w5[-1]['end'] - l_w5[0]['start']) / 3600:.0f} h "
      f"({100 * covered / (l_w5[-1]['end'] - l_w5[0]['start']):.0f}%)")

# --- 7-day windows ----------------------------------------------------------------------------------------------
l_w7 = load.windows(l_readings, "7d")
print(f"\n=== 7-day windows: {len(l_w7)} ===")
print("plan   start        end          end weekday   end - previous end (d)  peak %  first activity after previous end  "
      "end if it opened then")
prev = None
for d_w in l_w7:
    first = first_active(prev["end"]) if prev else None
    step = f"{(d_w['end'] - prev['end']) / 86400:.3f}" if prev else "-"
    print(f"{d_w['plan']:6} {fmt(d_w['start'])}  {fmt(d_w['end'])}  "
          f"{dt.datetime.fromtimestamp(d_w['end'], dt.timezone.utc):%a %H:%M}     {step:>22}  {d_w['peak']:6.0f}  "
          f"{fmt(first):>33}  {fmt(first + 7 * 86400) if first else '-':>21}")
    prev = d_w

print("\n7-day 'no window' runs (2 or more server readings in a row with no 7-day reset time):")
l_api = [d_r for d_r in l_readings if d_r["source"] == "claude_api"]
run, n_blips = None, 0
for d_r in l_api + [None]:
    if d_r is not None and d_r["end"]["7d"] is None:
        run = run or dict(first=d_r["ts"], n=0)
        run["n"] += 1
    elif run and run["n"] < 2:
        n_blips += 1  # one reading alone without a reset time, inside a running window: a server blip
        run = None
    elif run:
        end = d_r["ts"] if d_r else run["first"]
        print(f"  {fmt(run['first'])} -> {fmt(end)}  {(end - run['first']) / 3600:6.2f} h, {run['n']} readings; "
              f"first activity after its start: {fmt(first_active(run['first']))}")
        run = None

print(f"  and {n_blips} single readings without one (server blips)")

# --- a 7-day reset across a running 5-hour window ---------------------------------------------------------------
# Server readings only (a session's copy can be stale): the last one before each reached 7-day end with a 5-hour
# window running across it, and the first one after.
print("\n=== a running 5-hour window across a 7-day reset (server readings) ===")
l_api_ts = [d_r["ts"] for d_r in l_api]
for d_w in l_w7:
    if d_w["peak"] <= 0 or d_w["end"] > l_api_ts[-1] or d_w["cut"]:
        continue  # never used, not reached yet, or cut by a plan change
    i = bisect.bisect_left(l_api_ts, d_w["end"])
    l_before = [d_r for d_r in l_api[max(0, i - 20):i] if d_r["ts"] >= d_w["end"] - 2400]
    l_after = [d_r for d_r in l_api[i:i + 20] if d_r["ts"] <= d_w["end"] + 2400]
    b = next((d_r for d_r in reversed(l_before) if d_r["end"]["5h"] and d_r["end"]["5h"] > d_w["end"] and d_r["pct"]["5h"]), None)
    if not b or not l_after:
        continue
    a = l_after[0]
    print(f"  7-day end {fmt(d_w['end'])}: before {fmt(b['ts'])} 5h {b['pct']['5h']:.0f}% ending {fmt(b['end']['5h'])}, "
          f"7d {b['pct']['7d']:.0f}% | after {fmt(a['ts'])} 5h {a['pct']['5h']:.0f}% ending {fmt(a['end']['5h'])}, "
          f"7d {a['pct']['7d']:.0f}%")

print("\nplans: " + ", ".join(f"{name} from {fmt(start) if start else 'the start'}" for start, name in load.PLANS))
