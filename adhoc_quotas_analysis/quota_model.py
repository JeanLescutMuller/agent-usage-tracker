#!/usr/bin/env python3
"""Unit conversions between quota %, tokens and API-equivalent USD, for Claude Code and Codex.

Run by hand (read-only, no network): `python3 quota_model.py > /tmp/quota_model.log`. Prints every
table CONCLUSIONS.md quotes - re-run it to refresh them. Window *timing* (gaps, lattice) is
`window_gaps.py`'s job; this script is about *how much* a window holds.

Method, per provider and per window kind (5h / 7d):
  1. Identify every window by its reset time; its start is `end - span` (see CONCLUSIONS.md for why
     that is exact for Claude 5h and Codex 5h/7d, and lattice-exact for Claude 7d).
  2. For every quota reading inside the window, sum the API-equivalent cost of every local message
     sent between window start and the reading (cumulative, exact start known, so no offset).
  3. Fit pct = 100 * cost / B through the origin per window -> one budget B per window.
     Only readings >= MIN_PCT are used (integer-quantised % is too coarse below that).
"""
import bisect
import collections
import datetime as dt
import glob
import json
import os
import statistics

import numpy as np
from scipy.optimize import nnls

from account_rows import account_lines

MIN_PCT = 5
PROMO_END = dt.datetime(2026, 9, 1, 6, 59, tzinfo=dt.timezone.utc).timestamp()  # Claude +50% weekly promo end

# $ / MTok. Claude: platform.claude.com pricing (see analysis.ipynb). Codex: OpenAI standard list price for
# gpt-5.6-sol ($5/$30, cached 0.1x, cache write 1.25x; $4/$20 is a temporary promo) - an *assumed* reference.
D_CLAUDE_PRICE = {'claude-opus-5': (5, 25), 'claude-sonnet-5': (2, 10), 'claude-haiku-4-5-20251001': (1, 5)}
CODEX_PRICE = (5, 30)


