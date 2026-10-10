# Target specification (Claude only)

Status: **validated by the user on 2026-10-10 (P1–P12 all accepted); not built yet.** It turns the design the user validated in `design/DESIGN_MAP.html` (and the decisions in `design/OPEN_QUESTIONS.md`) into exact files, tables, jobs and contracts. The migration plan is written from it once it is validated. Codex is out of scope and keeps running as today (`design/CODEX_DESIGN.md`).

Words used here, as defined with the user:

| Word | Meaning |
|---|---|
| Category 1 · Quotas | the account's quota readings (5-hour and 7-day %), raw; the same rows on both machines, synced every 2 min |
| Category 2 · Machine raw | `sessions_usages.db` + Claude Code's transcripts; never leaves its machine |
| Category 3 · Aggregates | `aggregates.db`, one table `slots`; the Mac sends its rows to the VM every 5 min |
| Category 4 · Meta-analysis | built by the quota maximizer (another project), not here |
| intermediary | the local working tables between Category 2 and Category 3; never sent, deletable, rebuilt from Category 2 |
| 5-min slot | a 5-minute interval starting at a multiple of 300 s (e.g. 19:10:00–19:14:59 UTC) |
| final slot | a slot every request of which is already counted: it ended more than 15 min before the last read of the transcripts |

## 0. Proposals accepted on 2026-10-10

The details below the design were proposed by the agent and each accepted by the user. Their ids are used in the sections that follow.

