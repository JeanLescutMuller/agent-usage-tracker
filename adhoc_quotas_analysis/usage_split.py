"""Q3. Where do the $ of a window or a week go: which project, which kind of use, which machine, which model?

Read-only, no network:
  python3 adhoc_quotas_analysis/usage_split.py [--span 7d|5h|day] [--by project|kind|machine|model]
                                               [--since YYYY-MM-DD] [--top N]
Per period (a 7-day or 5-hour window rebuilt from the readings, or a UTC day): its peak percent, its $ and
tokens, then one row per group. Then the same groups over every period shown.

  project   the slot's folder reduced to its repo (load.project)
  kind      development (interactive), test run, scheduled run (load.kind)
  usd       Claude Code's own $ (Category 3); tokens Claude Code reported no $ for are counted in
            "tokens w/o $", never priced here (FOR_READERS.md rule 7)
A window's percent is the account's: it is never split per group (FOR_READERS.md rule 5), only the $ are.
"""
import argparse
import collections
import datetime as dt

import load
from load import fmt

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("--span", choices=("7d", "5h", "day"), default="7d")
p.add_argument("--by", choices=("project", "kind", "machine", "model"), default="project")
p.add_argument("--since", help="UTC date, YYYY-MM-DD: the periods that end after it, whole")
p.add_argument("--top", type=int, default=8, help="groups shown per period (default 8), the rest summed")
args = p.parse_args()

since = load.epoch(args.since + "T00:00:00Z") if args.since else 0
group = {"project": lambda d_s: load.project(d_s["folder"]),
         "kind": lambda d_s: load.kind(d_s["folder"], d_s["entrypoint"]),
         "machine": lambda d_s: d_s["machine"],
         "model": lambda d_s: d_s["model"]}[args.by]
l_slots = load.slots()

# The periods: [start, end) with a label and the account's peak percent (None for a day).
if args.span == "day":
    l_days = sorted({d_s["ts"] // 86400 * 86400 for d_s in l_slots if d_s["ts"] >= since})
    l_periods = [dict(start=t, end=t + 86400, plan=load.plan_at(t), peak=None) for t in l_days]
else:
    l_periods = [d_w for d_w in load.windows(load.readings(), args.span) if d_w["end"] > since]
first = l_periods[0]["start"] if l_periods else float("inf")   # slots before it are not counted as outside


def add(d_sum: dict, d_s: dict) -> None:
    d_sum["usd"] += d_s["usd"] or 0
    d_sum["tokens"] += d_s["tokens"]
    d_sum["unpriced"] += d_s["tokens_without_usd"]
    d_sum["requests"] += d_s["requests"]


def row(name: str, d_sum: dict, total_usd: float) -> str:
    share = f"{100 * d_sum['usd'] / total_usd:5.1f}%" if total_usd else "     -"
    return (f"  {name[:34]:34} {d_sum['usd']:9.2f} {share}  {d_sum['tokens'] / 1e6:9.1f}  "
            f"{d_sum['unpriced'] / 1e6:11.1f}  {d_sum['requests']:8}")


header = f"  {args.by:34} {'$':>9} {'of $':>6}  {'tokens M':>9}  {'w/o $ M':>11}  {'requests':>8}"
d_all = collections.defaultdict(collections.Counter)
i, n_out = 0, 0
for d_p in l_periods:
    d_sum = collections.defaultdict(collections.Counter)
    while i < len(l_slots) and l_slots[i]["ts"] + 150 < d_p["start"]:
        n_out += l_slots[i]["ts"] + 150 >= first
        i += 1
    while i < len(l_slots) and l_slots[i]["ts"] + 150 < d_p["end"]:
        add(d_sum[group(l_slots[i])], l_slots[i])
        i += 1
    if not d_sum:
        continue
    total = collections.Counter()
    for name, d_g in d_sum.items():
        add(total, dict(usd=d_g["usd"], tokens=d_g["tokens"], tokens_without_usd=d_g["unpriced"], requests=d_g["requests"]))
        for k, v in d_g.items():
            d_all[name][k] += v
    label = (dt.datetime.fromtimestamp(d_p["start"], dt.timezone.utc).strftime("%Y-%m-%d") if args.span == "day"
             else f"{fmt(d_p['start'])} -> {fmt(d_p['end'])}, peak {d_p['peak']:.0f}%")
    print(f"\n{d_p['plan']} {label}: ${total['usd']:.2f}, {total['tokens'] / 1e6:.1f} M tokens "
          f"({100 * total['unpriced'] / total['tokens'] if total['tokens'] else 0:.0f}% without $)")
    print(header)
    l_g = sorted(d_sum.items(), key=lambda kv: (-kv[1]["usd"], -kv[1]["tokens"]))
    for name, d_g in l_g[:args.top]:
        print(row(name, d_g, total["usd"]))
    if len(l_g) > args.top:
        rest = collections.Counter()
        for _, d_g in l_g[args.top:]:
            rest.update(d_g)
        print(row(f"({len(l_g) - args.top} others)", rest, total["usd"]))
n_out += len(l_slots) - i

print(f"\n=== every period above, by {args.by} ===")
print(header)
grand = sum((d_g["usd"] for d_g in d_all.values()), 0.0)
for name, d_g in sorted(d_all.items(), key=lambda kv: (-kv[1]["usd"], -kv[1]["tokens"])):
    print(row(name, d_g, grand))
if n_out:
    print(f"\n{n_out} slots fall in no period above (outside every {args.span} window)")
