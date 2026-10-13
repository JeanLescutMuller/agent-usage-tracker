# agent-usage-tracker development instructions

For *working on* this repo. Read only the file that answers your question:

| Question | File |
|---|---|
| What does it do, why this way, where does each file live, which job runs when? | `doc/design.md` (= `README.html` drawn, for Jean) |
| What does this table or field mean, which collector fills it, what are the traps? | `doc/tables.md` |
| What usage data exists upstream (Claude Code, Codex), captured or not? | `doc/upstream.md` |
| When did a row's shape or a path change? | `doc/changes.md` |
| How does another project read the data? | `FOR_READERS.md` (cited by other repos: keep its path) |
| What do the percents mean in $ and tokens? | `adhoc_quotas_analysis/CONCLUSIONS.md` (its own `AGENTS.md`) |
| What is left to do? | Todoist (skill `task-management`): its tasks carry `Repo: agent-usage-tracker`; [`viewer.html`](viewer.html) shows them |
| What does each test cover? | `tests/README.md` |

Code: `src/` (deployed flat to `~/opt/agent-usage-tracker/src/`; the gates are `ingest_*.py`), `jobs/` (job-runner entrypoints), `install.sh` / `uninstall.sh`; the full tree is `doc/design.md` §2.

## Rules

- **A repeated quota reading is kept, not dropped.** A statusline reading is dated by `observed_at` (its session's last API response); the account gate drops only a re-render. Check `doc/tables.md` §2.3 before describing dedup or dating.
- **Percent is account-scope only** (`doc/tables.md` §1): never in `sessions_usages.db` or the aggregates files; no cost or tokens in `account_quotas.db`.
- **Only the gates write** under `data/`. Exception: `transcript_reader.py` writes `data/claude/intermediary.db`. Other data-changing SQL fails `tests/test_repo_hygiene.sh`.
- **Never rewrite history.** Insert-only. A layout change is a one-time, idempotent, hand-run step that archives first (`adhoc_quotas_analysis/migrate_to_target.py`).
- **No $ computed for Claude**: only Claude Code's own (telemetry, snapshots).
- **Keep the contract with agent-statusline small** (`doc/design.md` §7.1): changing it is a change in every repo.
- **Readers open with `PRAGMA query_only = ON`**, never a `mode=ro` URI; new rows by `rowid` (`FOR_READERS.md`).
- **One fact, one file**: the table above says which; elsewhere, cite it by path and section instead of restating it.
- **Every change to what is captured** gets a dated row in `doc/changes.md` and the "Last verified" line of `doc/tables.md`.
- **`doc/design.md` and `README.html` change together.**

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
| **collector** | code that reads one upstream source and hands rows to a gate: the statusline reader, the pollers, the telemetry receiver, the transcript reader (`doc/tables.md` §2.1) | writer |
| **reset rule** | a window whose reset time has passed reads 0%, in the view `latest` | — |
| **reader** | any other project reading this data (`FOR_READERS.md`) | consumer, client |

## Install, deploy

`install.sh` assumes a bare machine, no migration logic. **Never use `uninstall.sh` for a layout change**: it stops the telemetry receiver (Claude Code drops what it can't deliver), removes the telemetry settings and deletes `state/` and `intermediary.db`. Retire a job with `migrate_to_target.py remove-trigger JOB`.

Mac (LaunchAgents) and the VM `H-Frank-1` (systemd `--user`): every scheduled step is a job-runner job, `com.jeanlescut.agent-usage-tracker.<job>`. VM deploy: rsync this repo to `~/dev/agent-usage-tracker` there, run `install.sh`; never edit code on the VM.

## Tests

`bash tests/run.sh` before committing a change to `src/`, `jobs/`, `adhoc_quotas_analysis/*.py`, `install.sh` or `uninstall.sh`. Hermetic. **Every python call in a test points at a temp runtime** (`AGENT_USAGE_TRACKER_RUNTIME` or `--runtime`): on 2026-10-10 one did not, and wrote into the real database. Untested: the real Keychain read and live API calls (re-verify per `doc/tables.md` §9).
