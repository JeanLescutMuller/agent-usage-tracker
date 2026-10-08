"""Sanity check: do local files give exact tokens and USD for Claude Code and Codex?

Three independent checks, all read-only, no network:
  1. Claude transcripts vs Claude Code's own per-request telemetry (sessions_usages.db): same tokens? does our
     price table reproduce its `cost_usd`? how much USD never reaches a transcript?
  2. Claude transcripts, summed per month x model, vs ccusage's monthly report (reference copied below).
  3. Codex session files, summed per month x model, vs the same ccusage report (tokens only).

Run by hand: `python3 adhoc_quotas_analysis/verify_token_costs.py`. Months still running when the reference was
taken (2026-10) keep growing, so a difference there is expected. Results are recorded in USAGE_DATA_SOURCES.md §3.4.
"""
import collections, glob, json, os, sqlite3, statistics

DATA_DIR = os.path.expanduser('~/opt/agent-usage-tracker/data')
CLAUDE_GLOB = os.path.expanduser('~/.claude/projects/**/*.jsonl')
CODEX_GLOBS = [os.path.expanduser(p) for p in ('~/.codex/sessions/**/*.jsonl', '~/.codex/archived_sessions/**/*.jsonl')]

# List prices, $ per million tokens: (input, output, cache-read ratio to input). Cache writes are 1.25x input
# (5 min) and 2x input (1 h) for every model. Each row reproduces Claude Code's own cost_usd exactly (check 1),
# except claude-sonnet-5, which had no telemetry and is confirmed only through ccusage (check 2).
D_PRICES = {
    'claude-opus-5': (5, 25, .1),
    'claude-opus-5-5': (4, 20, .05),
    'claude-sonnet-5': (2, 10, .1),
    'claude-sonnet-5-5': (2, 10, .1),
    'claude-haiku-4-5-20251001': (1, 5, .1),
}

# `npx ccusage@20.0.26 monthly --mode calculate --breakdown`, run by the user on 2026-10-07 (UTC months).
# Claude: (input, output, cache write, cache read, USD). Codex: (input excluding cached, output, cached input).
D_CCUSAGE_CLAUDE = {
    ('2026-07', 'claude-sonnet-5'): (1586, 639326, 3029681, 108241749, 38.38),
    ('2026-08', 'claude-sonnet-5'): (18473, 8788360, 44711624, 2378775452, 739.02),
    ('2026-08', 'claude-opus-5'): (690, 523373, 1957778, 51110860, 58.14),
    ('2026-08', 'claude-haiku-4-5-20251001'): (1132, 17482, 342923, 1538963, 0.68),
    ('2026-09', 'claude-sonnet-5'): (7857, 3378669, 15142439, 712541712, 230.32),
    ('2026-09', 'claude-opus-5'): (962, 822640, 3504852, 112634993, 110.71),
    ('2026-09', 'claude-opus-5-5'): (668, 324890, 962731, 76786461, 29.56),
    ('2026-09', 'claude-haiku-4-5-20251001'): (120, 823, 77788, 235436, 0.18),
    ('2026-10', 'claude-opus-5'): (2898, 1783170, 6586862, 366836603, 293.88),
    ('2026-10', 'claude-opus-5-5'): (2936, 1629243, 8259834, 351832133, 168.04),
    ('2026-10', 'claude-sonnet-5-5'): (252, 75057, 689315, 1461153, 3.80),
    ('2026-10', 'claude-haiku-4-5-20251001'): (29116, 13506, 152169, 485286, 0.45),
}
# ccusage reports Codex's `codex-auto-review` model as gpt-5.6-luna; the keys here use the name in the files.
D_CCUSAGE_CODEX = {
    ('2026-08', 'gpt-5.6-sol'): (4763983, 423256, 161737728),
    ('2026-08', 'codex-auto-review'): (1251695, 14326, 10053120),
    ('2026-09', 'gpt-5.6-sol'): (983608, 118152, 64645760),
    ('2026-09', 'codex-auto-review'): (309347, 3804, 1722368),
    ('2026-10', 'gpt-5.6-sol'): (645749, 114975, 27976064),
    ('2026-10', 'codex-auto-review'): (106068, 2780, 499968),
    ('2026-10', 'gpt-6-luna'): (71138, 6357, 555264),
}


