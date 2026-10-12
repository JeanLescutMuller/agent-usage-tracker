# agent-usage-tracker: design

Records Claude Code usage over time - quota percent, tokens, Claude Code's own $ - on the MacBook and the VM `H-Frank-1`, in four categories of data. Claude only; Codex keeps its own jobs (§13).

| | |
|---|---|
| Status | **built** 2026-10-10 (5 migration phases, each a dated row in `doc/changes.md`); decisions P1–P12 validated by Jean on 2026-10-10 (§11); the page validated by Jean on 2026-10-12 |
| Two files, one design | `doc/design.md` (this file, for agents) and `README.html` (the same sections, drawn, for Jean; the README since 2026-10-12). **They change together, section by section.** |
| Words | the `## Vocabulary` table of `AGENTS.md` |
| History | the migration plan and the record of the open questions: `git show 1ced0c4:design/MIGRATION_PLAN.md`, `git show 1ced0c4:design/OPEN_QUESTIONS.md` |
| Changed 2026-10-12 | P13 `data/claude/intermediary.db` replaces `intermediary/claude/`; P14 one `aggregates` file per machine in `data/claude/aggregates/`, replacing `central/` (§11); built the same day, 18:45 UTC (`migrate_to_target.py data-layout`) |
| Open work | `TODO/`, one file per task |

## 1. The four categories, and the picture

| Category | What | Raw or computed | Where it lives | Copies |
|---|---|---|---|---|
| **1 · Quotas** | the account's quota readings (5-hour and 7-day %, reset times); failed polls | raw: lost if not stored | both machines, the same rows, synced both ways every 2 min | 2 |
| **2 · Machine raw** | `sessions_usages.db` (telemetry, statusline session snapshots) + Claude Code's transcripts | raw | **never leaves its machine** | 1 (accepted) |
| **3 · Aggregates** | Category 2 added up per 5-min slot × machine × folder × entrypoint × model: one table, `slots` | computed from 2 | each machine computes its own every 5 min; the Mac sends its rows to the VM | 2 for the Mac's rows |
| **4 · Meta-analysis** | $ or tokens per window, past use by project, machine, interactive vs scheduled | computed from 1 + 3 | **not here: a reader's** (`FOR_READERS.md`) | — |

Data only flows 2 → 3 → 4 and 1 → 4. Only Categories 1 and 3 cross machines.

```
            MacBook (chan-lescut-macbook-pro)                                   VM (H-Frank-1)
            sleeps, behind NAT                                                  always on
 ┌──────────────────────────────────────────────────┐               ┌──────────────────────────────────────────────────┐
 │ CAT 1  data/claude/account_quotas.db   ◀══ sync, both ways, every 2 min ══▶  data/claude/account_quotas.db          │
 │          ▲ statusline readings, polls (if stale)│  (the Mac's   │          ▲ statusline readings, ▲ polls (if stale)│
 │                                                  │ claude-quota) │                                                  │
 │ CAT 2  data/claude/sessions_usages.db            │  ✕ never ✕    │ CAT 2  data/claude/sessions_usages.db            │
 │        ~/.claude/projects (transcripts)          │               │        ~/.claude/projects                        │
 │          │ job aggregates, every 5 min           │               │          │ job aggregates, every 5 min           │
 │          ▼                                       │               │          ▼                                       │
 │        data/claude/intermediary.db (local)       │               │        data/claude/intermediary.db               │
 │          ▼                                       │               │          ▼                                       │
 │ CAT 3  data/claude/aggregates/                   │               │ CAT 3  data/claude/aggregates/                   │
 │          chan-lescut-macbook-pro.db ── new final slots, every 5 min ─▶ chan-lescut-macbook-pro.db (as received)     │
 │                                                  │ (the Mac's    │          H-Frank-1.db (the VM's own)             │
 │                                                  │  aggregates)  │                                                  │
 │                                                  │               │                                                  │
 │ read by: agent-statusline (the printed line),    │               │ CAT 4  a reader (FOR_READERS.md): Categories 1+3 │
 │          auto-apply (view latest)                │               │                                                  │
 └──────────────────────────────────────────────────┘               └──────────────────────────────────────────────────┘
```

