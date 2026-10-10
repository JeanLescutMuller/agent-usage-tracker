# Migration plan: from today's design to `design/TARGET_SPEC.md`

Status: **validated by the user on 2026-10-10; not started.** The target is `design/TARGET_SPEC.md` (validated 2026-10-10, proposals P1–P12); its section 11 lists the data to move. This file says in which order, how each step is checked, and how each is undone. Claude only; Codex keeps running as today, except that its rows stop reaching the VM in phase 3 (accepted, `design/CODEX_DESIGN.md`).

## Rules for every phase

| Rule | Why |
|---|---|
| **Category 1 writers never stop for more than seconds.** The statusline reader writes on every render throughout; a job is paused only when the other machine keeps polling | a quota reading not taken is lost for good (the API has no history) |
| **The telemetry receiver is never stopped by hand.** `install.sh` restarts it (≈ 1 s, as on every deploy today); `uninstall.sh` is **not** used: it stops the receiver, removes the telemetry settings from `~/.claude/settings.json` and deletes all of `state/` | Claude Code drops telemetry it cannot deliver |
| Old jobs are removed by a migration step that unloads only their trigger | the repo's usual `uninstall.sh` + `install.sh` would break the two rules above |
| Every data change is a one-time, idempotent, hand-run step of `adhoc_quotas_analysis/migrate_to_target.py` (pattern of `merge_machine_names.py`): it archives what it changes first, prints counts before/after, logs to `<runtime>/data/_archive/migrate_to_target.log` (the VM's `~/dev` checkout is overwritten by every rsync) | "never rewrite history"; re-running a step changes nothing |
| An existing database gets a schema change only through that script, which also sets `PRAGMA user_version = 2` | `usage_db.connect()` applies the schema only to a new database |
| Each phase = one commit, `bash tests/run.sh` green, deployed **Mac first, then the VM** (`install.sh`; VM: rsync + `install.sh`), checked, before the next phase | a problem stays one phase wide |
| A phase is undone by: redeploying the previous commit (`install.sh`), and restoring the archive the phase's steps wrote | each phase lists its own undo |

## The phases

```
 0 Prepare ──▶ 1 Category 1 ──▶ 2 Category 3 ──▶ 3 Send slots, ──▶ 4 Readers of ──▶ 5 Clean up
   backups,      machine column,  intermediary/,   retire push,      the quota:       drop old tables,
   code that     VM seeded,       slots,           archive old       statusline,      docs, job-runner
   tolerates     sync replaces    job aggregates   central/          auto-apply;      dashboard
   both schemas  --peer           (push still on)                    bin/ and state
                                                                     file removed
 ◀── same day: 1 → 3 ───────────────────────────────▶
```

Phases 1 to 3 are best done the same day: between them the VM receives the Mac's rows by two paths (push, then sync), both harmless, but the fewer days in between, the fewer things to watch.

## Phase 0 · Prepare (no change in behaviour)

**Done 2026-10-10** (commit below): backups `data/_archive/pre-target-20261010T210110Z.tar.gz` (Mac, 4 databases, 30.6 MB) and `pre-target-20261010T210128Z.tar.gz` (VM, 10 databases, 31.1 MB), every copy `integrity_check` ok; deployed Mac then VM; rows still written by the new gates on both (Mac 177,363 → 177,367, VM 548 → 549 by its own poll).

| # | Where | Step |
|---|---|---|
| 0.1 | Mac, VM | `migrate_to_target.py backup`: every database copied with SQLite's backup API (they are written meanwhile) and checked, plus the other files of `data/`, `state/`, `central/`, into `data/_archive/pre-target-<stamp>.tar.gz` |
| 0.2 | code | The gates name their columns in every `INSERT` (today `account_quotas` gets `VALUES (?,…11)`, which would fail the moment a 12th column exists) (already the case: no change) |
| 0.3 | Mac, VM | deploy |

Check: tests green; `account_quotas` keeps growing on both machines after the deploy (`SELECT COUNT(*)` twice, 2 min apart). Undo: previous commit.

## Phase 1 · Category 1: machine column, VM seeded, sync

| # | Where | Step |
|---|---|---|
| 1.1 | Mac | `migrate_to_target.py pause claude-quota`: unloads the Mac's `claude-quota` trigger, so `--peer` stops copying readings. The VM keeps polling at its own thresholds; statusline readings go on on both machines |
| 1.2 | Mac → VM | `migrate_to_target.py cat1-shared`: compares `row_hash` between the two databases; writes the list of readings present in both (60 on 2026-10-10, more by then) to `data/_archive/cat1_shared.txt` |
| 1.3 | Mac, VM | `migrate_to_target.py cat1-machine`: archive, `ALTER TABLE account_quotas ADD COLUMN machine`, fill: this machine's name, except the shared readings → NULL; `user_version = 2` |
| 1.4 | VM | `migrate_to_target.py cat1-seed-vm`: every row of `central/chan-lescut-macbook-pro/data/claude/account_quotas.db` → `data/claude/account_quotas.db` (`INSERT OR IGNORE`, `machine` = the Mac, shared → NULL) |
| 1.5 | Mac | write `state/sync/claude_quotas.json`: M = the push watermark of `claude/account_quotas` (every Mac row up to it is in the central copy, hence now in the VM's data), V = 0 (the VM's own rows, 538 today, all go to the Mac) |
| 1.6 | code | P1 (`machine` in raw and column), P3 (`--sync-with`, `--sync-from`), P4 (`--if-older-than`, thresholds in `jobs/claude-quota.sh`); `--peer` and `--latest` removed. `state/quota/claude` is still written (its readers move in phase 4) |
| 1.7 | VM, then Mac | deploy (`install.sh` reloads the Mac's `claude-quota` trigger: the pause ends) |

Checks:

| Check | Expected |
|---|---|
| Row counts before / after 1.3, each machine | identical; `machine` NULL only for the shared readings |
| VM rows of `machine` = Mac after 1.4 | = rows of the central copy − shared |
| First sync (Mac's job log) | sends the Mac rows above M, receives the VM's 538 |
| Then, every 2 min | `SELECT machine, COUNT(*) FROM account_quotas GROUP BY 1` gives the same counts on both machines, within one sync |
| `poll_errors` | still per machine, not synced |
| Who polls (dashboard, an hour) | the Mac while awake; the VM only when the Mac's newest reading is > 290 s / 480 s old |

Undo: restore the two archived `account_quotas.db` (1.3, 1.4), previous commit. Readings written meanwhile: re-inserted from the newer database by `row_hash` (the archive step keeps it).

## Phase 2 · Category 3: intermediary, slots, job `aggregates`

| # | Where | Step |
|---|---|---|
| 2.1 | code | P7 (`intermediary/`), `aggregate_slots.py`, P10 (`ingest_aggregates.py`), P8 (`slots` columns), `transcript_reader.py` writes Claude requests to `intermediary/` (no `usd`, no `machine`), `jobs/aggregates.sh` without the send step yet; Codex part unchanged |
| 2.2 | Mac, VM | `migrate_to_target.py remove-trigger transcripts` (unload + remove the job's trigger files only) |
| 2.3 | Mac, VM | deploy |
| 2.4 | Mac, VM | `migrate_to_target.py first-aggregates`: the first run by hand, timed and logged (re-reads every transcript; about 1 GB on the Mac) — so the job's 5-min timeout never meets it |
| 2.5 | Mac, VM | `migrate_to_target.py check-intermediary` (below) |

Checks:

| Check | Expected |
|---|---|
| Every Claude `request_id` of the old `sessions_usages.db` `requests` table is in `intermediary/` `requests`, same 5 token counts | 0 missing, 0 different (a missing one = a request whose transcript is gone: listed, and the plan stops there) |
| Tokens per day: `slots` vs intermediary `requests` + telemetry-only requests | equal |
| $ per day: `slots.usd` vs telemetry `cost_usd` of the requests in final slots | equal |
| Slots per run afterwards | new final slots only; never a slot written twice |
| `push` | still running; old `requests` / `scans` no longer grow |

Undo: remove `intermediary/` and `aggregates.db` (both rebuildable), previous commit, re-add the `transcripts` trigger (`install.sh` of the previous commit).

## Phase 3 · Send slots to the VM, retire push

| # | Where | Step |
|---|---|---|
| 3.1 | Mac, VM | run `push.sh` once by hand (flush), then `migrate_to_target.py remove-trigger push` |
| 3.2 | VM | `migrate_to_target.py archive-central`: move every `central/<machine>/` whole to `central/_archive/<machine>-push-era/` (P9) |
| 3.3 | code | `push_to_central.py` / `receive_from_machine.py`: `slots` only; `jobs/aggregates.sh` sends on the Mac |
| 3.4 | VM, then Mac | deploy; the Mac's first send drains every slot since 2026-07-30, in chunks of 20,000 |

Checks: the VM's `central/chan-lescut-macbook-pro/data/claude/aggregates.db` has the same row count and the same token and $ sums as the Mac's `aggregates.db` (final slots); nothing else is written under `central/`; job-runner shows no `push` run after 3.1. Undo: move the folders back from `central/_archive/`, previous commit, re-add the `push` trigger.

## Phase 4 · Readers of the quota: statusline, auto-apply; `bin/` and the state file go

| # | Where | Step |
|---|---|---|
| 4.1 | code + Mac, VM | P5: `migrate_to_target.py view-latest` recreates `latest` with the reset rule; P6: the reader prints the 6 fields and becomes executable (`install.sh` writes its first line); `bin/` still present (its output goes to `/dev/null`, so printing changes nothing yet); deploy |
| 4.2 | agent-statusline (Mac, VM) | the provider runs the reader and reads its stdout; without it, its own payload's reading (as today without the tracker); its tests; its deploy |
| 4.3 | auto-apply (Mac) | `runner/limits.py` reads `latest` (`PRAGMA query_only = ON`); its tests; its deploy |
| 4.4 | code + Mac, VM | the gate stops writing `state/quota/claude`; `bin/` removed from the repo and `install.sh`; `migrate_to_target.py remove-legacy` deletes `bin/` and `state/quota/claude` from the runtime; deploy |

Checks: on both machines every open session's status line shows the same % as `SELECT * FROM latest`; after a reset time passes with no new reading, 0% and no countdown; auto-apply's next run logs the quota it read; `grep -r state/quota/claude ~/dev` finds only history. Undo per step: previous commit of the repo concerned (4.2 and 4.3 work with the tracker before or after 4.4).

## Phase 5 · Clean up

| # | Step |
|---|---|
| 5.1 | `migrate_to_target.py drop-old-requests` (Mac, VM): archive, then drop `requests`, `scans`, `usage_requests`, `usage_5m` from `data/claude/sessions_usages.db` (Codex's untouched) |
| 5.2 | `usage_db.PRICES`: Codex entry only; `state/transcript_reader/offsets.json` removed (moved to `intermediary/` in phase 2) |
| 5.3 | Docs: `FOR_READERS.md` (section 3: "today" becomes the target), `README.md`, `USAGE_DATA_REFERENCE.md` (tree, §4 state file gone, §10, §11, §6 rows, "Last verified"), `AGENTS.md` (layout, three gates, under 600 words: concern C13), `TODO.md` |
| 5.4 | Global instructions: the "Monitoring" paragraph of `~/.claude/CLAUDE.md` and of `~/dev/bootstrap-home/files/home_AGENTS.md` still describe `bin/ingest-claude-statusline.sh` and `state/quota/claude` |
| 5.5 | job-runner: `DECOMMISSIONED=1` for `agent-usage-tracker.transcripts` and `.push`; `examples/agent-usage-tracker/` updated; dashboard deployed |
| 5.6 | `design/`: DESIGN_MAP's "target" becomes the current design (README.html later, concern C14); `OPEN_QUESTIONS.md` emptied of what is done |

## What each phase changes in other projects

| Project | Phase | Change | Deployed on |
|---|---|---|---|
| agent-statusline | 4 | run the reader, read its stdout | Mac, VM |
| auto-apply | 4 | read `latest` | Mac |
| bootstrap-home + `~/.claude/CLAUDE.md` | 5 | Monitoring paragraph | Mac (template for every machine) |
| job-runner | 5 | dashboard conf, examples | VM (dashboard) |

## Decided with the user

| # | Decision | |
|---|---|---|
| 1 | ~~Commit in the other repos?~~ **Decided 2026-10-10**: agent-statusline, auto-apply, bootstrap-home: the agent edits, runs their tests, deploys and commits (one commit per repo per phase). agent-quota-maximizer: no change (to be decommissioned); readers follow `FOR_READERS.md` | — |
| 2 | ~~job-runner?~~ **Decided 2026-10-10**: Option A too: the agent edits, tests, deploys and commits: `examples/agent-usage-tracker/` (claude-quota updated in phase 1; aggregates added, transcripts and push removed in phases 2–3), `test/examples_test.sh`, `dashboard/conf/agent-usage-tracker.{transcripts,push}.conf` = `DECOMMISSIONED=1` + `deploy/deploy.sh vm`, the README diagram; no change to `lib.sh` | — |
| 3 | ~~Pace?~~ **Decided 2026-10-10**: one phase per session, each after the user's "go"; phases 1 → 3 the same day | — |