def usd(model, inp, out, cache_read, cache_write_5m, cache_write_1h):
    pin, pout, read_ratio = D_PRICES[model]
    return (inp * pin + out * pout + cache_read * pin * read_ratio + cache_write_5m * pin * 1.25 + cache_write_1h * pin * 2) / 1e6


def diff(ours, ref):
    return '=' if ours == ref else f'{100 * (ours - ref) / ref:+.2f}%' if ref else f'+{ours}'


# --- Claude transcripts: one entry per API request ---
# A streamed reply is written as several assistant lines sharing one requestId, and output_tokens grows from
# line to line: keep the line with the largest output (keeping the first undercounts output by about 2x).
# Dedup is global, not per file: a resumed session copies earlier requests into its new file.
d_requests = {}
for path in glob.glob(CLAUDE_GLOB, recursive=True):
    for line in open(path, errors='replace'):
        if '"usage"' not in line:
            continue
        try:
            d_rec = json.loads(line)
        except ValueError:
            continue
        d_msg = d_rec.get('message')
        if d_rec.get('type') != 'assistant' or not isinstance(d_msg, dict) or not isinstance(d_msg.get('usage'), dict):
            continue
        d_usage = d_msg['usage']
        d_cc = d_usage.get('cache_creation') or {}
        cache_write = d_usage.get('cache_creation_input_tokens') or 0
        cache_write_1h = d_cc.get('ephemeral_1h_input_tokens') or 0
        d_req = dict(month=(d_rec.get('timestamp') or '')[:7], model=d_msg.get('model'),
                     inp=d_usage.get('input_tokens') or 0, out=d_usage.get('output_tokens') or 0,
                     cache_read=d_usage.get('cache_read_input_tokens') or 0,
                     cache_write_5m=cache_write - cache_write_1h, cache_write_1h=cache_write_1h)
        key = d_rec.get('requestId') or d_msg.get('id')
        if key not in d_requests or d_req['out'] > d_requests[key]['out']:
            d_requests[key] = d_req

TOKEN_KEYS = ('inp', 'out', 'cache_read', 'cache_write_5m', 'cache_write_1h')

# --- 1. transcripts vs telemetry ---
d_telemetry = {}
db_tel = sqlite3.connect(f'{DATA_DIR}/claude/sessions_usages.db')
db_tel.execute('PRAGMA query_only = ON')  # not mode=ro: see src/usage_db.py's connect()
for (raw,) in db_tel.execute("SELECT raw FROM telemetry WHERE event = 'api_request'"):
    d_attr = json.loads(raw)['attributes']
    d_telemetry[d_attr['request_id']] = d_attr
print(f'1. Transcripts vs telemetry ({len(d_telemetry)} telemetry requests, from '
      f'{min((a["event.timestamp"] for a in d_telemetry.values()), default="-")})')
l_joined = [(d_requests[rid], d_attr) for rid, d_attr in d_telemetry.items() if rid in d_requests]
mismatches = sum(1 for d_req, d_attr in l_joined
                 if (d_req['inp'], d_req['out'], d_req['cache_read'], d_req['cache_write_5m'] + d_req['cache_write_1h'])
                 != (d_attr['input_tokens'], d_attr['output_tokens'], d_attr['cache_read_tokens'], d_attr['cache_creation_tokens']))
print(f'   requests in both: {len(l_joined)}, token mismatches: {mismatches}')
d_by_model = collections.defaultdict(list)
for d_req, d_attr in l_joined:
    if d_req['model'] in D_PRICES and d_attr['cost_usd'] > 0:
        d_by_model[d_req['model']].append((usd(d_req['model'], *(d_req[k] for k in TOKEN_KEYS)), d_attr['cost_usd']))