## 2. Where the data lives

`~/opt/agent-usage-tracker/` on each machine. Only the gates write a database: `ingest_account_quota.py`, `ingest_session_usage.py`, `ingest_aggregates.py`.

| Cat. | File | Table | One row = | Unit | From |
|---|---|---|---|---|---|
| 1 | `data/claude/account_quotas.db` (Mac and VM, the same rows) | `account_quotas` + view `latest` | one reading, with the machine that took it | % 5h and 7d, reset times | Anthropic API, statusline payload |
| 1 | same | `poll_errors` | one failed poll (this machine only) | — | the poller |
| 2 | `data/claude/sessions_usages.db` (each machine its own) | `session_snapshots` | one session's running total, when it changes | $ (all models together), cache statistics | statusline payload |
| 2 | same | `telemetry` + view `telemetry_requests` | one telemetry event (the view: one per request) | tokens + Claude Code's $ per request | Claude Code (OTLP) |
| 2 | `~/.claude/projects/**/*.jsonl` | — | Claude Code's own transcript lines | tokens per request, `cost-state` per session | Claude Code (deletes them after 365 days) |
| — | `data/claude/intermediary.db` (local, never sent) | `requests`, `scans`, `offsets` | one request counted once; one reader run; one transcript file | tokens; "complete through" time; how far read | Category 2; deletable, rebuilt from it (§5) |
| 3 | `data/claude/aggregates/<machine>.db`: one file per machine, each machine its own; the VM also keeps the Mac's, as received | `slots` | 5-min slot × machine × folder × entrypoint × model | 6 token columns (input, output, cache read, cache write 5m / 1h / unknown) + Claude Code's $ where telemetry has it + tokens without $ | transcripts + telemetry, through `intermediary.db` |
| 4 | — | — | a reader builds it from 1 + 3 | — | — |

```
~/dev/agent-usage-tracker/          the repo (development: Mac only)
├── README.html                     the same design drawn, for Jean (the only README)
├── AGENTS.md                       rules for working on it, the vocabulary, which file answers what
├── FOR_READERS.md                  how other projects read the data
├── doc/                            design.md (this file), tables.md (every table, field by field),
│                                   upstream.md (what exists upstream), changes.md (what changed, when)
├── TODO/                           open work, one file per task; done/ for finished ones
├── src/                            gates, collectors, usage_db.py; launchd/, systemd/ trigger templates
├── jobs/                           job-runner entrypoints <job>.sh and their dashboard settings <job>.conf
├── install.sh, uninstall.sh
├── adhoc_quotas_analysis/          research into what the percent means; the hand-run migrations
└── tests/                          hermetic bash suite

~/opt/agent-usage-tracker/          deployed by install.sh (Mac and VM)
├── src/                            the code
├── <job>.sh, <job>.conf            claude-quota, aggregates (both machines); codex-quota, codex-plan-history (Mac)
├── data/claude/                    account_quotas.db (1), sessions_usages.db (2), intermediary.db (local, deletable)
│   └── aggregates/                 <machine>.db (3): this machine's own; on the VM also chan-lescut-macbook-pro.db
├── data/codex/                     Codex readings and turns (§13)
├── data/_archive/                  copies kept by the migrations (the old push copies included), and their log
├── state/                          sync/claude_quotas.json (Mac), push/watermarks.json (Mac), transcript_reader/ (Codex offsets)
├── logs/                           otel-receiver.{log,err}; statusline_reader.err (the reader's last failure)
└── com.jeanlescut.agent-usage-tracker.*.plist / .service / .timer   trigger files, symlinked from the OS folders
```

## 3. Category 1 · Quotas

### 3.1 Table `account_quotas` (and `poll_errors`)

