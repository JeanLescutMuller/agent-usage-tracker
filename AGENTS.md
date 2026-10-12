# agent-usage-tracker development instructions

For *working on* this repo; what it does and how: `design/DESIGN.md`.

## Repo layout

```
agent-usage-tracker/
├── install.sh / uninstall.sh     # bare-machine installer and its inverse (see below)
├── USAGE_DATA_SOURCES.md         # CANONICAL: what usage data exists upstream
├── USAGE_DATA_REFERENCE.md       # CANONICAL: what this repo captures, how, where
├── FOR_READERS.md                # how other projects read the data
├── README.html                   # the design, for Jean (the only README)
├── design/DESIGN.md              # the same design, for agents
├── TODO/                         # open work, one file per task
├── src/                          # deployed flat to ~/opt/agent-usage-tracker/src/, except merge_claude_env.py
│   ├── usage_db.py               # paths, schemas, connect(), Codex price table
│   ├── ingest_account_quota.py   # GATE of account_quotas.db (Category 1); also the VM side of the 2-min sync
│   ├── ingest_session_usage.py   # GATE of sessions_usages.db (Category 2)
│   ├── ingest_aggregates.py      # GATE of aggregates/<machine>.db (Category 3)
│   ├── statusline_payload_reader.py  # run by agent-statusline: stores the payload's reading, prints `latest`
│   ├── claude_quota_api_poller.py    # job claude-quota: the sync (Mac), then a poll if no reading is fresh
│   ├── codex_*_poller.py, telemetry_receiver.py  # Codex pollers; the local OTLP receiver (a service)
│   ├── transcript_reader.py + aggregate_slots.py # job aggregates: transcripts -> intermediary.db -> final 5-min slots
│   ├── push_to_central.py / receive_from_machine.py  # the Mac's slots -> the VM
│   ├── merge_claude_env.py       # run from the repo: telemetry keys in ~/.claude/settings.json
│   └── launchd/, systemd/        # trigger templates (Mac, VM)
├── jobs/                         # job-runner entrypoints: claude-quota, aggregates, codex-quota, codex-plan-history (Mac)
├── adhoc_quotas_analysis/        # research and hand-run migrations, never deployed (its own AGENTS.md)
└── tests/                        # hermetic bash suite (tests/README.md)
```

## Rules

- **A repeated quota reading is kept, not dropped.** A statusline reading is dated by `observed_at` (its session's last API response); the account gate drops only a re-render (same session, `observed_at`, percents and resets). Check `USAGE_DATA_REFERENCE.md` §2 before describing dedup or dating.
- **Percent is account-scope only**: never in `sessions_usages.db` or the aggregates files (the gates reject a percent-like key); no cost or tokens in `account_quotas.db`.
- **Only the gates write** under `data/`. Exception: `transcript_reader.py` writes `data/claude/intermediary.db` (local, deletable). Other data-changing SQL fails `tests/test_repo_hygiene.sh`.
- **Never rewrite history.** Insert-only; `row_hash` / `request_id` / the slot key make a re-insert a no-op. A layout change is a one-time, idempotent, hand-run migration step that archives first (`adhoc_quotas_analysis/migrate_to_target.py`).
- **No $ computed for Claude**: only Claude Code's own (telemetry, snapshots).
- **Keep the contract with agent-statusline small**: the reader's path, its six-field line and the heartbeat path are shared; auto-apply reads `latest`. Changing one is a change in every repo. Nothing here runs agent-statusline code.
- **Readers open with `PRAGMA query_only = ON`**, never a `mode=ro` URI; new rows by `rowid` (`FOR_READERS.md`).
- **Every change to what is captured** gets a dated row in `USAGE_DATA_REFERENCE.md` §6 and its "Last verified" line.
- **`design/DESIGN.md` and `README.html` change together.**

## Vocabulary

| Word | Means | Never say |
|---|---|---|
| **Category 1 · Quotas** | the account's quota readings, raw; the same rows on both machines | — |
| **Category 2 · Machine raw** | `sessions_usages.db` + Claude Code's transcripts; never leaves its machine | — |
| **Category 3 · Aggregates** | `data/claude/aggregates/<machine>.db`, one file per machine, one table `slots`; the Mac's sent to the VM | summaries, rollups |
| **Category 4 · Meta-analysis** | anything computed from 1 + 3: a reader's, not this project's | — |
| **reading** | one quota observation: 5-hour and 7-day percent with their reset times | sample, snapshot (that is a session's cost row) |
| **window** | one quota meter, 5-hour or 7-day | envelope, bucket |
| **5-min slot** | a 5-minute interval starting at a multiple of 300 s; one `slots` row per slot × machine × folder × entrypoint × model | bin, bucket |
| **final slot** | a slot every request of which is counted: it ended before the last transcript read's "complete through" (run time − 15 min) | closed, done |
| **intermediary** | `data/claude/intermediary.db`: the local working tables between Category 2 and 3; never sent, deletable | cache, staging |
| **sync** | the Mac's 2-min exchange of each machine's own readings with the VM, both ways | push, replication |
| **send** | the Mac's `aggregates` job copying its new slots to the VM | push (the old job that copied every row) |
| **gate** | the only code that writes a database (`ingest_*.py`) | writer, ingester |
| **reset rule** | a window whose reset time has passed reads 0%, in the view `latest` | — |
| **reader** | any other project reading this data (`FOR_READERS.md`) | consumer, client |

## Install, deploy

`install.sh` assumes a bare machine, no migration logic. **Never use `uninstall.sh` for a layout change**: it stops the telemetry receiver (Claude Code drops what it can't deliver), removes the telemetry settings and deletes `state/` and `intermediary.db`. Retire a job with `migrate_to_target.py remove-trigger JOB`.

Mac (LaunchAgents) and the VM `H-Frank-1` (systemd `--user`): every scheduled step is a job-runner job, `com.jeanlescut.agent-usage-tracker.<job>`. VM deploy: rsync this repo to `~/dev/agent-usage-tracker` there, run `install.sh`; never edit code on the VM.

## Tests

`bash tests/run.sh` before committing a change to `src/`, `jobs/`, `adhoc_quotas_analysis/*.py`, `install.sh` or `uninstall.sh`. Hermetic. **Every python call in a test points at a temp runtime** (`AGENT_USAGE_TRACKER_RUNTIME` or `--runtime`): on 2026-10-10 one did not, and wrote into the real database. Untested: the real Keychain read and live API calls (re-verify per `USAGE_DATA_REFERENCE.md` §8).
