# For readers: how to read this project's data

For any project or script that **reads** what agent-usage-tracker stores: a status line, auto-apply, an analysis, a future quota maximizer. What the data means, field by field, is in `USAGE_DATA_REFERENCE.md`; what exists upstream, in `USAGE_DATA_SOURCES.md`. This file only says how to read it safely, and what to read (the design: `design/DESIGN.md`, built on 2026-10-10).

Last checked: 2026-10-10, after migration phase 5.

## 1. Rules

| # | Rule | Why |
|---|---|---|
| 1 | **Never write.** Only this project's gates write its databases | every row is insert-only and deduplicated by the gate |
| 2 | **Open read-only with `PRAGMA query_only = ON`**, never a `mode=ro` URI; set a busy timeout (10 s here) | macOS's SQLite intermittently refuses a `mode=ro` open of these WAL databases |
| 3 | **Read only the new rows by `rowid`, never by `ts` or `written_ts`**: remember the largest `rowid` you read, then `WHERE rowid > ?` | many rows are written well after their `ts` (section 2): a `ts` watermark silently skips them, forever |
| 4 | A `rowid` is local to one database file. **The same row has a different `rowid` on the Mac and on the VM**; across machines a row is identified by `row_hash` | each machine inserts in its own order |
| 5 | **A quota percent is account-wide only.** Never attribute it to a session, a folder or a machine | the meter is one number for the account; `USAGE_DATA_REFERENCE.md` §1 |
| 6 | **The percent also counts usage no local file sees** (claude.ai chats, the mobile and desktop apps, Cowork, cloud sessions) | any correlation of the % with local tokens or $ assumes that share is small (`USAGE_DATA_SOURCES.md` §2.3) |
| 7 | **Every $ is Claude Code's own list-price figure** (tokens × list price), not a bill. Where it is missing, do not compute one; use the tokens | no server returns a usage $ (`USAGE_DATA_SOURCES.md` §1) |
| 8 | **A window whose reset time has passed is at 0%**, whatever the last reading says, until a new reading arrives; `latest` already applies it | readings stop while nobody uses Claude Code (`USAGE_DATA_REFERENCE.md` §5.8) |

## 2. Rows written after their `ts` (rule 3)

| Rows | Written after their `ts` by | Since |
|---|---|---|
| `claude_statusline` readings | up to the session's idle time: `ts` is the session's last API response, written at its next render | 2026-09-30 |
| Readings copied between the Mac and the VM (`--peer`) | minutes; they also keep the other machine's `written_ts` | 2026-10-10, 14:10–21:19 UTC |
| Every reading from the other machine (sync every 2 min) | up to about 2 min | 2026-10-10 (phase 1) |
| `slots` | 15 min or more after the slot ends; the Mac's slots reach the VM up to 5 min later, or hours later after the Mac slept | 2026-10-10 |

Example: a reader runs at 10:00:00 and remembers `ts` 09:59:50. At 10:01 the sync brings the VM's reading of 09:59:30: `ts > 09:59:50` misses it forever; `rowid > 4812` finds it.

```python
db = sqlite3.connect(path, timeout=10, isolation_level=None)
db.execute("PRAGMA busy_timeout = 10000")
db.execute("PRAGMA query_only = ON")
l_rows = db.execute("SELECT rowid, * FROM account_quotas WHERE rowid > ? ORDER BY rowid", (last_rowid,)).fetchall()
last_rowid = l_rows[-1][0] if l_rows else last_rowid   # keep it in your own state
```

## 3. What to read

All paths under `~/opt/agent-usage-tracker/`. Claude only; Codex stays as it was (`USAGE_DATA_REFERENCE.md` §3, §10). Built on 2026-10-10 (`git show 1ced0c4:design/MIGRATION_PLAN.md`); the last column says what it replaced.

| Question | Read | Replaces (gone) |
|---|---|---|
| The account's quota now | view `latest` of `data/claude/account_quotas.db`: the freshest reading, any source, any machine, the reset rule applied (a passed window reads 0%, its reset NULL). The status line gets the same as six `\x1c`-separated fields printed by `src/statusline_payload_reader.py` | the file `state/quota/claude` |
| Quota history | table `account_quotas` of the same database: **the same rows on both machines**; `machine` = the machine that took the reading (NULL for the 72 readings copied between the machines on 2026-10-10 before the sync existed) | each machine's own readings only |
| Tokens and $ per 5-min slot, folder, interactive or not | table `slots` of `data/claude/aggregates/<machine>.db` (one file per machine): 5-min slot × machine × folder × entrypoint × model; 6 token columns, `usd` (Claude Code's own), `tokens_without_usd` (the part `usd` does not cover) | the views `usage_5m`, `usage_requests` of `sessions_usages.db` (their `usd` partly from a price table) |
| The same, every machine at once | on the VM: every file in `data/claude/aggregates/` (`H-Frank-1.db`, the VM's; `chan-lescut-macbook-pro.db`, the Mac's); `UNION ALL` their `slots` | until 2026-10-12: `data/claude/aggregates.db` + `central/chan-lescut-macbook-pro/data/claude/aggregates.db`; before 2026-10-10, the raw copies the old push sent, now in `data/_archive/central-push-era/` |
| Per request, per session | `telemetry` (one row per API request event, Claude Code's own cost; count requests through the view `telemetry_requests`) and `session_snapshots` in `data/claude/sessions_usages.db`, each machine its own (Category 2, never shared). The per-request transcript rows are in `intermediary/`: local working tables, deletable, **not for readers** | Claude's `requests` table in `sessions_usages.db` |
| "Interactive or scheduled" | `slots.entrypoint`: `cli` = interactive terminal, `sdk-cli` = `claude -p`, `sdk-py` = Agent SDK; classifying them is the reader's job | — |

The four categories (`design/DESIGN.md`): **1** quotas, raw, shared by both machines; **2** each machine's raw rows, never shared; **3** `slots`, aggregates of 2, the Mac's sent to the VM; **4** anything computed from 1 + 3 belongs to the reader, not to this project.

## 4. When this file changes

Any change to what a reader reads, or how, updates section 3 here with its date, and a row in `USAGE_DATA_REFERENCE.md` §6.