| # | Proposal | Section |
|---|---|---|
| P1 | A new Category 1 row carries `"machine"` inside its raw JSON (so its identity, `row_hash`, includes it) and in a new column `machine` | 3.1 |
| P2 | `poll_errors` (failed polls) stays on its machine, never synced: a 429 or a 401 concerns one machine only | 3.1 |
| P3 | The sync is done by the Mac's `claude-quota` job (the VM is passive), every 2 min, both ways in one ssh call; it replaces `--peer` | 3.3 |
| P4 | Poll thresholds: Mac 110 s (status line on screen) / 300 s (idle); VM 290 s / 480 s | 3.4 |
| P5 | The reset rule lives in the `latest` view: after its reset time, a window shows 0% and an empty reset time | 3.2 |
| P6 | The reader becomes executable (install.sh writes its first line with this machine's python); agent-statusline runs it and reads the 6 fields it prints | 7.1 |
| P7 | `intermediary/claude/` holds `requests.db` (tables `requests`, `scans`) and `offsets.json`; written by `transcript_reader.py` only | 5 |
| P8 | `slots` gets a 6th token column, `cache_write_unknown_tokens`, for telemetry-only requests (telemetry gives no 5m / 1h split) | 6.1 |
| P9 | On the VM, the old `central/<machine>/` folders move whole to `central/_archive/` (that move is the backup); `central/chan-lescut-macbook-pro/` restarts with only `aggregates.db` | 6.4 |
| P10 | A new gate, `ingest_aggregates.py`, is the only writer of `aggregates.db` (the Mac's own, and the VM's copy of it) | 6.3 |
| P11 | Readers that read new rows incrementally use `rowid`, never `ts` or `written_ts`: synced rows arrive up to 2 min after their time | 7.3 |
| P12 | Jobs: `transcripts` and `push` are replaced by one job `aggregates`; their names are decommissioned on job-runner's dashboard | 8 |

## 1. The picture

```
            MacBook (chan-lescut-macbook-pro)                                VM (H-Frank-1)
 ┌──────────────────────────────────────────────────────┐   ┌──────────────────────────────────────────────────────┐
 │ CAT 1  data/claude/account_quotas.db  ◀═══ sync every 2 min, both ways (Mac starts it) ═══▶  data/claude/account_quotas.db │
 │          ▲ statusline readings, ▲ polls (if stale)   │   │          ▲ statusline readings, ▲ polls (if stale)  │
 │                                                      │   │                                                      │
 │ CAT 2  data/claude/sessions_usages.db (telemetry, …) │   │ CAT 2  data/claude/sessions_usages.db               │
 │        ~/.claude/projects (transcripts)              │   │        ~/.claude/projects                           │
 │          │ job aggregates, every 5 min               │   │          │ job aggregates, every 5 min              │
 │          ▼                                           │   │          ▼                                          │
 │        intermediary/claude/ (local, never sent)      │   │        intermediary/claude/                         │
 │          ▼                                           │   │          ▼                                          │
 │ CAT 3  data/claude/aggregates.db (slots) ─── new final slots, every 5 min ──▶ central/chan-lescut-macbook-pro/data/claude/aggregates.db │
 │                                                      │   │ CAT 3  data/claude/aggregates.db (the VM's slots)   │
 └──────────────────────────────────────────────────────┘   │                                                      │
                                                            │ CAT 4  the quota maximizer reads Categories 1 + 3   │
                                                            └──────────────────────────────────────────────────────┘
```

## 2. Files on disk

`~/opt/agent-usage-tracker/` on each machine (Codex files omitted: unchanged).

| Path | Category | Mac | VM | Change |
|---|---|---|---|---|
| `data/claude/account_quotas.db` | 1 | ✓ | ✓ | new column `machine`; view `latest` applies the reset rule |
| `data/claude/sessions_usages.db` | 2 | ✓ | ✓ | `requests`, `scans` and the views `usage_requests`, `usage_5m` leave it |
| `data/claude/aggregates.db` | 3 | ✓ | ✓ | **new**, table `slots` |
| `intermediary/claude/requests.db`, `offsets.json` | — | ✓ | ✓ | **new** (P7); `offsets.json` moves here from `state/transcript_reader/` |
| `central/chan-lescut-macbook-pro/data/claude/aggregates.db` | 3 | — | ✓ | **new**: the Mac's slots |
| `central/_archive/<machine>-push-era/` | — | — | ✓ | the old `central/<machine>/` folders, moved whole (P9) |
| `state/quota/claude` | — | ✗ | ✗ | **removed** (the `latest` view replaces it) |
| `state/sync/claude_quotas.json` | — | ✓ | — | **new**: the sync's two watermarks and its last success |
| `state/push/watermarks.json` | — | ✓ | — | kept: now only the `slots` watermark |
| `bin/` | — | ✗ | ✗ | **removed** (P6) |

## 3. Category 1 · Quotas

### 3.1 Table `account_quotas` (and `poll_errors`)

Unchanged columns: `ts` (when the reading was true), `dt`, `source` (`claude_statusline` / `claude_api`), the four percent and reset columns, `observed_by_session`, `written_ts`, `raw`, `row_hash`. Added:

| Column | Type | Meaning |
|---|---|---|
| `machine` | TEXT, NULL allowed | the machine that took the reading: `usage_db.machine_name()` when it was written; NULL = unknown (P1) |

- **New rows** (P1): the collector adds `"machine": "<name>"` to the raw JSON before handing it to the gate. The same reading taken on two machines can then never collide, and a row synced to the other machine keeps its machine.
- **Old rows**, filled once by the migration: rows in the Mac's database → `chan-lescut-macbook-pro`; in the VM's → `H-Frank-1`; the **60** readings present in both since 2026-10-10 14:10 UTC (copied by `--peer`; same `written_ts`, nothing in `raw` names the machine) → NULL. Decided with the user on 2026-10-10.
- `poll_errors` (P2): unchanged and **not synced**. A 429 or a 401 is about one machine's login token.
- The gate stays the only writer; dedup unchanged (a re-render is dropped, a new observation with the same % is kept).

### 3.2 View `latest` and the reset rule (P5)

`latest` = the freshest reading, any source, **any machine**, with the reset rule applied when read, per window:

```
five_hour_pct  = 0     and five_hour_resets_ts = NULL   when five_hour_resets_ts <= now
seven_day_pct  = 0     and seven_day_resets_ts = NULL   when seven_day_resets_ts <= now
otherwise the reading's own values
```

(The validated rule `observation < reset <= now` reduces to `reset <= now`: a reading's reset time is always after the reading.) Example: a reading at 08:00 says 5h 42%, reset 10:00. At 10:05, with no newer reading, `latest` gives 5h 0%, reset empty. The next real reading replaces it.

Columns of `latest`: `ts, dt, source, machine, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts`.

### 3.3 The sync (P3)

The Mac's `claude-quota` job runs it, when the last successful sync is ≥ 110 s old (so about every 2 min with a 60 s tick). One ssh call does both directions:

```
Mac                                                         VM
claude_quota_api_poller.py --sync-with=H-Frank-1
  stdin ─▶ the Mac's own new rows                     ─▶  ingest_account_quota.py --sync-from=chan-lescut-macbook-pro --after=<V>
           (machine = Mac, rowid > M)                       1. stores each line through the gate (no dedup: already deduped)
  stdout ◀─ the VM's own new rows (machine = VM,       ◀─   2. prints its own rows with rowid > V, then "rowid <max>"
           rowid > V), then "rowid <max>"
  stores them through the local gate, then saves M and V in state/sync/claude_quotas.json
```

- Only a machine's **own** rows travel (never echoed back); a re-sent row is a no-op (`row_hash`).
- Watermarks advance only after both sides succeeded: a failed sync is simply resent next time.
- At most 20,000 rows per call; a backlog drains over successive runs.
- VM unreachable: the job goes on (it still polls if needed); its summary line says `VM not reached`. The job reports a FAILURE only when the last successful sync is older than 30 min, so a lasting break shows on the dashboard.
- `--peer` and `--latest` are removed.

### 3.4 When a machine polls (P4)

Each run of `claude-quota` (tick 60 s, both machines): sync first (Mac only, when due), then **age** = now − the newest reading's `ts` in the local `account_quotas.db` (any machine, any source). Poll only if the age reaches the threshold:

| Machine | Status line on screen here (heartbeat < 90 s) | Idle |
|---|---|---|
| Mac | 110 s | 300 s |
| VM | 290 s = 110 + 120 (sync) + 60 (tick) | 480 s = 300 + 180 |

So while the Mac is awake and syncing, the VM never polls; when the Mac sleeps, the VM takes over. A new Mac poll reaches the VM at the next sync (≤ 2 min). The age check moves into the poller (`--if-older-than=SECONDS`, exit 10 = fresh, no poll); 429 → exit 75, WAIT on this machine only (unchanged).

## 4. Category 2 · Machine raw

| Store | Tables | Change |
|---|---|---|
| `data/claude/sessions_usages.db` | `session_snapshots` (statusline: session cost, cache stats), `telemetry` (one row per event, Claude Code's own $) | `requests`, `scans` move to intermediary; views `usage_requests`, `usage_5m` removed; view `telemetry_requests` (one event per request id) kept |
| `~/.claude/projects/**/*.jsonl` | Claude Code's transcripts (its own files; deleted after 365 days) | none |

Never sent anywhere. The `$` computed by us disappears with `requests.usd`; `usage_db.PRICES` keeps only the Codex entry (Codex out of scope).

## 5. intermediary (P7)

`~/opt/agent-usage-tracker/intermediary/claude/`, on each machine, written only by `transcript_reader.py`:

| File / table | One row = | Columns |
|---|---|---|
| `requests.db` · `requests` | one API request, counted once (the transcript line with the largest `output_tokens` per request id) | `ts` (its first line), `dt`, `request_id` (UNIQUE), `session_id`, `folder` (cwd), `entrypoint`, `model`, the 5 token columns. No `usd`, no `machine` (always this machine) |
| `requests.db` · `scans` | one run of the reader | `ts`, `dt`, `complete_through_ts` (= run time − 15 min: every request that started before it is in `requests`), `requests_added` |
| `offsets.json` | one transcript file | how far it was read (byte offset, inode) |

Rule: **deleting `intermediary/` is always safe.** The next run re-reads every transcript from the start (one slow run, about 1 GB on the Mac) and rebuilds the same rows. Slots already written are not touched (insert-only).

## 6. Category 3 · Aggregates

### 6.1 Table `slots` (P8)

| Column | Type | Meaning |
|---|---|---|
| `ts` | INTEGER | slot start, a multiple of 300 (epoch s) |
| `dt` | TEXT | the same, ISO UTC |
| `machine` | TEXT NOT NULL | where the requests ran |
| `folder` | TEXT NOT NULL | the session's working directory (`cwd`); `''` when unknown |
| `entrypoint` | TEXT NOT NULL | as Claude Code wrote it: `cli` (interactive terminal), `sdk-cli` (`claude -p`), `sdk-py` (Agent SDK), …; `''` when unknown |
| `model` | TEXT NOT NULL | e.g. `claude-opus-5-5` |
| `requests` | INTEGER | number of requests |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens` | INTEGER | sums, from the transcripts |
| `cache_write_unknown_tokens` | INTEGER | cache writes of telemetry-only requests (telemetry gives no 5m / 1h split; 6% of all cache writes, measured) |
| `usd` | REAL, NULL allowed | sum of Claude Code's own `cost_usd` (telemetry) over the requests that have one; NULL when none has |
| `tokens_without_usd` | INTEGER | all tokens (the 6 kinds) of the requests with no telemetry: the part of the slot the `usd` does not cover |
| `written_ts` | INTEGER | when the row was written |

Key: UNIQUE (`ts`, `machine`, `folder`, `entrypoint`, `model`). Insert-only: a slot is written once, when final, and never changes. No percent, ever (same check as `sessions_usages.db`). An example built from two real requests (their telemetry `cost_usd`: 0.0686 + 0.0730):

```
ts 2026-10-10T19:10:00Z · chan-lescut-macbook-pro · /Users/jeanlescut/dev/trading-bot · cli · claude-opus-5-5
requests 2 · in 6 · out 351 · cache read 643,761 · write 5m 0 · write 1h 724 · write unknown 0 · usd 0.1416 · without usd 0
```

### 6.2 How a slot is built (job `aggregates`, every 5 min)

```
1. transcript_reader.py: new transcript lines → intermediary requests (+ a scan: complete_through = now − 15 min)
2. every request of every slot that is final and not yet written:
     transcript requests ── LEFT JOIN telemetry_requests ON request_id ──▶ tokens + usd (or tokens_without_usd)
   + telemetry-only requests (prompt suggestions, summaries, web search, titles, compaction: 1,228 so far, 9.2% of the $)
       placed by their event time; folder and entrypoint from the same session's transcript requests
       (every one so far has such a session); cache writes → cache_write_unknown_tokens
3. sum per (slot, machine, folder, entrypoint, model) → ingest_aggregates.py → data/claude/aggregates.db
4. Mac only: send the new slots to the VM (6.4)
```

- A request belongs to the slot of its first transcript line; a telemetry-only request, to the slot of its event.
- **Final** = the slot ended at or before the latest scan's `complete_through_ts`. Telemetry is never later than that: 8,089 of 8,090 events arrived within 60 s, the slowest 556 s, none after 15 min (measured 2026-10-10). A Mac asleep 22:00–08:00 writes nothing meanwhile, then every slot up to 07:45 at its first run.
- First run: every slot since the oldest transcript (2026-07-30). Before telemetry existed (2026-09-30), `usd` is NULL and `tokens_without_usd` holds everything.

### 6.3 The gate `ingest_aggregates.py` (P10)

The only writer of any `aggregates.db`. Checks each row (columns, types, no percent), `INSERT OR IGNORE` on the key. Used by the aggregates step locally and by the receiver on the VM (`--machine NAME` writes into `central/NAME/`).

### 6.4 Sending to the VM (P9)

`push_to_central.py` keeps its mechanism (rowid watermark, chunks of 20,000, ssh, the watermark advances only on success) but sends **only `slots`**. `receive_from_machine.py` accepts only `slots` and writes `central/<machine>/data/claude/aggregates.db` through the gate. The VM sends nothing (its slots are already on the VM). Before the first send, the old `central/<machine>/` folders move whole to `central/_archive/<machine>-push-era/`, untouched.

## 7. Contracts with other projects

### 7.1 agent-statusline (P6)

| | Today | Target |
|---|---|---|
| Call | `bin/ingest-claude-statusline.sh` (payload on stdin), then read `state/quota/claude` | `~/opt/agent-usage-tracker/src/statusline_payload_reader.py` (payload on stdin), executable; read its stdout |
| Output | nothing | one line, the 6 fields separated by `\x1c`: `five_pct five_reset week_pct week_reset source observed_at` (percents rounded half up, as today; reset rule applied: an empty reset after it passed) — or nothing |
| Python | `bin/` script with the interpreter path written by `install.sh` | the reader's first line, written by `install.sh`: `#!<this machine's python3> -S` |
| Always | exit 0 | exit 0; prints nothing on any failure, and agent-statusline then shows its own payload's reading (as today without the tracker) |

### 7.2 auto-apply

`runner/limits.py` reads `SELECT five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts, ts FROM latest` from `account_quotas.db` (opened with `PRAGMA query_only = ON`, never `mode=ro`) instead of `state/quota/claude`. Its own "a passed reset counts as 0" stays harmless.

### 7.3 agent-quota-maximizer (P11)

It reads `account_quotas.db`'s `raw` (`release/aqm/s1_ingest.py`): the new `machine` column changes nothing for it. But its incremental read (`WHERE ts > ? OR written_ts > ?`) would miss synced rows, which arrive up to 2 min after their `ts` and `written_ts`: it must use `rowid > ?`. Its Category 4 reads `slots` from `data/claude/aggregates.db` and `central/*/data/claude/aggregates.db` on the VM.

### 7.4 job-runner

The dashboard conf gets `DECOMMISSIONED=1` for `agent-usage-tracker.transcripts` and `agent-usage-tracker.push`; `examples/agent-usage-tracker/` gets the new entrypoints. job-runner is changed only with the user's OK.

## 8. Jobs (P12)

| Job | Mac | VM | Every | Does |
|---|---|---|---|---|
| `claude-quota` | ✓ | ✓ | tick 60 s | Mac: sync when due (3.3); both: poll if the age reaches the threshold (3.4) |
| `aggregates` **new** | ✓ | ✓ | 5 min | transcripts → intermediary → final slots → `aggregates.db`; Mac: then send to the VM. Also reads Codex session files as today (Codex unchanged). Claim 5 min: no overlap |
| `otel` (service) | ✓ | ✓ | always on | unchanged |
| `codex-quota`, `codex-plan-history` | ✓ | — | 60 s, 1 h | unchanged |
| ~~`transcripts`~~, ~~`push`~~ | | | | replaced by `aggregates` |

Mac: 4 jobs + 1 service (today 5 + 1). VM: 2 jobs + 1 service (today 3 + 1).

## 9. Codex (unchanged, consequences)

`transcript_reader.py` keeps writing Codex turns into `data/codex/sessions_usages.db` (`requests` with `usd`, `scans`), inside the `aggregates` job. With `push` gone, Codex rows stop reaching the VM (accepted, `design/CODEX_DESIGN.md`).

## 10. Code changes, file by file

| File | Change |
|---|---|
| `src/usage_db.py` | `machine` column; `latest` with the reset rule; sessions schema without `requests`/`scans`/2 views; new `AGGREGATES_SCHEMA`, `INTERMEDIARY_SCHEMA`; `open_aggregates()`, `open_intermediary()`; `PRICES`: Codex only |
| `src/ingest_account_quota.py` | stores `machine`; no more `state/quota/claude`; `--sync-from` / `--after` (VM side of the sync); `--latest` removed |
| `src/claude_quota_api_poller.py` | `--sync-with`, `--if-older-than`; adds `machine` to its rows; `--peer` removed |
| `src/statusline_payload_reader.py` | adds `machine`; prints the 6 fields from `latest` |
| `src/transcript_reader.py` | Claude part writes `intermediary/` (no `usd`, no `machine`); Codex part unchanged |
| `src/aggregate_slots.py` **new** | the slot building of 6.2 |
| `src/ingest_aggregates.py` **new** | the gate of 6.3 |
| `src/push_to_central.py`, `src/receive_from_machine.py` | `slots` only |
| `jobs/claude-quota.sh` | thresholds of 3.4; age from the poller; the sync |
| `jobs/aggregates.sh` **new**; `jobs/transcripts.sh`, `jobs/push.sh` removed | |
| `bin/ingest-claude-statusline.sh` removed | |
| `install.sh` / `uninstall.sh` | jobs list; reader's first line; `intermediary/`; old triggers removed |
| `tests/` | per change; hygiene test: three gates + `transcript_reader.py` for `intermediary/` |
| `README.md`, `USAGE_DATA_REFERENCE.md` (§6 row, "Last verified"), `AGENTS.md` (layout, rules), `TODO.md` | |
| other repos | agent-statusline provider + its tests; auto-apply `limits.py`; agent-quota-maximizer `s1_ingest.py` (rowid); job-runner dashboard conf |

## 11. Data to migrate (inputs to the migration plan, not the plan)

| Data | From | To |
|---|---|---|
| Category 1, Mac rows | VM `central/chan-lescut-macbook-pro/data/claude/account_quotas.db` (pushed until the switch) | VM `data/claude/account_quotas.db`, `machine` = Mac (60 shared readings: NULL) |
| Category 1, `machine` of old rows | each machine's own database | filled as in 3.1 |
| Category 1, VM rows to the Mac | VM | Mac, by the first sync (538 rows today) |
| `requests`, `scans` | `sessions_usages.db` | not moved: rebuilt in `intermediary/` from the transcripts, compared with the old table (every old request found again), then dropped with an archive |
| `state/quota/claude`, `bin/` | | removed |
| old `central/<machine>/` | VM | `central/_archive/<machine>-push-era/` (P9) |

## 12. Facts measured for this specification (2026-10-10)

| Fact | Value |
|---|---|
| Telemetry lateness (`received_ts − ts`, 8,090 `api_request` events) | ≤ 60 s: 8,089; max 556 s; > 15 min: 0 |
| Telemetry-only requests | 1,228 of 8,091 (9.2% of the $); all in a session that also has transcript requests |
| Telemetry attributes | no working directory, no 5m / 1h cache split, has `request_id`, `session.id`, `model`, `query_source` |
| Transcript lines per request (Mac) | 47,204 lines, 22,617 requests: 2.1 per request |
| Readings in both machines' databases since 2026-10-10 14:10 UTC | 60 (`claude_api`), same `written_ts` on both sides |
| Oldest transcript (Mac and VM) | 2026-07-30 |
