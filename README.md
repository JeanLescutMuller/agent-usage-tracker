# agent-usage-tracker

Records Claude Code and Codex usage over time — quota percent, tokens, spend — from every source that has no history of its own, and keeps the research into what the quota percentages mean. Personal, user-space: macOS (LaunchAgents) and Linux (systemd `--user`), with every machine's data copied to a central store on the VM `H-Frank-1`.

Split out of [`agent-statusline`](https://github.com/JeanLescutMuller/agent-statusline) on 2026-09-30. The status line stays there; this repo owns the data. Git history of this code between 2026-08-31 and the split lives in `agent-statusline` (up to `d744951`); older history (the original `agent-quota-tracker`) is below this tree's first commit.

## Usage data documentation

Two canonical files, which downstream projects (`agent-quota-maximizer`) link to instead of restating them:

- `USAGE_DATA_SOURCES.md` — **what usage data exists upstream**, independent of this repo: every Claude and Codex source (transcripts, status-line stdin, APIs, OpenTelemetry, the ChatGPT backend), each unit (quota percent / tokens / USD) at each granularity, tested live, plus what is not available anywhere.
- `USAGE_DATA_REFERENCE.md` — **what this repo captures**: which writer records what, when, from which source, into which file, with which shape, and the traps consumers must handle.

The dollar conversions both assume are derived in `adhoc_quotas_analysis/CONCLUSIONS.md`.

All usage data lives in two SQLite databases per agent, split by **scope**: `data/<agent>/account_quotas.db` holds the account-wide meter (quota percent), `data/<agent>/sessions_usages.db` holds tokens and spend per session and per request, with the execution folder. **Quota percent is account-scope only**, never in the sessions database — see `USAGE_DATA_REFERENCE.md` §1. Until 2026-10-08 the same data was in JSONL files (`account.jsonl`, `<session-id>.jsonl`); every line was migrated verbatim (`raw` column).

## Usage

```bash
bash install.sh     # idempotent; requires python3 and job-runner (~/opt/job-runner/lib.sh); LaunchAgents on macOS, systemd --user units on Linux
bash uninstall.sh   # removes what install.sh deploys; preserves data/; flags anything else left over
```

`install.sh` assumes a bare machine and carries no migration logic: to move between incompatible layouts, run `uninstall.sh`, resolve reported orphans, then `install.sh`. It also adds Claude Code's OpenTelemetry keys to the `env` object of `~/.claude/settings.json` (only those keys; `uninstall.sh` removes the ones still holding our values). They take effect for Claude sessions started afterwards.

## Collectors and gates

Each database has one **gate**, the only code that writes it: it checks every row (types, scope rule), skips repeats, and stores the row's JSON verbatim in `raw`. **Collectors** read a source and hand rows to a gate; they never open a database for writing (`tests/test_repo_hygiene.sh` enforces it).

```
COLLECTORS (src/)                           GATES (src/)               DATABASES (data/<agent>/)
claude_quota_api_poller.py ──────────────┐
codex_quota_api_poller.py ───────────────┤
codex_plan_history_poller.py ────────────┼▶ ingest_account_quota.py ─▶ account_quotas.db  (+ state/quota/claude, view latest)
                               ┌─quota───┘
statusline_payload_reader.py ──┤
                               └─session─┐
telemetry_receiver.py ───────────────────┼▶ ingest_session_usage.py ─▶ sessions_usages.db (views usage_requests, usage_5m)
transcript_reader.py (every 5 min) ──────┘
```

| Collector | Runs | Source | Hands to |
|---|---|---|---|
| `statusline_payload_reader.py` (run by `bin/ingest-claude-statusline.sh`) | Every Claude status-line render, before the display (about 28 ms) | Statusline stdin: `rate_limits`, session cost, `prompt_cache`, model | Account gate (source `claude_statusline`) and sessions gate (snapshot). Nothing when the transcript has no `assistant` entry to date the reading; repeats of the same session's reading are skipped |
| `claude_quota_api_poller.py` | Job `claude-quota` (Mac and VM, 60 s tick): polls when `state/quota/claude` is older than 110 s (Mac) / 170 s (VM) while a Claude status line is on screen, else 300 s / 360 s; a 429 waits its Retry-After on the machine that got it only (429s are counted per machine, `USAGE_DATA_SOURCES.md` §3.5). The Mac first takes the VM's fresh reading, and hands the VM its own (`--peer`) | `GET /api/oauth/usage` | Account gate (`claude_api`); a failed attempt goes to the `poll_errors` table |
| `codex_quota_api_poller.py` | Job `codex-quota` (Mac, 60 s tick); skips while a Codex session file is fresh | `codex app-server` JSON-RPC | Account gate (`codex_app_server`) |
| `codex_plan_history_poller.py` | Job `codex-plan-history` (Mac, hourly tick); one fetch a day, 1 h after a failure | ChatGPT backend `plan_limit_history` | Account gate (`codex_plan_limit_history`) |
| `telemetry_receiver.py` | Its own KeepAlive LaunchAgent on `127.0.0.1:4318`; Claude Code pushes to it | Claude Code OpenTelemetry events | Sessions gate (`telemetry` table) |
| `transcript_reader.py` | Job `transcripts`, every 5 min; incremental (byte offset per file) | `~/.claude/projects/**/*.jsonl`, `~/.codex/{sessions,archived_sessions}/**/*.jsonl` | Sessions gate (`requests` table of each agent): one row per Claude API request once it is 15 min old, one per Codex turn |
| `receive_from_machine.py` | On the VM, run over ssh by another machine's `push_to_central.py` | That machine's new rows | Both gates, into `central/<machine>/` (see "Central store on the VM") |

Details, table shapes, views and traps: `USAGE_DATA_REFERENCE.md`. Why the push path exists at all (the poll endpoint 429s ~21% of the time, and can lock out for days): `adhoc_quotas_analysis/AGENTS.md`.

## Central store on the VM

Every machine pushes its new rows to the VM `H-Frank-1` every 5 minutes; the VM keeps one copy per machine, written through the same gates. Push only: the VM never pulls (the Mac sleeps, goes offline, sits behind NAT), and a failed push is simply retried next time.

```
MacBook                                               VM H-Frank-1 (always on)
 collectors → gates → data/<agent>/*.db                collectors → gates → data/<agent>/*.db
 push_to_central.py ── new rows, ssh ────────────────▶ receive_from_machine.py → gates → central/<machine>/data/<agent>/*.db
                                                       push_to_central.py (local, no ssh) ──▶ central/H-Frank-1/data/<agent>/*.db
```

| Piece | What |
|---|---|
| `push_to_central.py` | Every 5 min on every machine: per table, the rows above the last confirmed rowid (`state/push/watermarks.json`), in chunks of 20,000, over `ssh -C`; the watermark advances only after the VM confirms |
| `receive_from_machine.py` | On the VM: hands each received row to the gates of `central/<machine>/` (history as sent; a re-sent row is a no-op; never touches `state/quota/claude`) |
| Reading everything | One folder per machine under `~/opt/agent-usage-tracker/central/` on the VM, each with the same databases and views as a local install |

Deploying to the VM (development stays on the Mac):

```bash
rsync -a --delete --exclude .git --exclude __pycache__ ./ H-Frank-1:dev/agent-usage-tracker/ && ssh H-Frank-1 'bash ~/dev/agent-usage-tracker/install.sh'
```

On the VM, there are no Codex jobs (Codex is not installed there), and the Claude poller reads the token from `~/.claude/.credentials.json`, so Claude Code must be logged in there.

## Contract with agent-statusline

The two projects share exactly three files, each written by one side only. Neither writes into the other's tree, and each works without the other. Unchanged by the 2026-10-08 move to SQLite.

| Interface | Written by | Read by | What |
|---|---|---|---|
| `~/opt/agent-usage-tracker/bin/ingest-claude-statusline.sh` | this repo (deployed) | agent-statusline's Claude provider runs it | The Claude provider pipes its raw stdin payload in, unchanged, on every render, before display. Which fields are kept, and where, is this repo's business only. Runs `statusline_payload_reader.py` in the foreground (about 28 ms), so the render that brings a new reading displays it. Always exits 0; its output is ignored. |
| `~/opt/agent-usage-tracker/state/quota/claude` | the account gate, for `claude_statusline` and `claude_api` readings | agent-statusline's Claude provider, whatever the source; also `auto-apply` (`runner/limits.py`) and this repo's `claude-quota.sh` (its age decides when to poll) | The freshest known 5h/7d reading: six `$'\034'`-separated fields, `five_pct five_reset week_pct week_reset source observed_at`, percents rounded. Each writer overwrites only if its `observed_at` is newer, so every open session converges on the account's freshest reading. `source` is information only. A push reading is dated by its transcript's last `assistant` entry and is never written undated, so an idle session's frozen reading can't win. |
| `~/opt/agent-statusline/state/heartbeat/{claude,codex}` | agent-statusline, every render | `claude-quota.sh`, `codex_quota_api_poller.py` | Only the mtime matters: a status line is on screen, so poll faster. Missing = idle cadence. |

Without agent-statusline there are no push rows: Claude Code runs a single `statusLine` command, so nothing else can feed the ingest script. Claude's meter history then comes only from the poller, on its ~5 min idle cadence and subject to its 429s, and there are no session cost or `prompt_cache` rows. Codex is unaffected. What the status line shows without this repo is agent-statusline's business.

## Runtime layout

    ~/opt/agent-usage-tracker/
    ├── bin/ingest-claude-statusline.sh   the statusline's entry point (see the contract above)
    ├── {claude-quota,codex-quota,codex-plan-history,transcripts,push}.sh   the jobs' job-runner entrypoints (from jobs/; Linux: no Codex jobs)
    ├── src/                              usage_db.py, the two ingest_*.py gates, the collectors
    ├── data/                             see USAGE_DATA_REFERENCE.md §1
    │   ├── claude/
    │   │   ├── account_quotas.db         account scope: Claude readings (quota percent), failed poll attempts
    │   │   └── sessions_usages.db        session scope: requests, telemetry events, session snapshots (never a percent)
    │   ├── codex/
    │   │   ├── account_quotas.db         account scope: Codex readings and plan-limit history
    │   │   └── sessions_usages.db        session scope: Codex turns (requests)
    │   └── _archive/                     originals kept by one-time migrations, incl. pre-sqlite-20261008T090432Z/ (every JSONL file) and its .tar.gz
    ├── state/
    │   ├── quota/claude                  latest Claude reading (see the contract above)
    │   ├── transcript_reader/offsets.json  how far each transcript has been read
    │   └── push/watermarks.json            last rowid confirmed by the VM, per table
    ├── central/<machine>/data/<agent>/   on the VM only: every machine's pushed copy
    ├── logs/                             otel-receiver (.log / .err); each job's checks are logged by job-runner (~/opt/job-runner/logs/agent-usage-tracker.<job>/)
    └── com.jeanlescut.agent-usage-tracker.{otel,<job>}.plist   LaunchAgents, symlinked from ~/Library/LaunchAgents/
                                          (Linux: the same names as .service/.timer, symlinked from ~/.config/systemd/user/)

`adhoc_quotas_analysis/` is run-by-hand research, never deployed: it stays in `~/dev/agent-usage-tracker` and reads `~/opt/agent-usage-tracker/data/` from there.

## Tests

```bash
bash tests/run.sh
```

Hermetic: a temp `$HOME`, fixture transcripts and payloads, monkeypatched HTTP, `AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1` to keep install/uninstall off the real launchd domain. See `tests/README.md`.
