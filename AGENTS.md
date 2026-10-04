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
│   └── ingest-claude-statusline.sh  # the statusline's entry point: raw payload on stdin -> account row, session row, state/quota/claude (source "statusline")
├── src/
│   ├── quota_polling/            # LaunchAgent-scheduled, deployed, unattended
│   │   ├── poll_all.py                          # the LaunchAgent entry point, runs each poller as a subprocess
│   │   ├── poll_claude.py / poll_codex.py       # per-agent meter pollers
│   │   ├── poll_codex_plan_history.py           # daily Codex plan_limit_history fetch (fractional per-window history)
│   │   ├── _quota_common.py                     # log tail, heartbeat freshness, state-file writer (mirrors the ingest script's)
│   │   └── com.jeanlescut.agent-usage-tracker.plist.template
│   └── telemetry/                # local OTLP receiver + its KeepAlive LaunchAgent; merge_claude_env.py owns the telemetry keys in ~/.claude/settings.json's `env`
├── adhoc_quotas_analysis/        # research, run by hand, never deployed - see its own AGENTS.md (the deep-dive: investigation, findings, gotchas, naming history)
├── tests/                        # hermetic bash test suite, see tests/README.md
└── TODO.md                       # parked gaps
```

## Rules

- **Percent is account-scope only.** A session file (`data/<agent>/<session-id>.jsonl`) never carries a quota percent under any name; an account row never carries per-session cost or tokens. `USAGE_DATA_REFERENCE.md` §1 states it for downstream readers. Nothing here estimates a per-session percent.
- **Never rewrite history.** The data files are append-only raw material. A layout change moves files and, if rows must change, uses a one-time, idempotent, hand-run migration in `adhoc_quotas_analysis/` that archives the originals (`split_by_scope.py` is the pattern).
- **Keep the contract with agent-statusline small** (README.md's "Contract with agent-statusline"): the ingest script takes the raw payload and picks its own fields; it must always exit 0 and never print anything the statusline would need. The state file's six-field format and the heartbeat path are shared with that repo - changing either is a change in both. Nothing here sources or runs agent-statusline code (enforced by `tests/test_repo_hygiene.sh`).
- **Writers append one line per `write()`** with `O_APPEND`, so concurrent writers (every open Claude session, the pollers, the receiver) never interleave.
- **Every doc change to what is captured goes into `USAGE_DATA_REFERENCE.md`**, with a dated change-history row (§6) and its "Last verified" line updated.

## Install/uninstall

`install.sh` assumes a bare machine and carries no one-time migration logic. For a layout change: `uninstall.sh` (removes what `install.sh` deploys, preserves `data/`, flags anything else as an orphan), resolve orphans, `install.sh`. Don't add a migration guard to `install.sh` instead.

## Deployment

MacBook only (LaunchAgents). `~/opt/agent-usage-tracker/` holds the deployed code, its data and state, and the real plist files; `~/Library/LaunchAgents/` holds symlinks only. `adhoc_quotas_analysis/` and `src/telemetry/merge_claude_env.py` run from the `~/dev` checkout.

## Tests

`bash tests/run.sh` before committing a change to `bin/`, `src/`, `adhoc_quotas_analysis/*.py`, `install.sh` or `uninstall.sh`. Hermetic: temp `$HOME`, fixture transcripts, monkeypatched `urlopen`, `AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1`. Never touches the real `~/.claude`, `~/.codex`, Keychain or network. Deliberately untested: the real Keychain read and live API calls (`poll_claude.py`'s `fetch_token`, the pollers' real requests) - re-verify those live per `USAGE_DATA_REFERENCE.md` §8.
