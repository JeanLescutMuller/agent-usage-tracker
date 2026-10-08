"""The account quota history, one JSON text line per row, in the order the
rows were written - from data/<agent>/account_quotas.db's `raw` column since
the 2026-10-08 move to SQLite, or from the old JSONL file with
QUOTA_SOURCE=jsonl (used once to prove the two give identical results).

The runtime is ~/opt/agent-usage-tracker unless AGENT_USAGE_TRACKER_RUNTIME
says otherwise. Read-only.
"""
import os
import sqlite3

RUNTIME = os.environ.get('AGENT_USAGE_TRACKER_RUNTIME') or os.path.expanduser('~/opt/agent-usage-tracker')


def account_lines(agent):
    if os.environ.get('QUOTA_SOURCE') == 'jsonl':
        yield from open(f'{RUNTIME}/data/{agent}/account.jsonl')
        return
    # query_only rather than mode=ro: see src/usage_db.py's connect().
    db = sqlite3.connect(f'{RUNTIME}/data/{agent}/account_quotas.db')
    db.execute('PRAGMA query_only = ON')
    for (raw,) in db.execute('SELECT raw FROM account_quotas ORDER BY rowid'):
        yield raw + '\n'
    db.close()
