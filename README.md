# agent-usage-tracker

Records Claude Code and Codex usage over time — quota percent, tokens, spend — from every source that has no history of its own, and keeps the research into what the quota percentages mean. Personal, user-space, macOS (LaunchAgents).

Split out of [`agent-statusline`](https://github.com/JeanLescutMuller/agent-statusline) on 2026-09-30. The status line stays there; this repo owns the data. Git history of this code between 2026-08-31 and the split lives in `agent-statusline` (up to `d744951`); older history (the original `agent-quota-tracker`) is below this tree's first commit.

## Usage data documentation

Two canonical files, which downstream projects (`agent-quota-maximizer`) link to instead of restating them:

- `USAGE_DATA_SOURCES.md` — **what usage data exists upstream**, independent of this repo: every Claude and Codex source (transcripts, status-line stdin, APIs, OpenTelemetry, the ChatGPT backend), each unit (quota percent / tokens / USD) at each granularity, tested live, plus what is not available anywhere.
- `USAGE_DATA_REFERENCE.md` — **what this repo captures**: which writer records what, when, from which source, into which file, with which shape, and the traps consumers must handle.

The dollar conversions both assume are derived in `adhoc_quotas_analysis/CONCLUSIONS.md`.

All usage data is split by agent and by **scope**: `data/<agent>/account.jsonl` holds the account-wide meter (quota percent), `data/<agent>/<session-id>.jsonl` holds one session's tokens and spend. **Quota percent is account-scope only**, never in a session file — see `USAGE_DATA_REFERENCE.md` §1.

## Usage

```bash
bash install.sh     # idempotent; requires python3 and jq
bash uninstall.sh   # removes what install.sh deploys; preserves data/; flags anything else left over
```

`install.sh` assumes a bare machine and carries no migration logic: to move between incompatible layouts, run `uninstall.sh`, resolve reported orphans, then `install.sh`. It also adds Claude Code's OpenTelemetry keys to the `env` object of `~/.claude/settings.json` (only those keys; `uninstall.sh` removes the ones still holding our values). They take effect for Claude sessions started afterwards.

## Writers

| Writer | Runs | Source | Writes |
|---|---|---|---|
| `bin/ingest-claude-statusline.sh` | Every Claude status-line render (agent-statusline pipes the payload in) | Statusline stdin: `rate_limits`, session cost, `prompt_cache`, model | `data/claude/account.jsonl` + `data/claude/<session-id>.jsonl` + `state/quota/claude` (source `statusline`); nothing when the transcript has no `assistant` entry to date the reading, and no row when the session already wrote the same one |
| `src/quota_polling/poll_claude.py` | LaunchAgent, 60 s tick; polls every tick while a Claude status line is on screen, else every ~5 min | `GET /api/oauth/usage` | `data/claude/account.jsonl` + `state/quota/claude` (source `API`) |
| `src/quota_polling/poll_codex.py` | Same tick; skips while a Codex session file is fresh | `codex app-server` JSON-RPC | `data/codex/account.jsonl` |
| `src/quota_polling/poll_codex_plan_history.py` | Same tick; one fetch a day | ChatGPT backend `plan_limit_history` | `data/codex/account.jsonl` |
| `src/telemetry/otlp_receiver.py` | Its own KeepAlive LaunchAgent on `127.0.0.1:4318`; Claude Code pushes to it | Claude Code OpenTelemetry events | `data/claude/<session-id>.jsonl` |

Details, row shapes and traps: `USAGE_DATA_REFERENCE.md`. Why the push path exists at all (the poll endpoint 429s ~21% of the time, and can lock out for days): `adhoc_quotas_analysis/AGENTS.md`.

## Contract with agent-statusline

The two projects share exactly three files, each written by one side only. Neither writes into the other's tree, and each works without the other.

| Interface | Written by | Read by | What |
|---|---|---|---|
| `~/opt/agent-usage-tracker/bin/ingest-claude-statusline.sh` | this repo (deployed) | agent-statusline's Claude provider runs it | The Claude provider pipes its raw stdin payload in, unchanged, on every render, before display. Which fields are kept, and where, is this repo's business only. Must always exit 0 quickly; its output is ignored. |
| `~/opt/agent-usage-tracker/state/quota/claude` | ingest (`statusline`), `poll_claude.py` (`API`) | agent-statusline's Claude provider, whatever the source | The freshest known 5h/7d reading: six `$'\034'`-separated fields, `five_pct five_reset week_pct week_reset source observed_at`, percents rounded. Each writer overwrites only if its `observed_at` is newer, so every open session converges on the account's freshest reading. `source` is information only. A push reading is dated by its transcript's last `assistant` entry and is never written undated, so an idle session's frozen reading can't win. |
| `~/opt/agent-statusline/state/heartbeat/{claude,codex}` | agent-statusline, every render | `poll_claude.py`, `poll_codex.py` | Only the mtime matters: a status line is on screen, so poll faster. Missing = idle cadence. |

Without agent-statusline there are no push rows: Claude Code runs a single `statusLine` command, so nothing else can feed the ingest script. Claude's meter history then comes only from the poller, on its ~5 min idle cadence and subject to its 429s, and there are no session cost or `prompt_cache` rows. Codex is unaffected. What the status line shows without this repo is agent-statusline's business.

## Runtime layout

    ~/opt/agent-usage-tracker/
    ├── bin/ingest-claude-statusline.sh   the statusline's entry point (see the contract above)
    ├── src/
    │   ├── quota_polling/                deployed poll_all.py, poll_claude.py, poll_codex.py, poll_codex_plan_history.py, _quota_common.py
    │   └── telemetry/otlp_receiver.py
    ├── data/                             see USAGE_DATA_REFERENCE.md §1
    │   ├── claude/
    │   │   ├── account.jsonl             account scope: Claude poll + push meter readings (quota percent)
    │   │   └── <session-id>.jsonl        session scope: push session rows + telemetry rows (never a percent)
    │   ├── codex/account.jsonl           account scope: Codex poll + plan-history rows
    │   ├── _unattributed/                telemetry events with no usable session id (created on the first one)
    │   └── _archive/                     pre-2026-09-30 logs, kept after the one-time split_by_scope.py migration
    ├── state/
    │   ├── quota/claude                  latest reading only (see the contract above)
    │   ├── ingest/<session-id>           what each session's push last wrote, for dedup; pruned after 30 days idle
    │   └── poll/codex_plan_limit_history last plan-history attempt
    ├── logs/                             quota-poll.{log,err}, otel-receiver.{log,err}
    ├── com.jeanlescut.agent-usage-tracker.plist        poll LaunchAgent (symlinked from ~/Library/LaunchAgents/)
    └── com.jeanlescut.agent-usage-tracker.otel.plist   receiver LaunchAgent (same)

`adhoc_quotas_analysis/` is run-by-hand research, never deployed: it stays in `~/dev/agent-usage-tracker` and reads `~/opt/agent-usage-tracker/data/` from there.

## Tests

```bash
bash tests/run.sh
```

Hermetic: a temp `$HOME`, fixture transcripts and payloads, monkeypatched HTTP, `AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1` to keep install/uninstall off the real launchd domain. See `tests/README.md`.
