# For readers: how to read this project's data

For any project or script that **reads** what agent-usage-tracker stores: a status line, auto-apply, an analysis, a future quota maximizer. This file says how to read it safely, and what to read. What each field means: `doc/tables.md`; what exists upstream: `doc/upstream.md`; why it is built this way: `doc/design.md`; when a row's shape changed: `doc/changes.md`.

Last checked: 2026-10-12.

## 1. Rules

| # | Rule | Why |
|---|---|---|
| 1 | **Never write.** Only this project's gates write its databases | every row is insert-only and deduplicated by the gate |
| 2 | **Open read-only with `PRAGMA query_only = ON`**, never a `mode=ro` URI; set a busy timeout (10 s here) | macOS's SQLite intermittently refuses a `mode=ro` open of these WAL databases ("unable to open database file") |
| 3 | **Read only the new rows by `rowid`, never by `ts` or `written_ts`**: remember the largest `rowid` you read, then `WHERE rowid > ?`. A time range (not "what is new") filters on `ts`, indexed in every table | many rows are written well after their `ts` (section 2): a `ts` watermark silently skips them, forever |
| 4 | A `rowid` is local to one database file. **The same row has a different `rowid` on the Mac and on the VM**; across machines a row is identified by `row_hash` | each machine inserts in its own order |
| 5 | **A quota percent is account-wide only.** Never attribute it to a session, a folder or a machine | the meter is one number for the account (`doc/tables.md` §1) |
| 6 | **The percent also counts usage no local file sees** (claude.ai chats, the mobile and desktop apps, Cowork, cloud sessions) | any correlation of the % with local tokens or $ assumes that share is small (`doc/upstream.md` §2.3) |
| 7 | **Every $ is Claude Code's own list-price figure** (tokens × list price), not a bill. Where it is missing, do not compute one; use the tokens | no server returns a usage $ (`doc/upstream.md` §1) |
| 8 | **A window whose reset time has passed is at 0%**, whatever the last reading says, until a new reading arrives; `latest` already applies it | readings stop while nobody uses Claude Code (`doc/design.md` P15) |
| 9 | **A stretch with no reading at all is unknown, not unchanged** | while Claude Code runs on neither machine for more than 8 h, no reading is taken; usage elsewhere shows up afterwards as a total (`doc/design.md` P15) |
| 10 | **Within one window the percent never decreases**: in a series, keep the first reading of each new running maximum, keyed by `ts` | older statusline rows include stale re-sent readings (`doc/tables.md` §7.2) |
| 11 | **Never compare a percent across plans** (Pro until 2026-10-07 16:51 UTC, Max 5x since) | 1% of Max 5x weighs about 4× a Pro 1% (`adhoc_quotas_analysis/CONCLUSIONS.md` §2) |

## 2. Rows written after their `ts` (rule 3)

| Rows | Written after their `ts` by | Since |
|---|---|---|
| `claude_statusline` readings | up to the session's idle time: `ts` is the session's last API response, written at its next render | 2026-09-30 |
| Every reading from the other machine (sync every 2 min) | up to about 2 min; it keeps the other machine's `written_ts` | 2026-10-10 |
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

All paths under `~/opt/agent-usage-tracker/`. Claude only; Codex: `doc/tables.md` §6.

| Question | Read |
|---|---|
| The account's quota now | view `latest` of `data/claude/account_quotas.db`: the freshest reading, any source, any machine, the reset rule applied (a passed window reads 0%, its reset NULL). The status line gets the same as six `\x1c`-separated fields printed by `src/statusline_payload_reader.py` (`doc/tables.md` §3.3) |
| Quota history | table `account_quotas` of the same database: **the same rows on both machines**; `machine` = the machine that took the reading (NULL for 72 readings copied between the machines on 2026-10-10 before the sync existed) |
| Tokens and $ per 5-min slot, folder, interactive or not | table `slots` of `data/claude/aggregates/<machine>.db` (one file per machine): 5-min slot × machine × folder × entrypoint × model; 6 token columns, `usd` (Claude Code's own), `tokens_without_usd` (the part `usd` does not cover) |
| The same, every machine at once | on the VM: every file in `data/claude/aggregates/` (`H-Frank-1.db`, the VM's; `chan-lescut-macbook-pro.db`, the Mac's); `UNION ALL` their `slots` |
| Per request, per session | `telemetry` (one row per API request event, Claude Code's own cost; count requests through the view `telemetry_requests`) and `session_snapshots` in `data/claude/sessions_usages.db`, each machine its own (Category 2, never shared). The per-request transcript rows in `data/claude/intermediary.db` are local working tables, deletable: **not for readers** |
| "Interactive or scheduled" | `slots.entrypoint`: `cli` = interactive terminal, `sdk-cli` = `claude -p`, `sdk-py` = Agent SDK; classifying them is the reader's job |
| Something you read before 2026-10-10 (`state/quota/claude`, the views `usage_5m` / `usage_requests`, `central/`) | gone: `doc/changes.md` says what replaced it |

The four categories (`doc/design.md` §1): **1** quotas, raw, shared by both machines; **2** each machine's raw rows, never shared; **3** `slots`, aggregates of 2, the Mac's sent to the VM; **4** anything computed from 1 + 3 belongs to the reader, not to this project.

## 4. When this file changes

Any change to what a reader reads, or how, updates section 3 here, its "Last checked" line, and adds a row to `doc/changes.md`.