for model, l_pairs in sorted(d_by_model.items()):
    l_err = [abs(ours - ref) / ref for ours, ref in l_pairs]
    total_err = sum(o for o, _ in l_pairs) / sum(r for _, r in l_pairs) - 1
    print(f'   {model:27} n={len(l_pairs):5}  median err {100 * statistics.median(l_err):.3f}%  '
          f'requests off by >1%: {sum(e > .01 for e in l_err)}  total err {100 * total_err:+.3f}%')
d_missing = collections.Counter()
for rid, d_attr in d_telemetry.items():
    if rid not in d_requests:
        d_missing[d_attr.get('query_source')] += d_attr['cost_usd']
total_tel = sum(d_attr['cost_usd'] for d_attr in d_telemetry.values())
print(f'   USD only in telemetry (never in a transcript): ${sum(d_missing.values()):.2f} of ${total_tel:.2f} '
      f'({100 * sum(d_missing.values()) / max(total_tel, 1e-9):.1f}%): '
      + ', '.join(f'{k} ${v:.2f}' for k, v in d_missing.most_common()))

# --- 2. Claude monthly totals vs ccusage ---
d_month = collections.defaultdict(collections.Counter)
for d_req in d_requests.values():
    d_month[(d_req['month'], d_req['model'])].update({k: d_req[k] for k in TOKEN_KEYS})
print('\n2. Claude monthly totals vs ccusage   (in / out / cache write / cache read, then USD)')
for key, (inp, out, cw, cr, ref_usd) in sorted(D_CCUSAGE_CLAUDE.items()):
    d_c = d_month.get(key, collections.Counter())
    ours_usd = usd(key[1], *(d_c[k] for k in TOKEN_KEYS))
    l_cols = [diff(d_c['inp'], inp), diff(d_c['out'], out), diff(d_c['cache_write_5m'] + d_c['cache_write_1h'], cw),
              diff(d_c['cache_read'], cr)]
    print(f'   {key[0]} {key[1]:27}' + ''.join(f'{c:>9}' for c in l_cols) + f'   ${ours_usd:8.2f} vs ${ref_usd:8.2f}')

# --- 3. Codex monthly tokens vs ccusage ---
# `total_token_usage` is cumulative per session file; take deltas between successive token_count events. It can
# reset (3 times by 2026-10-07): when a delta goes negative, that event's `last_token_usage` is the turn's usage.
d_codex = collections.defaultdict(collections.Counter)
for path in [p for g in CODEX_GLOBS for p in glob.glob(g, recursive=True)]:
    model, d_prev = None, None
    for line in open(path, errors='replace'):
        if '"turn_context"' not in line and '"token_count"' not in line:
            continue
        d_rec = json.loads(line)
        d_payload = d_rec.get('payload') or {}
        if d_rec.get('type') == 'turn_context':
            model = d_payload.get('model', model)
            continue
        d_info = d_payload.get('info') if d_payload.get('type') == 'token_count' else None
        d_total = (d_info or {}).get('total_token_usage')
        if not d_total or d_total == d_prev:
            continue
        l_keys = ('input_tokens', 'cached_input_tokens', 'output_tokens')
        d_delta = {k: d_total.get(k, 0) - (d_prev or {}).get(k, 0) for k in l_keys}
        if min(d_delta.values()) < 0:
            d_delta = {k: (d_info.get('last_token_usage') or {}).get(k, 0) for k in l_keys}
        d_prev = d_total
        d_codex[(d_rec['timestamp'][:7], model)].update(
            inp=d_delta['input_tokens'] - d_delta['cached_input_tokens'], out=d_delta['output_tokens'],
            cache_read=d_delta['cached_input_tokens'])
print('\n3. Codex monthly tokens vs ccusage   (input excl. cached / output / cached input)')
for key, l_ref in sorted(D_CCUSAGE_CODEX.items()):
    d_c = d_codex.get(key, collections.Counter())
    print(f'   {key[0]} {key[1]:27}' + ''.join(f'{diff(d_c[k], r):>9}' for k, r in zip(('inp', 'out', 'cache_read'), l_ref)))