| Column | Meaning |
|---|---|
| `ts`, `dt` | when the reading was true: the poll's time, or the statusline session's last API response (`observed_at`) |
| `source` | `claude_api` (the poller) or `claude_statusline` |
| `machine` | the machine that took it (P1); NULL for 72 old readings `--peer` had copied to both machines, whose origin is unknown |
| `five_hour_pct`, `five_hour_resets_ts`, `seven_day_pct`, `seven_day_resets_ts` | the two windows: percent and reset time |
| `observed_by_session`, `written_ts`, `raw`, `row_hash` | the statusline session; when written; the reading as received (with `"machine"` inside); its identity |

- **Dedup**: the gate drops only a re-render (same session, `observed_at`, percents and resets). A new observation with the same percent is kept.
- **`poll_errors` stays on its machine** (P2): a 429 or a 401 concerns one machine's login token.

### 3.2 View `latest` and the reset rule (P5)

`latest` = the freshest reading, any source, any machine, with the reset rule applied when read, for each window separately:

```
pct = 0 and resets_ts = NULL     when resets_ts <= now       (the window has reset, no reading since)
otherwise the reading's own values
```

| Time | Stored | `latest` gives |
|---|---|---|
| 10:00 | a reading: 5h 50%, resets 12:00; 7d 20%, resets Monday 19:00 | 5h 50% · 7d 20% |
| 12:30, no newer reading | nothing new | **5h 0%, no reset time** · 7d 20% |

Columns: `ts, dt, source, machine, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts`. Only real observations are stored: no predicted 0% rows.

### 3.3 The sync (P3)

The Mac's `claude-quota` job runs it when the last successful sync is 110 s old or more (about every 2 min, with a 60 s tick). One ssh call does both directions, because the VM cannot reach the Mac:

```
Mac                                                          VM
claude_quota_api_poller.py --sync-with=H-Frank-1
  stdin  ─▶ the Mac's own new rows (machine = Mac, rowid > M)  ─▶  ingest_account_quota.py --sync-from=chan-lescut-macbook-pro --after=V
                                                                     1. stores each row through the gate
  stdout ◀─ the VM's own new rows (machine = VM, rowid > V)    ◀─    2. prints its own rows with rowid > V, then "rowid <max>"
  stores them through the local gate; saves M and V in state/sync/claude_quotas.json
```

- Only a machine's **own** rows travel, never echoed back; a re-sent row is a no-op (`row_hash`).
- The two watermarks advance only after both sides succeeded; at most 20,000 rows per call, a backlog drains over the next runs.
- VM unreachable: the job goes on and polls if needed; its last line says `H-Frank-1 not reached`. It fails only when the last successful sync is more than 30 min old.

### 3.4 Who polls (P4)

Each `claude-quota` run (tick 60 s, both machines): the sync first (Mac, when due), then **age** = now − the newest reading's `ts` in the local `account_quotas.db`. Poll `GET /api/oauth/usage` only if the age reaches the threshold:

| Machine | A status line on screen here (heartbeat < 90 s) | Idle |
|---|---|---|
| Mac | 110 s | 300 s |
| VM | 290 s = 110 + 120 (sync) + 60 (tick) | 480 s = 300 + 180 |

| Situation | Mac | VM |
|---|---|---|
| a Claude session active anywhere | no poll: statusline readings are fresh | no poll |
| everything idle, Mac awake | **polls** | no: the Mac's reading arrives within 2 min |
| Mac asleep | — | **polls**, if its login token is valid (renewed only by Claude Code running on the VM) |
| the VM's token expired (401) | polls | fails, harmless |
| 429 on one machine | only that machine waits (its Retry-After) | |

Idle, both poll less often: the reset rule (§3.2) covers the window ends.

## 4. Category 2 · Machine raw

