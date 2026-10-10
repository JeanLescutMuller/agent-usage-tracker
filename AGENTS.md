# agent-usage-tracker development instructions

These instructions govern development of this repository. What it does, its runtime layout and its contract with `agent-statusline` are in `README.md`; what data it captures is in `USAGE_DATA_REFERENCE.md`. This file only covers things relevant to *working on* the project. `CLAUDE.md` is a compatibility symlink to this file.

## Repo layout

```
agent-usage-tracker/
├── install.sh / uninstall.sh     # bare-machine installer and its inverse; no migration logic (see below)
├── utils.sh                      # echo/color helpers for install.sh, uninstall.sh and tests
├── USAGE_DATA_SOURCES.md         # CANONICAL: what usage data exists upstream, in which unit, at which granularity, for both agents
├── USAGE_DATA_REFERENCE.md       # CANONICAL: what this repo captures, how, when, and where it lands
│                                 # Other projects link to these rather than restating them. Keep them tested and dated.
├── bin/
│   └── ingest-claude-statusline.sh  # the statusline's entry point (path = contract): runs src/statusline_payload_reader.py, foreground, python -S
├── src/                          # deployed flat to ~/opt/agent-usage-tracker/src/, except merge_claude_env.py
│   ├── usage_db.py                              # database paths, schema (tables + views), connect(), verified price table
│   ├── ingest_account_quota.py                  # GATE: only writer of data/<agent>/account_quotas.db (+ state/quota/claude)
│   ├── ingest_session_usage.py                  # GATE: only writer of data/<agent>/sessions_usages.db
│   ├── statusline_payload_reader.py             # collector: statusline payload -> both gates
│   ├── claude_quota_api_poller.py / codex_quota_api_poller.py / codex_plan_history_poller.py  # collectors: meter pollers
│   ├── telemetry_receiver.py                    # collector: local OTLP receiver (KeepAlive LaunchAgent)
│   ├── transcript_reader.py                     # collector: Claude transcripts + Codex session files -> requests, every 5 min
│   ├── push_to_central.py                       # every machine: new rows -> the VM over ssh, every 5 min (reads only)
│   ├── receive_from_machine.py                  # collector on the VM: pushed rows -> both gates, into central/<machine>/
│   ├── merge_claude_env.py + claude_telemetry_env.json  # run from the repo: owns the telemetry keys in ~/.claude/settings.json's `env`
│   ├── launchd/                                 # LaunchAgent templates (macOS): job.plist.template (one per job, rendered) and the receiver's
│   └── systemd/                                 # the same as systemd --user units (Linux: the VM): job.{service,timer} and the receiver's
├── jobs/                         # the jobs' job-runner entrypoints (separate project ~/dev/job-runner), deployed to ~/opt/agent-usage-tracker/<job>.sh:
│                                 # claude-quota, codex-quota, codex-plan-history (Mac only), transcripts, push; each decides when, runs one collector, records the check
├── adhoc_quotas_analysis/        # research, run by hand, never deployed - see its own AGENTS.md (the deep-dive: investigation, findings, gotchas, naming history)
├── tests/                        # hermetic bash test suite, see tests/README.md
└── TODO.md                       # parked gaps
```

## Rules

- **Percent is account-scope only.** `sessions_usages.db` never carries a quota percent under any name; `account_quotas.db` never carries per-session cost or tokens. The sessions gate rejects a percent-like key anywhere in a row. `USAGE_DATA_REFERENCE.md` §1 states it for downstream readers. Nothing here estimates a per-session percent.
- **Only the gates write.** `ingest_account_quota.py` and `ingest_session_usage.py` are the only code that inserts into a database; collectors build a row and hand it over. Data-changing SQL anywhere else under `src/` or `bin/` fails `tests/test_repo_hygiene.sh`.
- **Never rewrite history.** Every table is insert-only; `row_hash` (or `request_id`) makes re-inserting the same row a no-op. Rows keep their JSON verbatim in `raw`. A layout change uses a one-time, idempotent, hand-run migration in `adhoc_quotas_analysis/` that archives the originals (`migrate_to_sqlite.py` is the latest pattern).
- **Keep the contract with agent-statusline small** (README.md's "Contract with agent-statusline"): the ingest script takes the raw payload and picks its own fields; it must always exit 0 and never print anything the statusline would need. The state file's six-field format and the heartbeat path are shared with that repo (and the state file is also read by auto-apply and by `jobs/claude-quota.sh`) - changing either is a change in all of them. Nothing here sources or runs agent-statusline code (enforced by `tests/test_repo_hygiene.sh`).
- **Concurrent writers** (every open Claude session's statusline, the pollers, the receiver, the transcript reader) are handled by SQLite: WAL mode and a busy timeout (`usage_db.connect`). Keep each write short.
- **Readers open with `PRAGMA query_only = ON`, never a `mode=ro` URI**: macOS's system SQLite intermittently refuses a mode=ro open of a WAL database whose `-shm` file no connection holds at that instant.
- **Every doc change to what is captured goes into `USAGE_DATA_REFERENCE.md`**, with a dated change-history row (§6) and its "Last verified" line updated.

## Install/uninstall

`install.sh` assumes a bare machine and carries no one-time migration logic. For a layout change: `uninstall.sh` (removes what `install.sh` deploys, preserves `data/`, flags anything else as an orphan), resolve orphans, `install.sh`. Don't add a migration guard to `install.sh` instead.

## Deployment

MacBook (LaunchAgents) and the VM `H-Frank-1` (systemd `--user`); every scheduled collector is a job-runner job (one trigger each, `com.jeanlescut.agent-usage-tracker.<job>`, its statuses on job-runner's dashboard), the telemetry receiver a plain service. The VM is deployed by rsyncing this repo to `~/dev/agent-usage-tracker` there and running `install.sh`; never edit code on the VM. `~/opt/agent-usage-tracker/` holds the deployed code, its data and state, and the real plist / unit files; `~/Library/LaunchAgents/` and `~/.config/systemd/user/` hold symlinks only. `adhoc_quotas_analysis/` and `src/merge_claude_env.py` run from the `~/dev` checkout.

## Tests

`bash tests/run.sh` before committing a change to `bin/`, `src/`, `adhoc_quotas_analysis/*.py`, `install.sh` or `uninstall.sh`. Hermetic: temp `$HOME`, fixture transcripts, monkeypatched `urlopen`, `AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1`. Never touches the real `~/.claude`, `~/.codex`, Keychain or network. Deliberately untested: the real Keychain read and live API calls (`poll_claude.py`'s `fetch_token`, the pollers' real requests) - re-verify those live per `USAGE_DATA_REFERENCE.md` §8.