def fmt(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%m-%d %H:%M')


def to_epoch(iso):
    return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp()


def pct_str(l_vals):
    """median [p10-p90] of a list"""
    a = np.array(l_vals, float)
    return f'{np.median(a):7.2f} [{np.percentile(a, 10):.2f}-{np.percentile(a, 90):.2f}]'


def envelope(l_readings):
    """Per window, keep only the first reading of each new maximum %. Within one window the true % never
    decreases, so anything at or below the running max is stale (an idle session re-pushing an old
    reading, or a duplicate) - this is what makes the dense statusline-push data usable."""
    d_win = collections.defaultdict(list)
    for ts, pct, end in l_readings:
        d_win[round(end / 600) * 600].append((ts, pct, end))
    l_out = []
    for l_w in d_win.values():
        mx = -1
        for ts, pct, end in sorted(l_w):
            if pct > mx:
                l_out.append((ts, pct, end))
                mx = pct
    return sorted(l_out)


def fit_budget(l_pairs):
    """l_pairs = [(cum_x, pct)] -> (B, r2) of pct = 100*x/B through the origin."""
    a = np.array([p for p in l_pairs if p[1] >= MIN_PCT], float)
    if len(a) < 3 or a[:, 0].max() <= 0:
        return None, None
    x, y = a[:, 0], a[:, 1]
    k = (x @ y) / (x @ x)
    r2 = 1 - ((y - k * x) ** 2).sum() / ((y - y.mean()) ** 2).sum() if y.std() > 0 else float('nan')
    return 100 / k, r2


def cum_series(a_ts, a_val):
    """prefix sums for O(log n) 'total value of events in (lo, hi]' queries"""
    a_val = np.asarray(a_val, float)
    a_cum = np.concatenate([np.zeros((1,) + a_val.shape[1:]), np.cumsum(a_val, axis=0)])
    return lambda lo, hi: a_cum[bisect.bisect_right(a_ts, hi)] - a_cum[bisect.bisect_right(a_ts, lo)]


def window_budgets(name, l_readings, span, total, d_extra=None):
    """Cluster readings by reset time, fit one budget per window, print table, return rows."""
    d_win = collections.defaultdict(list)
    for ts, pct, end in l_readings:
        d_win[round(end / 600) * 600].append((ts, pct))
    l_rows = []
    print(f'\n--- {name}: per-window budget (B = API-equivalent $ at 100%) ---')
    print('start        end           readings  peak%   cost_at_peak$   B$      r2    extra')
    for end in sorted(d_win):
        start = end - span
        l_pairs = [(total(start - 60, ts), pct) for ts, pct in d_win[end] if start - 60 <= ts <= end + 60]
        b, r2 = fit_budget(l_pairs)
        if b is None:
            continue
        peak = max(p for _, p in l_pairs)
        extra = d_extra(start, end) if d_extra else ''
        l_rows.append(dict(start=start, end=end, b=b, r2=r2, peak=peak, cost=total(start - 60, end)))
        print(f'{fmt(start)}  {fmt(end)}  {len(l_pairs):8}  {peak:5.0f}  {total(start - 60, end):12.2f}  {b:7.2f}  {r2:5.2f}  {extra}')
    return l_rows


# ============================================================================================
# CLAUDE
# ============================================================================================
# --- events: dedup by message id (Claude Code writes one transcript line per content block, all
# carrying the same usage; keep the last one, which has the final output_tokens count).
d_msg = {}
for path in glob.glob(os.path.expanduser('~/.claude/projects/**/*.jsonl'), recursive=True):
    for line in open(path, errors='replace'):
        if '"usage"' not in line:
            continue
        try:
            d_ev = json.loads(line)
        except ValueError:
            continue
        d_m = d_ev.get('message') or {}
        if d_ev.get('type') != 'assistant' or not d_m.get('usage') or d_m.get('model') not in D_CLAUDE_PRICE:
            continue
        d_msg[d_m.get('id') or (path, d_ev.get('uuid'))] = (to_epoch(d_ev['timestamp']), d_m['model'], d_m['usage'])

# per event: ts, then cost split into [input, output, cache_read, cache_write_5m, cache_write_1h] $ and
# the same split in raw tokens, plus per-model $ (opus, sonnet, haiku)
l_ev = []
for ts, model, d_u in sorted(d_msg.values(), key=lambda t: t[0]):
    pin, pout = (p / 1e6 for p in D_CLAUDE_PRICE[model])
    d_cc = d_u.get('cache_creation') or {}
    w5, w1h = d_cc.get('ephemeral_5m_input_tokens', 0), d_cc.get('ephemeral_1h_input_tokens', 0)
    if not d_cc:  # older rows: aggregate only, assume 1h (96% of writes are 1h on this account)
        w1h = d_u.get('cache_creation_input_tokens', 0)
    l_tok = [d_u.get('input_tokens', 0), d_u.get('output_tokens', 0), d_u.get('cache_read_input_tokens', 0), w5, w1h]
    l_usd = [l_tok[0] * pin, l_tok[1] * pout, l_tok[2] * pin * 0.1, w5 * pin * 1.25, w1h * pin * 2]
    l_model = [sum(l_usd) if model == m else 0 for m in D_CLAUDE_PRICE]
    l_ev.append((ts, l_usd, l_tok, l_model))
a_ts = np.array([e[0] for e in l_ev])
a_usd = np.array([e[1] for e in l_ev])
a_tok = np.array([e[2] for e in l_ev], float)
a_model = np.array([e[3] for e in l_ev])
total_usd = cum_series(a_ts, a_usd.sum(1))
cat_usd = cum_series(a_ts, a_usd)
cat_tok = cum_series(a_ts, a_tok)
model_usd = cum_series(a_ts, a_model)
print(f'CLAUDE: {len(l_ev)} unique priced messages {fmt(a_ts[0])} -> {fmt(a_ts[-1])}, total ${a_usd.sum():,.0f}')

# --- readings (poll + statusline push), same normalisation as window_gaps.py
l_r5, l_r7 = [], []
for line in account_lines('claude'):
    d_row = json.loads(line)
    if d_row.get('source') == 'claude_statusline':
        ts = d_row['ts']
        if d_row['five_hour_resets_at']:
            l_r5.append((ts, d_row['five_hour_pct'], float(d_row['five_hour_resets_at'])))
        if d_row['seven_day_resets_at']:
            l_r7.append((ts, d_row['seven_day_pct'], float(d_row['seven_day_resets_at'])))
    elif d_row.get('api') and d_row['api'].get('five_hour') and d_row['api'].get('seven_day'):
        d_f, d_s = d_row['api']['five_hour'], d_row['api']['seven_day']
        if d_f['resets_at']:
            l_r5.append((d_row['ts'], d_f['utilization'], to_epoch(d_f['resets_at'])))
        if d_s['resets_at']:
            l_r7.append((d_row['ts'], d_s['utilization'], to_epoch(d_s['resets_at'])))

l_r5, l_r7 = envelope(l_r5), envelope(l_r7)


def opus_share(start, end):
    a = model_usd(start - 60, end)
    return f'opus {100 * a[0] / max(a.sum(), 1e-9):3.0f}%'


l_c5 = window_budgets('Claude 5h', l_r5, 5 * 3600, total_usd, opus_share)
l_c7 = window_budgets('Claude 7d', l_r7, 7 * 86400, total_usd, opus_share)

# The 08-31 -> 09-07 week straddles the promo end: fit the slope separately before/after it.
print('\n--- Claude 7d: pct-per-$ slope before vs after the promo end (09-01 06:59 UTC), within the straddling week ---')
end = round(next(e for _, _, e in l_r7 if fmt(e).startswith('09-07')) / 600) * 600
start = end - 7 * 86400
for label, lo, hi in (('before promo end', start, PROMO_END), ('after promo end', PROMO_END, end)):
    l_pts = [(ts, p) for ts, p, e in l_r7 if abs(e - end) < 900 and lo <= ts <= hi]
    if len(l_pts) >= 2:
        (t0, p0), (t1, p1) = l_pts[0], l_pts[-1]
        c = total_usd(t0, t1)
        print(f'  {label}: {fmt(t0)} -> {fmt(t1)}  d%={p1 - p0:.0f}  d$={c:.2f}  -> $ per 1% = {c / max(p1 - p0, 1e-9):.2f}')

# --- meter ratio, cost-model-free: d7% / d5% over the same interval, per 5h window
print('\n--- Claude meter ratio B7/B5 = d5% / d7% over each 5h window (no cost model) ---')


def p7_at(ts):
    """7d % reached by time ts in the 7d window that contains ts (envelope => last level reached)"""
    l_p = [p for t, p, e in l_r7 if t <= ts < e]
    return max(l_p) if l_p else None


for w in l_c5:
    # 7d% at the first and last reading of the 5h window (same 7d window required)
    l_in = sorted((ts, p) for ts, p, e in l_r5 if abs(round(e / 600) * 600 - w['end']) < 1)
    (t0, p0), (t1, p1) = l_in[0], l_in[-1]
    q0, q1 = p7_at(t0), p7_at(t1)
    if q0 is not None and q1 is not None and q1 > q0 and p1 - p0 >= 10:
        w['ratio'] = (p1 - p0) / (q1 - q0)
        print(f'  {fmt(w["start"])}  d5={p1 - p0:5.0f}  d7={q1 - q0:4.0f}  ratio={w["ratio"]:5.1f}  {"PROMO" if w["end"] < PROMO_END else ""}')

# --- token-type weights, window level: per 5h window with peak >= 30%, the $ (at list price) of each
# category spent up to the peak reading; NNLS of peak% on those. Weight 1.0 = charged exactly at list
# price relative to a single $-slope; the leave-one-out spread shows how stable each weight is.
l_x, l_y = [], []
for end in sorted({round(e / 600) * 600 for _, _, e in l_r5}):
    l_w = [(ts, p) for ts, p, e in l_r5 if round(e / 600) * 600 == end]
    ts, pct = max(l_w, key=lambda t: t[1])
    if pct >= 30:
        c = cat_usd(end - 5 * 3600 - 60, ts)
        l_x.append([c[1] + c[0], c[2], c[3] + c[4]])  # input is ~0% of $, folded into output
        l_y.append(pct)
a_x, a_y = np.array(l_x), np.array(l_y, float)
k_all = (a_x.sum(1) @ a_y) / (a_x.sum(1) ** 2).sum()
a_coef, _ = nnls(a_x, a_y)
a_loo = np.array([nnls(np.delete(a_x, i, 0), np.delete(a_y, i))[0] for i in range(len(a_y))]) / k_all
r2 = lambda pred: 1 - ((a_y - pred) ** 2).sum() / ((a_y - a_y.mean()) ** 2).sum()
print(f'\n--- Claude token-type weights, window level (NNLS, n={len(a_y)} windows): weight vs list price ---')
for i, name in enumerate(['input+output', 'cache_read', 'cache_write']):
    print(f'  {name:13} {a_coef[i] / k_all:5.2f}   leave-one-out range {a_loo[:, i].min():.2f}-{a_loo[:, i].max():.2f}   '
          f'=> $ at list per 1% of 5h: {1 / a_coef[i] if a_coef[i] else float("inf"):.3f}')
print(f'  R2 across windows: single $-slope {r2(k_all * a_x.sum(1)):.2f}, per-category weights {r2(a_x @ a_coef):.2f}')

# --- is the $ per 1% different at peak hours / weekends, and is there usage the local transcripts miss?
# Uses consecutive envelope steps (first time each new 5h level appeared), dense push era only.
print('\n--- Claude 5h: $ per 1% by time bucket (envelope steps since 09-13) ---')
d_b = collections.defaultdict(lambda: [0., 0.])
n_step = n_dark = 0
for end in sorted({round(e / 600) * 600 for _, _, e in l_r5}):
    l_w = sorted((ts, p) for ts, p, e in l_r5 if round(e / 600) * 600 == end)
    if end < to_epoch('2026-09-13T00:00:00Z'):
        continue
    for (t0, p0), (t1, p1) in zip(l_w, l_w[1:]):
        t = dt.datetime.fromtimestamp(t1, dt.timezone.utc)
        key = ('weekend' if t.weekday() >= 5 else 'weekday') + (' 12-18 UTC' if 12 <= t.hour < 18 else ' other hours')
        d_b[key][0] += total_usd(t0, t1)
        d_b[key][1] += p1 - p0
        n_step += 1
        n_dark += total_usd(t1 - 900, t1) == 0
for key, (usd, dp) in sorted(d_b.items()):
    print(f'  {key:22} d%={dp:5.0f}  $={usd:7.2f}  $ per 1% = {usd / dp:.3f}')
print(f'  5h % steps with zero local $ in the 15 min before: {n_dark} of {n_step}')

# --- where the $ goes, all events since polling started
m = a_ts >= to_epoch('2026-08-24T00:00:00Z')
s = a_usd[m].sum(0)
print('\n--- Claude $ share by category since 08-24: ' + ', '.join(f'{n} {100 * v / s.sum():.1f}%' for n, v in zip(['input', 'output', 'cache_read', 'cw5m', 'cw1h'], s)))
print('    token share: ' + ', '.join(f'{n} {100 * v / a_tok[m].sum():.1f}%' for n, v in zip(['input', 'output', 'cache_read', 'cw5m', 'cw1h'], a_tok[m].sum(0))))
s = a_model[m].sum(0)
print('    model $ share: ' + ', '.join(f'{n} {100 * v / s.sum():.1f}%' for n, v in zip(D_CLAUDE_PRICE, s)))

# --- organic spend profile (for forecasting): $ per UTC hour-of-day and per weekday, since 08-24
print('\n--- Claude organic spend profile since 08-24 (API-equivalent $) ---')
n_days = (a_ts[-1] - to_epoch('2026-08-24T00:00:00Z')) / 86400
a_h = np.zeros(24)
a_d = np.zeros(7)
for ts, usd in zip(a_ts[m], a_usd[m].sum(1)):
    t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    a_h[t.hour] += usd
    a_d[t.weekday()] += usd
print('  $/day avg over hour-of-day (UTC): ' + ' '.join(f'{h:02d}:{v / n_days:.1f}' for h, v in enumerate(a_h)))
print('  $/weekday avg: ' + ' '.join(f'{n}:{v / (n_days / 7):.0f}' for n, v in zip('Mon Tue Wed Thu Fri Sat Sun'.split(), a_d)))
l_daily = collections.Counter()
for ts, usd in zip(a_ts[m], a_usd[m].sum(1)):
    l_daily[fmt(ts)[:5]] += usd
print(f'  $/calendar day: median {statistics.median(l_daily.values()):.0f}, max {max(l_daily.values()):.0f}, days with usage {len(l_daily)} of {n_days:.0f}')

# ============================================================================================
# CODEX
# ============================================================================================
# token_count events carry both the per-turn token usage and the rate-limit snapshot *after* that turn.
# Dedup: the same token_count is often emitted twice in a row (same cumulative total) - skip repeats.
l_cx = []
for path in glob.glob(os.path.expanduser('~/.codex/sessions/**/*.jsonl'), recursive=True):
    model, last_total = None, None
    for line in open(path, errors='replace'):
        if '"turn_context"' in line:
            model = json.loads(line)['payload'].get('model')
        if '"token_count"' not in line:
            continue
        try:
            d_ev = json.loads(line)
            d_p = d_ev['payload']
        except ValueError:
            continue
        d_info, d_rl = d_p.get('info'), d_p.get('rate_limits')
        if d_p.get('type') != 'token_count' or not d_info or not d_rl or not d_rl.get('primary'):
            continue
        tot = d_info['total_token_usage']['total_tokens']
        if tot == last_total:
            continue
        last_total = tot
        d_u = d_info['last_token_usage']
        # [uncached input, cached input, output (incl. reasoning)] - input_tokens includes cached
        l_tok = [d_u['input_tokens'] - d_u['cached_input_tokens'], d_u['cached_input_tokens'], d_u['output_tokens']]
        l_cx.append((to_epoch(d_ev['timestamp']), l_tok, model,
                     d_rl['primary']['used_percent'], d_rl['primary']['resets_at'],
                     d_rl['secondary']['used_percent'], d_rl['secondary']['resets_at']))
l_cx.sort(key=lambda t: t[0])
a_cts = np.array([e[0] for e in l_cx])
a_ctok = np.array([e[1] for e in l_cx], float)
pin, pout = (p / 1e6 for p in CODEX_PRICE)
a_cusd = a_ctok * [pin, pin * 0.1, pout]
cx_usd = cum_series(a_cts, a_cusd.sum(1))
cx_cat = cum_series(a_cts, a_ctok)
print(f'\nCODEX: {len(l_cx)} unique token_count events {fmt(a_cts[0])} -> {fmt(a_cts[-1])}; models: '
      + ', '.join(f'{k} {v}' for k, v in collections.Counter(e[2] for e in l_cx).most_common()))
print('  token share: ' + ', '.join(f'{n} {100 * v / a_ctok.sum():.1f}%' for n, v in zip(['uncached_in', 'cached_in', 'output'], a_ctok.sum(0))))
print('  $ share (gpt-5.6-sol list): ' + ', '.join(f'{n} {100 * v / a_cusd.sum():.1f}%' for n, v in zip(['uncached_in', 'cached_in', 'output'], a_cusd.sum(0))))

# Codex readings: session snapshots + polls. A poll at 0% whose resetsAt == ts + span is the idle
# "rolling countdown" placeholder, not a window - dropped (see CONCLUSIONS.md).
l_x5, l_x7 = [], []
for ts, _, _, p5, e5, p7, e7 in l_cx:
    l_x5.append((ts, p5, e5))
    l_x7.append((ts, p7, e7))
for line in account_lines('codex'):
    d_row = json.loads(line)
    if not d_row.get('codex_rate_limits'):
        continue
    d_rl = d_row['codex_rate_limits']['rateLimits']
    for l_out, d_w, span in ((l_x5, d_rl['primary'], 18000), (l_x7, d_rl['secondary'], 604800)):
        if not (d_w['usedPercent'] == 0 and abs(d_w['resetsAt'] - d_row['ts'] - span) < 300):
            l_out.append((d_row['ts'], d_w['usedPercent'], d_w['resetsAt']))

l_x5, l_x7 = envelope(l_x5), envelope(l_x7)
l_x5w = window_budgets('Codex 5h', l_x5, 18000, cx_usd)
l_x7w = window_budgets('Codex 7d', l_x7, 604800, cx_usd)

# Codex token-type weights, pooled NNLS on raw token counts of the 5h meter
l_x, l_y = [], []
for ts, pct, end in l_x5:
    if pct >= MIN_PCT:
        l_x.append(cx_cat(end - 18000 - 60, ts))
        l_y.append(pct)
a_x, a_y = np.array(l_x), np.array(l_y, float)
a_coef, _ = nnls(a_x, a_y)
print(f'\n--- Codex token-type weights (NNLS on raw tokens, n={len(a_y)}), relative to uncached input = 1 ---')
for name, c in zip(['uncached_in', 'cached_in', 'output'], a_coef):
    print(f'  {name:12} {c / a_coef[0] if a_coef[0] else float("nan"):7.3f}   (list price ratio: {dict(uncached_in=1, cached_in=0.1, output=6)[name]})   % per MTok {c * 1e6:.2f}')

# Codex meter ratio from session snapshots (same event gives both meters)
print('\n--- Codex meter ratio B7/B5 = d5% / d7% over each 5h window ---')
d_x = collections.defaultdict(list)
for ts, _, _, p5, e5, p7, e7 in l_cx:
    d_x[(round(e5 / 600), round(e7 / 600))].append((ts, p5, p7))
for k, l_pts in sorted(d_x.items()):
    (t0, a5, a7), (t1, b5, b7) = l_pts[0], l_pts[-1]
    if b5 - a5 >= 10 and b7 > a7:
        print(f'  {fmt(t0)} -> {fmt(t1)}  d5={b5 - a5:4.0f} d7={b7 - a7:4.0f}  ratio={(b5 - a5) / (b7 - a7):5.1f}')

# ============================================================================================
# SUMMARY
# ============================================================================================
print('\n=== SUMMARY: budget per window, median [p10-p90] over windows with peak >= 30% ===')
for name, l_w in (('Claude 5h', [w for w in l_c5 if w['end'] > PROMO_END - 30 * 86400]),
                  ('Claude 7d', l_c7), ('Codex 5h', l_x5w), ('Codex 7d', l_x7w)):
    l_b = [w['b'] for w in l_w if w['peak'] >= 30]
    if l_b:
        print(f'  {name}: B = ${pct_str(l_b)}  (n={len(l_b)})  -> $ per 1% = {statistics.median(l_b) / 100:.3f}')

# ============================================================================================
# CODEX window timing (the Claude equivalent lives in window_gaps.py)
# ============================================================================================
# Windows = envelope clusters with usage > 0 (0% readings can be the idle rolling-countdown placeholder).
print('\n=== Codex window timing ===')
n_idle_polls = sum(1 for line in account_lines('codex')
                   if (d_row := json.loads(line)).get('codex_rate_limits')
                   and abs(d_row['codex_rate_limits']['rateLimits']['primary']['resetsAt'] - d_row['ts'] - 18000) < 300
                   and d_row['codex_rate_limits']['rateLimits']['primary']['usedPercent'] == 0)
print(f'  idle rolling-countdown poll readings (0%, 5h resetsAt == now+5h): {n_idle_polls}')
for name, l_r, span in (('5h', l_x5, 18000), ('7d', l_x7, 604800)):
    d_w = collections.defaultdict(list)
    for ts, pct, end in l_r:
        if pct > 0:
            d_w[round(end / 600) * 600].append((ts, pct))
    l_end = sorted(d_w)
    l_gap = [(b - span - a) / 3600 for a, b in zip(l_end, l_end[1:])]
    print(f'  {name}: {len(l_end)} windows with usage; consecutive pairs {len(l_gap)}: idle gap >20min {sum(g > 1 / 3 for g in l_gap)}, '
          f'back-to-back {sum(abs(g) <= 1 / 3 for g in l_gap)}, overlap (early reset) {sum(g < -1 / 3 for g in l_gap)}'
          + (f'; idle gap median {statistics.median([g for g in l_gap if g > 1 / 3]):.1f}h max {max(l_gap):.1f}h' if any(g > 1 / 3 for g in l_gap) else ''))
    for a, b, g in zip(l_end, l_end[1:], l_gap):
        if g < -1 / 3:
            print(f'    early reset: window ending {fmt(a)} (peak {max(p for _, p in d_w[a]):.0f}%) replaced by one starting {fmt(b - span)}, '
                  f'first seen {fmt(min(t for t, _ in d_w[b]))}')

# ============================================================================================
# Does a 7-day reset also reset the 5-hour window? For every weekly reset with a 5h window active
# across it, compare the 5h reading just before vs just after. Claude: poll rows only (push rows can
# be stale); Codex: polls + session snapshots. Early (server-side) Codex resets are listed above.
# ============================================================================================
print('\n=== 5h window across a natural 7d reset ===')
l_claude = []
for line in account_lines('claude'):
    d_row = json.loads(line)
    if d_row.get('source') != 'claude_statusline' and d_row.get('api') and d_row['api'].get('five_hour'):
        d_f, d_s = d_row['api']['five_hour'], d_row['api']['seven_day']
        l_claude.append((d_row['ts'], d_f['utilization'], to_epoch(d_f['resets_at']) if d_f['resets_at'] else 0,
                         d_s['utilization'], to_epoch(d_s['resets_at']) if d_s['resets_at'] else 0))
l_codex = [(e[0], e[3], e[4], e[5], e[6]) for e in l_cx]
for line in account_lines('codex'):
    d_row = json.loads(line)
    if d_row.get('codex_rate_limits'):
        d_rl = d_row['codex_rate_limits']['rateLimits']
        l_codex.append((d_row['ts'], d_rl['primary']['usedPercent'], d_rl['primary']['resetsAt'],
                        d_rl['secondary']['usedPercent'], d_rl['secondary']['resetsAt']))
for name, l_rows in (('Claude', sorted(l_claude)), ('Codex', sorted(l_codex))):
    # natural 7d reset instants = pinned 7d ends that were actually reached (usage > 0 in that window)
    for end7 in sorted({round(r[4] / 600) * 600 for r in l_rows if r[4] and r[3] > 0}):
        l_before = [r for r in l_rows if end7 - 2400 <= r[0] < end7 - 60 and r[2] > r[0] and r[1] > 0]
        l_after = [r for r in l_rows if end7 + 60 < r[0] <= end7 + 2400]
        if not l_before or not l_after or l_after[0][0] > l_before[-1][2]:
            continue  # no 5h window active across this reset
        b, a = l_before[-1], l_after[0]
        print(f'  {name} 7d reset {fmt(end7)}: before {fmt(b[0])} 5h={b[1]:.0f}% end5={fmt(b[2])} 7d={b[3]:.0f}% | '
              f'after {fmt(a[0])} 5h={a[1]:.0f}% end5={fmt(a[2]) if a[2] else None} 7d={a[3]:.0f}% end7={fmt(a[4]) if a[4] else None}')
