#!/usr/bin/env python3
"""Do consecutive 5-hour / 7-day quota windows chain back-to-back, or can idle gaps sit between them?

Run by hand (read-only, no network): `python3 window_gaps.py`. Prints the tables that
CONCLUSIONS.md's numbers come from - re-run it to refresh them.

Method:
  1. Rebuild every window from the two quota-log writers. A window is one distinct `resets_at`
     (snapped to a 10-min grid to absorb the API's sub-second jitter); start = end - span.
  2. Gap between consecutive windows = next.start - prev.end. Chained windows give 0, idle gaps > 0.
  3. Cross-check each gap against local Claude Code transcripts (message timestamps) and against
     server-side "no window" readings (poll rows with `resets_at: null`).
"""
import bisect
import datetime as dt
import glob
import json
import os
import statistics

from account_rows import account_lines

TRANSCRIPTS = os.path.expanduser('~/.claude/projects/**/*.jsonl')
GRID_S = 600           # 5h/7d `resets_at` is snapped to 10 min (5h) / 1 h (7d); 10 min is the common divisor
GRID_NOISE_H = 1 / 3   # gaps up to 20 min are indistinguishable from grid rounding, not counted as idle


def fmt(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%m-%d %H:%M')


def to_epoch(iso):
    return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp()


# --- 1. load readings -------------------------------------------------------------------------
# One tuple per usable reading: (ts, source, end5, pct5, end7, pct7). `end*` is None when the
# source reports "no window". Only *poll* rows carry a server-side null; a statusline push row
# with a null window just means that session's client-side copy expired, so those rows are kept
# for window discovery but never counted as evidence of an idle gap.
l_readings = []
for line in account_lines('claude'):
    d_row = json.loads(line)
    if d_row.get('source') == 'claude_statusline':
        l_readings.append((d_row['ts'], 'push',
                           float(d_row['five_hour_resets_at']) if d_row['five_hour_resets_at'] else None, d_row['five_hour_pct'],
                           float(d_row['seven_day_resets_at']) if d_row['seven_day_resets_at'] else None, d_row['seven_day_pct']))
    elif d_row.get('api') and d_row['api'].get('five_hour') and d_row['api'].get('seven_day'):
        d_five, d_seven = d_row['api']['five_hour'], d_row['api']['seven_day']
        l_readings.append((d_row['ts'], 'poll',
                           to_epoch(d_five['resets_at']) if d_five['resets_at'] else None, d_five['utilization'],
                           to_epoch(d_seven['resets_at']) if d_seven['resets_at'] else None, d_seven['utilization']))
l_readings.sort(key=lambda t: (t[0], t[1]))

# Local message timestamps. `<synthetic>` rows are Claude Code's own error stubs (e.g. "organization
# has disabled subscription access"): the request was rejected, so it neither consumes quota nor opens a window.
l_events = []
for path in glob.glob(TRANSCRIPTS, recursive=True):
    for line in open(path, errors='replace'):
        if '"usage"' not in line:
            continue
        try:
            d_ev = json.loads(line)
        except ValueError:
            continue
        d_msg = d_ev.get('message') or {}
        if d_ev.get('type') == 'assistant' and d_msg.get('usage') and d_ev.get('timestamp') and d_msg.get('model') != '<synthetic>':
            l_events.append(to_epoch(d_ev['timestamp']))
l_events.sort()


def count_between(l_sorted, lo, hi):
    return bisect.bisect_left(l_sorted, hi) - bisect.bisect_left(l_sorted, lo)


def build_windows(i_end, i_pct, span_s):
    """Cluster readings by (snapped) resets_at into windows, oldest first."""
    d_win = {}
    for r in l_readings:
        if r[i_end] is None:
            continue
        end = round(r[i_end] / GRID_S) * GRID_S
        d_w = d_win.setdefault(end, {'end': end, 'start': end - span_s, 'peak': 0})
        d_w['peak'] = max(d_w['peak'], r[i_pct])
    return sorted(d_win.values(), key=lambda d_w: d_w['end'])


# --- 2. 5-hour windows ------------------------------------------------------------------------
l_w5 = build_windows(2, 3, 5 * 3600)
l_null5_ts = [r[0] for r in l_readings if r[1] == 'poll' and r[2] is None]  # server-side "no 5h window"
l_all_ts = [r[0] for r in l_readings]

print(f'=== 5-hour windows: {len(l_w5)} distinct, {fmt(l_w5[0]["start"])} -> {fmt(l_w5[-1]["end"])} UTC ===')
print('prev_end     next_start   gap_h  local_msgs_in_gap  poll_nulls_in_gap  first_msg_after_prev_end  next_start-first_msg(min)')
l_gaps = []
for d_a, d_b in zip(l_w5, l_w5[1:]):
    gap_h = (d_b['start'] - d_a['end']) / 3600
    i = bisect.bisect_left(l_events, d_a['end'])
    first_msg = l_events[i] if i < len(l_events) else None
    n_msgs = count_between(l_events, d_a['end'] + 60, d_b['start'] - 60)
    n_null = count_between(l_null5_ts, d_a['end'], d_b['start'])
    l_gaps.append((gap_h, n_msgs, n_null, count_between(l_all_ts, d_a['end'], d_b['start']), (d_b['start'] - first_msg) / 60))
    print(f'{fmt(d_a["end"])}  {fmt(d_b["start"])}  {gap_h:6.2f}  {n_msgs:17}  {n_null:17}  {fmt(first_msg):>24}  {l_gaps[-1][4]:9.1f}')

l_idle = [g for g in l_gaps if g[0] > GRID_NOISE_H]
print(f'\npairs={len(l_gaps)}  back-to-back(gap 0)={sum(abs(g[0]) < 0.01 for g in l_gaps)}  '
      f'within grid noise(<=20min)={sum(0.01 <= g[0] <= GRID_NOISE_H for g in l_gaps)}  '
      f'idle gaps(>20min)={len(l_idle)}  overlaps={sum(g[0] < -0.01 for g in l_gaps)}')
print(f'idle gaps: min={min(g[0] for g in l_idle):.2f}h median={statistics.median(g[0] for g in l_idle):.2f}h max={max(g[0] for g in l_idle):.2f}h')
print(f'idle gaps with >=1 server-side null reading: {sum(g[2] > 0 for g in l_idle)}/{len(l_idle)}; '
      f'with zero readings of any kind: {sum(g[3] == 0 for g in l_idle)}/{len(l_idle)}; '
      f'containing >=1 local message: {sum(g[1] > 0 for g in l_idle)}/{len(l_idle)}')
print(f'next_start - first local message after prev_end, all pairs: {min(g[4] for g in l_gaps):.1f} .. {max(g[4] for g in l_gaps):.1f} min')
l_start = [d_w['start'] for d_w in l_w5]
n_orphan = sum(1 for e in l_events if e >= l_start[0] and e > l_w5[bisect.bisect_right(l_start, e) - 1]['end'] + 60)
print(f'local messages since first window: {sum(e >= l_start[0] for e in l_events)}, outside every known 5h window: {n_orphan}')
covered_h = sum(d_w['end'] - d_w['start'] for d_w in l_w5) / 3600
span_h = (l_w5[-1]['end'] - l_w5[0]['start']) / 3600
print(f'time covered by a 5h window: {covered_h:.0f}h of {span_h:.0f}h ({100 * covered_h / span_h:.0f}%)')

# --- 3. 7-day windows -------------------------------------------------------------------------
l_w7 = build_windows(4, 5, 7 * 86400)
print(f'\n=== 7-day windows: {len(l_w7)} distinct ===')
print('start(=end-7d)  end          end-prev_end(d)  end weekday/time   first local msg after prev_end  activity-triggered prediction  observed-predicted(h)')
for d_a, d_b in zip([None] + l_w7, l_w7):
    step = '-' if d_a is None else f'{(d_b["end"] - d_a["end"]) / 86400:.4f}'
    if d_a is None:
        first_msg, pred = None, None
    else:
        i = bisect.bisect_left(l_events, d_a['end'])
        first_msg = l_events[i]
        pred = first_msg + 7 * 86400  # if a 7d window opened at the first message, like the 5h one does
    print(f'{fmt(d_b["start"])}     {fmt(d_b["end"])}   {step:>15}  {dt.datetime.fromtimestamp(d_b["end"], dt.timezone.utc):%a %H:%M}         '
          f'{fmt(first_msg) if first_msg else "-":>30}  {fmt(pred) if pred else "-":>29}  {(d_b["end"] - pred) / 3600 if pred else float("nan"):>10.1f}')

# Server-side "no 7d window" runs: contiguous poll readings whose seven_day.resets_at is null.
print('\n7-day "no window" runs (poll readings with seven_day.resets_at null):')
l_runs, run = [], None
for r in (r for r in l_readings if r[1] == 'poll'):
    if r[4] is None:
        run = run or [r[0], r[0], 0]
        run[1], run[2] = r[0], run[2] + 1
    elif run:
        l_runs.append(run + [r[0]])
        run = None
for first, last, n, nxt in l_runs:
    i = bisect.bisect_left(l_events, first)
    print(f'  {fmt(first)} -> {fmt(nxt)}  ({(nxt - first) / 3600:.2f}h, {n} null readings)  first local msg after run start: {fmt(l_events[i])}')