| Store | Tables | Written by |
|---|---|---|
| `data/claude/sessions_usages.db` | `session_snapshots` (statusline: session cost, cache statistics), `telemetry` (one row per event, Claude Code's own $), view `telemetry_requests` | `ingest_session_usage.py`, from `statusline_payload_reader.py` and `telemetry_receiver.py` (service `otel`) |
| `~/.claude/projects/**/*.jsonl` | Claude Code's transcripts | Claude Code; deleted after 365 days |

Never sent anywhere. No $ computed by us: the only $ is Claude Code's own (telemetry, snapshots).

## 5. intermediary (P7, P13)

`~/opt/agent-usage-tracker/data/claude/intermediary.db`, on each machine, written only by `transcript_reader.py`:

| Table | One row = | Columns |
|---|---|---|
| `requests` | one API request, counted once (the transcript line with the largest `output_tokens` per request id) | `ts` (its first line), `dt`, `request_id` (UNIQUE), `session_id`, `folder` (cwd), `entrypoint`, `model`, the 5 token columns |
| `scans` | one run of the reader | `ts`, `dt`, `complete_through_ts` (= run time − 15 min), `requests_added` |
| `offsets` | one transcript file | `path`, `inode`, `byte_offset`: how far it was read; saved in the same commit as the requests it gave |

**Deleting `intermediary.db` is always safe** (with its `-wal` and `-shm` files): the next run re-reads every transcript (one slow run, about 1 GB on the Mac) and rebuilds the same rows; slots already written are not touched.

## 6. Category 3 · Aggregates

### 6.1 Table `slots` (P8)

| Column | Type | Meaning |
|---|---|---|
| `ts`, `dt` | INTEGER, TEXT | slot start, a multiple of 300 s |
| `machine` | TEXT NOT NULL | where the requests ran |
| `folder` | TEXT NOT NULL | the session's working directory; `''` when unknown |
| `entrypoint` | TEXT NOT NULL | as Claude Code wrote it: `cli` (interactive), `sdk-cli` (`claude -p`), `sdk-py` (Agent SDK), …; `''` when unknown |
| `model` | TEXT NOT NULL | e.g. `claude-opus-5-5` |
| `requests` | INTEGER | number of requests |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens` | INTEGER | sums, from the transcripts |
| `cache_write_unknown_tokens` | INTEGER | cache writes of telemetry-only requests (telemetry gives no 5m / 1h split) |
| `usd` | REAL, NULL allowed | sum of Claude Code's own `cost_usd` over the requests that have one; NULL when none has |
| `tokens_without_usd` | INTEGER | all tokens of the requests with no telemetry: the part `usd` does not cover |
| `written_ts` | INTEGER | when the row was written |

Key: UNIQUE (`ts`, `machine`, `folder`, `entrypoint`, `model`). Written once, when final; never changed. No percent, ever. Example, two real requests:

```
ts 2026-10-10T19:10:00Z · chan-lescut-macbook-pro · /Users/jeanlescut/dev/trading-bot · cli · claude-opus-5-5
requests 2 · in 6 · out 351 · cache read 643,761 · write 5m 0 · write 1h 724 · write unknown 0 · usd 0.1416 (0.0686 + 0.0730) · without usd 0
```

### 6.2 How a slot is built (job `aggregates`, every 5 min)

```
1. transcript_reader.py: new transcript lines → intermediary requests, + a scan: complete_through = now − 15 min
2. every slot that is final and not yet written:
     transcript requests ── LEFT JOIN telemetry_requests ON request_id ──▶ tokens + usd (or tokens_without_usd)
   + telemetry-only requests (prompt suggestions, summaries, web search, titles, compaction: 9.2% of the $)
       placed by their event time; folder and entrypoint from the same session's transcript requests;
       their cache writes → cache_write_unknown_tokens
3. sum per (slot, machine, folder, entrypoint, model) → ingest_aggregates.py → data/claude/aggregates/<this machine>.db
4. Mac only: send the new slots to the VM (§6.4)
```

- A request belongs to the slot of its first transcript line; a telemetry-only request, to the slot of its event.
- **Final** = the slot ended at or before the latest scan's `complete_through_ts`. All lines of one request are written within 292 s, and telemetry arrives within 556 s (§12), so 15 min is enough.

| Mac asleep 22:00 → 08:00 | |
|---|---|
| 22:00 → 08:00 | nothing written, nothing sent |
| first run at 08:00 | every slot up to 07:45 written and sent |

### 6.3 The gate `ingest_aggregates.py` (P10, P14)

The only writer of `data/claude/aggregates/<machine>.db`: checks each row (columns, types, no percent, and `machine` = the file's machine), then `INSERT OR IGNORE` on the key. Used by step 3 locally (this machine's file), and on the VM by the receiver (`--machine NAME` writes `NAME.db`, rejecting any row of another machine).

### 6.4 Sending to the VM (P9, P14)

`push_to_central.py` sends the slots not sent yet (rowid watermark in `state/push/watermarks.json`, chunks of 20,000, ssh; the watermark advances only on success). `receive_from_machine.py` on the VM accepts only `slots` and writes `data/claude/aggregates/chan-lescut-macbook-pro.db` through the gate; one bad row refuses its whole chunk (the job fails, nothing goes silently missing). A slot reaches the VM within 5 min of being final while the Mac is online; a Mac asleep sends everything on its next run. The VM sends nothing: its slots are already there.

| Full reset (one file at a time, by hand) | Then |
|---|---|
| a machine's own `aggregates/<machine>.db` deleted | its next `aggregates` run rebuilds every slot from `intermediary.db` (and that from the transcripts, if it was deleted too) |
| the VM's copy `aggregates/chan-lescut-macbook-pro.db` deleted | the Mac's `slots` watermark in `state/push/watermarks.json` set to 0: its next run sends everything again |

The copies of the old push (raw rows, until 2026-10-10) are in `data/_archive/`, untouched.

## 7. Contracts with other projects

### 7.1 agent-statusline (P6)

The two projects share two paths, each written by one side only; each works without the other.

| Interface | Written by | Read by | What |
|---|---|---|---|
| `~/opt/agent-usage-tracker/src/statusline_payload_reader.py` | this repo; executable, `install.sh` writes its first line (this machine's python with `-S`) and its machine name | agent-statusline's Claude provider runs it on every render | takes the raw payload on stdin, stores the reading (and the session snapshot), then prints the view `latest` as one line of six `\x1c`-separated fields: `five_pct five_reset week_pct week_reset source observed_at` (percents rounded half up). Always exits 0; on any failure it prints nothing and the status line shows its own payload's reading |
| `~/opt/agent-statusline/state/heartbeat/{claude,codex}` | agent-statusline, every render | `claude-quota.sh`, `codex_quota_api_poller.py` | only the mtime: a status line is on screen (§3.4) |

Without agent-statusline there are no statusline readings and no session snapshots: Claude Code runs a single `statusLine` command. The quota then comes from the poller only.

### 7.2 auto-apply

`runner/limits.py` reads `SELECT five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts, ts FROM latest` from `account_quotas.db`, opened with `PRAGMA query_only = ON`.

### 7.3 Every other reader (P11)

`FOR_READERS.md` is the contract: open with `PRAGMA query_only = ON` (never a `mode=ro` URI), read new rows by `rowid` (synced rows arrive up to 2 min after their `ts` and `written_ts`), read the slots of every file in `data/claude/aggregates/` (one per machine), never attribute a percent to a project, machine or slot. agent-quota-maximizer gets no change: it will be decommissioned.

### 7.4 job-runner

Every scheduled step is a job-runner job, `com.jeanlescut.agent-usage-tracker.<job>`: `jobs/<job>.sh` sources `~/opt/job-runner/lib.sh`; its dashboard settings are `jobs/<job>.conf`, deployed beside it and handed to the dashboard by `install.sh`. The retired jobs `transcripts` and `push` are marked `DECOMMISSIONED=1` on the dashboard.

## 8. Jobs and services (P12)

| Job (job-runner) | Category | Every | Mac | VM | Does |
|---|---|---|---|---|---|
| `claude-quota` | 1 | tick 60 s, timeout 1 min | ✓ sync + poll if needed | ✓ poll if needed | §3.3, §3.4 |
| `otel` (service, always on) | 2 | — | ✓ | ✓ | stores Claude Code's telemetry |
| `aggregates` | 3 | 5 min, claim 5 min (no overlap) | ✓ compute + send | ✓ compute | transcripts → `intermediary.db` → final slots (§6.2); also reads the Codex session files (§13) |
| `codex-quota`, `codex-plan-history` | Codex | 60 s, 1 h | ✓ | — | §13 |
| not a job: the statusline reader | 1, 2 | every render | ✓ | ✓ | run by agent-statusline (§7.1) |

Mac: 4 jobs + 1 service. VM: 2 jobs + 1 service.

```
MacBook                                                              VM
statusline render ─▶ statusline_payload_reader.py ─▶ gates 1, 2      statusline render ─▶ the same, for the VM's sessions
claude-quota.sh ─▶ 1 sync with the VM ─▶ 2 poll if stale ─▶ gate 1   claude-quota.sh ─▶ poll if stale (longer thresholds) ─▶ gate 1
otel: telemetry_receiver.py ─▶ gate 2                                otel: telemetry_receiver.py ─▶ gate 2
aggregates.sh ─▶ 1 transcript_reader.py ─▶ intermediary.db           aggregates.sh ─▶ the same, without step 3
              ─▶ 2 aggregate_slots.py ─▶ gate 3                      receive_from_machine.py (on each Mac send) ─▶ gate 3
              ─▶ 3 push_to_central.py ─ ssh ─▶ the VM                    ─▶ aggregates/chan-lescut-macbook-pro.db
```

## 9. Status (2026-10-12)

| Component | Status | Note |
|---|---|---|
| statusline reading + printed line (Mac, VM) | LIVE | about 24 ms per render |
| Category 1 sync Mac ↔ VM | LIVE | both databases hold the same readings |
| polling only when no reading is fresh | PARTIAL | the Mac polls, the VM waits; while the Mac sleeps the VM does try (seen 2026-10-11), but its login token expired (401: Claude Code last ran there on 10-08), so no reading is taken: the accepted gap (P15, `TODO/0010`) |
| telemetry receiver `otel` | LIVE | auto-apply's Agent SDK sessions send none: their slots carry tokens without $ (`TODO/0004`) |
| Category 3 `slots` (Mac, VM) | LIVE | history since 2026-07-30; `usd` since 2026-09-30 |
| the Mac's slots on the VM | LIVE | `data/claude/aggregates/chan-lescut-macbook-pro.db`, the same rows as the Mac's |
| P13 `data/claude/intermediary.db`, P14 one aggregates file per machine | LIVE | since 2026-10-12 18:45 UTC; the slots still equal a separate count of every request (Mac 3,331, VM 41) |
| readers: agent-statusline, auto-apply | LIVE | the printed line; the view `latest` |
| Category 4 | PLANNED | a reader's (`FOR_READERS.md`); this repo's own analysis: `adhoc_quotas_analysis/` |
| Codex: `codex-quota`, `codex-plan-history`, its turns (Mac) | LIVE | outside the four categories; its rows no longer reach the VM (§13) |

## 10. Install, tests, logs, docs

```
bash install.sh      # needs python3 and job-runner (~/opt/job-runner/lib.sh); LaunchAgents on macOS, systemd --user on Linux
bash tests/run.sh    # hermetic
```

VM deploy: rsync the repo to `~/dev/agent-usage-tracker` there, run its `install.sh`; never edit code on the VM. Never `uninstall.sh` for a layout change: a hand-run step of `adhoc_quotas_analysis/migrate_to_target.py` instead.

| Something looks wrong in… | Look at (on that machine) |
|---|---|
| any job | job-runner's dashboard `http://h-frank-1:8097`; each run's output in `~/opt/job-runner/logs/agent-usage-tracker.<job>/<machine>/`, its statuses in `~/opt/job-runner/status/agent-usage-tracker.<job>/<machine>.tsv` |
| the quota on the status line | `logs/statusline_reader.err` (the reader's last failure; absent if it never failed), then `SELECT * FROM latest` |
| the sync | the Mac's `claude-quota` last line (`sync ↑n ↓n` or `H-Frank-1 not reached`); `state/sync/claude_quotas.json` |
| the slots, or their copy on the VM | `python3 adhoc_quotas_analysis/migrate_to_target.py check-intermediary`; the Mac's `aggregates` last line (`sent n to H-Frank-1`); `state/push/watermarks.json` |
| telemetry | `logs/otel-receiver.{log,err}`; `launchctl print gui/$(id -u)/com.jeanlescut.agent-usage-tracker.otel` (VM: `systemctl --user status`) |
| a migration step | `data/_archive/migrate_to_target.log` |

| File | For |
|---|---|
| `FOR_READERS.md` | how another project reads the data: the rules, which table answers which question |
| `doc/tables.md` | every table, field by field: which collector fills it from which upstream source, the traps in the rows |
| `doc/upstream.md` | what usage data exists upstream, Claude and Codex, unit and granularity, captured or not |
| `doc/changes.md` | every change to what is captured, dated |
| `adhoc_quotas_analysis/` | what the quota percent means (`CONCLUSIONS.md`; drawn: its `README.html`) |

Split out of `agent-statusline` on 2026-09-30: its git history up to `d744951` holds this code from 2026-08-31; older history (the original `agent-quota-tracker`) is below this repo's first commit.

## 11. Decisions

Proposed by the agent, each validated by Jean: P1–P12 and P15 on 2026-10-10, P13–P14 on 2026-10-12; the unnumbered ones decided with Jean along the way.

| # | Decision | Why | Rejected |
|---|---|---|---|
| P1 | a reading carries `machine`, in its raw JSON (so in `row_hash`) and a column | the same reading taken on two machines never collides; a synced row keeps its origin | labelling old rows by the database holding them: wrong for the 72 `--peer` copies (NULL instead) |
| P2 | `poll_errors` never synced | a 429 or a 401 is about one machine's login token | — |
| P3 | the Mac syncs, the VM is passive; both ways in one ssh call, every 2 min | the VM cannot reach the Mac (NAT, sleep) | `--peer` (one-way copies); the push of whole databases (128 MB for the first sync) |
| P4 | thresholds Mac 110 / 300 s, VM 290 / 480 s | the Mac polls first; the VM only once the Mac's reading could not have arrived | — |
| P5 | the reset rule in the view `latest`, applied when read | the database keeps only observations | predicted 0% rows; a state file `state/quota/claude` |
| P6 | the reader is executable and prints the line itself | one process per render (about 24 ms) | a `bin/` wrapper + a state file read afterwards |
| P7 | the intermediary tables local and deletable | everything in it is rebuilt from Category 2 | `requests` kept in `sessions_usages.db` with a $ computed by us |
| P8 | `cache_write_unknown_tokens` | telemetry gives no 5m / 1h split (6% of cache writes) | — |
| P9 | the old push copies moved whole to an archive, untouched | that move is the backup | — |
| P10 | one gate, `ingest_aggregates.py`, for every aggregates file | the same checks for the Mac's own file and the VM's copy | — |
| P11 | readers read new rows by `rowid` | synced rows arrive up to 2 min after their `ts` | by `ts` or `written_ts`: misses late rows |
| P12 | one job `aggregates` | reading, building and sending under one claim: two runs would read the same bytes | the jobs `transcripts` and `push` |
| P13 | the intermediary tables in one file, `data/claude/intermediary.db`, with the offsets as a table (Jean, 2026-10-12) | everything a machine keeps lives under `data/<agent>/`; one file to delete for a rebuild; the offsets commit with their requests | a folder `intermediary/claude/` with `requests.db` + `offsets.json` |
| P14 | Category 3 as one file per machine, `data/claude/aggregates/<machine>.db`, on the VM too (Jean, 2026-10-12) | a full reset of one machine's slots is deleting one file; one writer machine per file | the folder `central/<machine>/` (left from the push of whole databases); one shared file with a `machine` column (a reset would need a `DELETE`: insert-only) |
| P15 | no Claude reading while Claude Code runs on neither machine for more than 8 h: accepted (Jean, 2026-10-10). The poller borrows each machine's login token, which only Claude Code renews (8 h; the refresh token, weeks: then only a new login helps). Usage made meanwhile elsewhere shows up in the first reading afterwards, as a total; *when* is lost, and readers treat the stretch as unknown | any message opens a 5-hour window at an arbitrary time (`doc/upstream.md` §5.2) | a keepalive message every few hours; not tried yet: a command that sends no message (`TODO/0010`) |
| — | `slots` is one table, no `session_id` | sessions are a Category 2 detail; readers need slots | a `session_totals` table |
| — | Category 4 belongs to a reader | each reader asks its own question | Category 4 built here every 5 min |
| — | no $ computed for Claude | Claude Code states its own $ | tokens × list price |
| — | Codex out of this design (§13) | Claude first; Codex's sources differ | — |

## 12. Facts measured for this design (2026-10-10)

| Fact | Value |
|---|---|
| Telemetry lateness (`received_ts − ts`, 8,090 `api_request` events) | ≤ 60 s: 8,089; max 556 s; > 15 min: 0 |
| Lines of one request in a transcript | all written within 292 s |
| Telemetry-only requests | 1,228 of 8,091 (9.2% of the $); every one in a session that also has transcript requests |
| Telemetry attributes | no working directory, no 5m / 1h cache split; has `request_id`, `session.id`, `model`, `query_source` |
| Transcript lines per request (Mac) | 47,204 lines, 22,617 requests: 2.1 per request |
| Readings copied to both machines by `--peer` | 72, now `machine` NULL |
| A 429 is per login token | the Mac polled fine 27 times during a VM Retry-After of 3,600 s |
| Oldest transcript (Mac and VM) | 2026-07-30 |
| The slots after the migration | equal to a separate count of every request: Mac 24,004, VM 189 |

## 13. Not decided: Codex

Codex runs on the Mac only (not installed on the VM) and keeps its pre-design jobs; nothing in this design may break them. Picking it up is `TODO/0007`.

```
job codex-quota          tick 1 min ─▶ codex_quota_api_poller.py ◀─ codex app-server (account/rateLimits/read, account/usage/read)
job codex-plan-history   tick 1 h   ─▶ codex_plan_history_poller.py ◀─ ChatGPT backend (usage/plan_limit_history), once a day
                                         └─▶ ingest_account_quota.py ─▶ data/codex/account_quotas.db
job aggregates (Codex part)  5 min  ─▶ transcript_reader.py ◀─ ~/.codex/sessions, ~/.codex/archived_sessions
                                         └─▶ ingest_session_usage.py ─▶ data/codex/sessions_usages.db (requests, scans)
```

| Piece | Notes |
|---|---|
| `codex-quota` | skips while a Codex session file is fresher than 5 min (the session itself writes the quota); exit 10 = skip |
| `codex-plan-history` | once per 24 h after a success, 1 h after a failure; skips without `~/.codex/auth.json` |
| Codex turns | entrypoint = `session_meta.source` or `subagent`; match ccusage's monthly totals (`doc/upstream.md` §4.1) |
| `requests.usd` for Codex | from `usage_db.PRICES` (ccusage's $4 / $20 / cached 0.1× for `gpt-5.6-sol`), NULL for other models: not a number OpenAI states |

What this design changed for Codex: its rows no longer reach the VM (the push of raw rows is gone), and `codex/account_quotas.db` stays on the Mac.

| Category | Codex, sketch |
|---|---|
| 1 · Quotas | `codex/account_quotas.db` in the same sync. Codex reset times move and it reports a fake idle countdown (`doc/upstream.md` §5.1, §5.3): the reset rule needs care |
| 2 · Machine raw | the session files only: no statusline payload captured, no telemetry |
| 3 · Aggregates | the same `slots` shape, tokens only (OpenAI states no $); then drop `requests.usd` |
| 4 · Meta-analysis | a reader's, with Codex rows |

Open for that day: Codex session-file retention; usage no local file sees (cloud tasks, IDE extension, ChatGPT app), visible only account-wide (`doc/upstream.md` §4.3, §4.4); `codex-auto-review` turns (ccusage calls them `gpt-5.6-luna`: keep Codex's own name); Codex on the VM or Mac-only.
