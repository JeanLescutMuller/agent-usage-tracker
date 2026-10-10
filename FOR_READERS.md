# For readers: how to read this project's data

For any project or script that **reads** what agent-usage-tracker stores: a status line, auto-apply, an analysis, a future quota maximizer. What the data means, field by field, is in `USAGE_DATA_REFERENCE.md`; what exists upstream, in `USAGE_DATA_SOURCES.md`. This file only says how to read it safely, and what changes for readers while the target design (`design/TARGET_SPEC.md`, validated 2026-10-10) is being built (`design/MIGRATION_PLAN.md`).

Last checked: 2026-10-10, against commit `9aed132`.

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
| 8 | **A window whose reset time has passed is at 0%**, whatever the last reading says, until a new reading arrives | readings stop while nobody uses Claude Code (`USAGE_DATA_REFERENCE.md` §5.8) |

## 2. Rows written after their `ts` (rule 3)

| Rows | Written after their `ts` by | Since |
|---|---|---|
| `claude_statusline` readings | up to the session's idle time: `ts` is the session's last API response, written at its next render | 2026-09-30 |
| Readings copied between the Mac and the VM (`--peer`) | minutes; they also keep the other machine's `written_ts` | 2026-10-10 |
| Transcript requests | 15 min or more: a request is stored once its first line is 15 min old | 2026-10-08 |
| Target: every reading from the other machine (sync every 2 min) | up to about 2 min | phase 1 |
| Target: `slots` | 15 min or more after the slot ends; the Mac's slots reach the VM up to 5 min later, or hours later after the Mac slept | phases 2, 3 |

Example: a reader runs at 10:00:00 and remembers `ts` 09:59:50. At 10:01 the sync brings the VM's reading of 09:59:30: `ts > 09:59:50` misses it forever; `rowid > 4812` finds it.

```python
db = sqlite3.connect(path, timeout=10, isolation_level=None)
db.execute("PRAGMA busy_timeout = 10000")
db.execute("PRAGMA query_only = ON")
l_rows = db.execute("SELECT rowid, * FROM account_quotas WHERE rowid > ? ORDER BY rowid", (last_rowid,)).fetchall()
last_rowid = l_rows[-1][0] if l_rows else last_rowid   # keep it in your own state
```

## 3. What to read, today and once built

All paths under `~/opt/agent-usage-tracker/`. Claude only; Codex stays as today (`USAGE_DATA_REFERENCE.md` §3).

| Question | Today (2026-10-10) | Target | Changes in |
|---|---|---|---|
| The account's quota now | `state/quota/claude`: one line, 6 fields separated by `\x1c`: `five_pct five_reset week_pct week_reset source observed_at`; apply rule 8 yourself | view `latest` of `data/claude/account_quotas.db`, rule 8 already applied (a passed window: 0%, reset NULL). The status line gets the same 6 fields printed by `src/statusline_payload_reader.py` | phase 4 (the file disappears) |
| Quota history | table `account_quotas` of `data/claude/account_quotas.db`, each machine its own readings | the same table, **the same rows on both machines**, new column `machine` (NULL for the readings `--peer` copied between the machines from 2026-10-10: their machine is unknown) | phase 1 |
| Tokens and $ per 5-min slot, folder, interactive or not | view `usage_5m` of `data/claude/sessions_usages.db` (its `usd` is partly computed from a price table: avoid) | table `slots` of `data/claude/aggregates.db`: 5-min slot × machine × folder × entrypoint × model; 6 token columns, `usd` (Claude Code's own), `tokens_without_usd` (the part `usd` does not cover) | phase 2 |
| The same, every machine at once | the VM's `central/<machine>/data/claude/sessions_usages.db` (raw rows pushed every 5 min) | on the VM: `data/claude/aggregates.db` (the VM's slots) + `central/chan-lescut-macbook-pro/data/claude/aggregates.db` (the Mac's) | phase 3 (`central/` raw copies archived) |
| Per request, per session | `requests`, `telemetry`, `session_snapshots` in `data/claude/sessions_usages.db` | `telemetry`, `session_snapshots` stay there (Category 2, on its own machine only); per-request transcript rows move to `intermediary/` (local working tables, deletable: **do not read**) | phases 2, 5 |
| "Interactive or scheduled" | `entrypoint`: `cli` = interactive terminal, `sdk-cli` = `claude -p`, `sdk-py` = Agent SDK; classifying them is the reader's job | the same, a column of `slots` | — |

The four categories (`design/TARGET_SPEC.md`): **1** quotas, raw, shared by both machines; **2** each machine's raw rows, never shared; **3** `slots`, aggregates of 2, the Mac's sent to the VM; **4** anything computed from 1 + 3 belongs to the reader, not to this project.

## 4. When this file changes

Each migration phase that changes a row of section 3 updates it, with its date. A reader built against "today" keeps working until the phase named in the last column.
