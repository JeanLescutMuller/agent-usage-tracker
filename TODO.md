# TODO

## Usage per project and per kind of use (development / test runs / scheduled runs)

Requested 2026-10-07 by the user, from the `auto-apply` project. Prompt for the agent working here:

### Decided with the user, 2026-10-07 → 10-08

| Decision | Detail |
|---|---|
| Two parts, one repo | **Account quotas** (account-wide %, real time) and **session usages** (tokens + $ per request, session and execution folder). Classifying usage (organic, bot, test run…) is `agent-quota-maximizer`'s job; this repo only exposes facts (`folder`, `entrypoint`, `interactive` = `entrypoint == "cli"`) |
| Two SQLite databases per machine and per agent | `data/<agent>/account_quotas.db` and `data/<agent>/sessions_usages.db`. One technology. Scope rule unchanged: no $ or tokens in the first, no percent in the second |
| Every database has one gate, the only writer | `ingest_account_quota.py` and `ingest_session_usage.py` validate types and scope and skip redundant rows. Collectors never open a database. Enforced by a repo-hygiene test |
| Insert-only everywhere | A request is stored once, when its transcript lines are ≥ 10 min old (a request's lines span ≤ 292 s, measured). Per-folder $ therefore lags 10–15 min; quota % stays real time |
| Time fields | `ts` (Unix epoch) and `dt` (ISO UTC) are the first columns. In `account_quotas.db`, `ts` = when the reading was true (today's `observed_at`) |
| No state file | `state/quota/claude` is dropped; the statusline reads the `latest` view with the `sqlite3` CLI (4 ms; a full render is 40 ms) and runs the ingest in the foreground (real time, about 28 ms, decided 2026-10-08) |
| Central store on the VM, push only | Each machine pushes **new rows only** (row id > last pushed) over ssh every 5 min when online; on the VM the same gates insert them into `~/opt/agent-usage-tracker/central/<machine>/<agent>/`. The VM never pulls (the Mac sleeps, goes offline, sits behind NAT) |
| No `account_usages` | No account-wide token or $ source exists for a Claude subscription (ccusage reads only local files; `USAGE_DATA_SOURCES.md` §3.5). Unseen usage is bounded by the meter's resolution, about 1 point per window |
| Sources and prices proven | Transcript tokens and $ match telemetry and ccusage exactly (`USAGE_DATA_SOURCES.md` §3.4, `adhoc_quotas_analysis/verify_token_costs.py`) |
| Speed | 200k rows (a year): 5-min batch insert 1.5 ms, last-24 h slot query 1.3 ms. `sqlite3` CLI insert from bash 5.5 ms; 8 concurrent writers: 0 failures |

### Build plan (2026-10-08; Claude first, Mac first)

```
COLLECTORS (by source)                 GATES (only writers)         DATABASES (per machine, per agent)
statusline_payload_reader.py ─quota──┐
quota_api_poller.py ─────────────────┴▶ ingest_account_quota.py ─▶ account_quotas.db ─(view latest)─▶ statusline
statusline_payload_reader.py ─session┐
telemetry_receiver.py ───────────────┤
transcript_reader.py (every 5 min) ──┴▶ ingest_session_usage.py ─▶ sessions_usages.db ─(view usage_5m)
                                                                        │ new rows, every 5 min, ssh
VM central/<machine>/<agent>/  ◀── same two gates ◀──────────────────────┘
```

| # | Step | Detail |
|---|---|---|
| 1 | Schemas + gates | `account_quotas(ts, dt, source, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts, observed_by_session, raw)` + view `latest`. `sessions_usages.db`: `requests(ts, dt, request_id UNIQUE, machine, session_id, folder, entrypoint, model, query_source, in, out, cache_read, cache_write_5m, cache_write_1h, usd, usd_from)`, `telemetry(ts, dt, request_id, session_id, …, raw)`, `session_snapshots(ts, dt, session_id, model_id, session_cost_usd, prompt_cache)`, `files(path, ino, offset)`, `scans(ts, dt, complete_through_ts)`, view `usage_5m`. WAL, busy timeout. Both gates are importable and runnable from the command line |
| 2 | Collectors | `statusline_payload_reader.py` (replaces `bin/ingest-claude-statusline.sh`), `quota_api_poller.py` (renames `poll_claude.py` / `poll_codex.py`), `telemetry_receiver.py` (renames `otlp_receiver.py`, now writes through the gate), `transcript_reader.py` (new, every 5 min, byte offset per file, requests ≥ 10 min old, $ from telemetry else the verified price table) |
| 3 | One-time migration | `adhoc_quotas_analysis/migrate_to_sqlite.py`: JSONL → the two databases through the gates; originals to `data/_archive/`; idempotent; row counts reconcile; `quota_model.py` / `window_gaps.py` output unchanged |
| 4 | agent-statusline | Runs the payload reader in the foreground, reads `latest` instead of `state/quota/claude`. Unchanged when the tracker is absent. Heartbeat stays (the poller uses it) |
| 5 | Push to the VM | `push_to_central.py` after each `transcript_reader.py` run; VM install: `apt install sqlite3`, systemd `--user` units for the poller, receiver, transcript reader and the receiving gates |
| 6 | Readers, docs, tests | `agent-quota-maximizer` reads the central databases; rewrite `USAGE_DATA_REFERENCE.md`, `README.md` (contract with agent-statusline), the rules in `AGENTS.md`, `tests/` |
| 7 | Codex | Same layout under `data/codex/` |

**Status 2026-10-08 (deployed on the Mac):**

| # | Step | Status |
|---|---|---|
| 1–4 | Schemas + gates, collectors, migration, agent-statusline compatibility | ✅ done; see `USAGE_DATA_REFERENCE.md` §6 (2026-10-08 row) |
| 5 | Push to the VM | ✅ 2026-10-08: tracker on the VM (systemd `--user`), every machine pushes to `central/<machine>/`; see `USAGE_DATA_REFERENCE.md` §11. Open: log Claude Code in on the VM so its own Claude poller works (credentials empty since 2026-08-27); the token also needs Claude Code to run there now and then to stay refreshed |
| 6 | Readers, docs, tests | ✅ this repo and agent-quota-maximizer's `s1_ingest.py`; ⏳ the statusline, auto-apply and smart-orchestrator still read `state/quota/claude` (kept, written by the account gate) - switch them to the `latest` view, then drop the file |
| 7 | Codex sessions | ✅ 2026-10-08: turns in `data/codex/sessions_usages.db`, identical to ccusage. **Open: which Codex price is the reference?** `usage_db.PRICES` uses ccusage's $4/$20 for `gpt-5.6-sol` (reproduces its totals; other Codex models stay NULL), `adhoc_quotas_analysis/CONCLUSIONS.md` uses $5/$30. Pick one before the maximizer converts Codex $ to % |

`machine` = the short hostname (decided 2026-10-08).

### Why

Some projects now call Claude **by themselves**, unattended: `auto-apply` (`~/opt/auto-apply/`, daily via
launchd) runs short headless sessions through the Claude Agent SDK to judge jobs, draft letters, drive Chrome
and read Gmail. Those sessions use the same subscription quota as interactive work, but this tracker **does not
see them at all**:

- headless sessions never render a status line, so the push path gets nothing;
- the SDK is started with `setting_sources=[]`, so the telemetry `env` keys of `~/.claude/settings.json` are not
  applied and the OTLP receiver gets nothing;
- the quota poller only sees the account-wide percent moving.

Checked 2026-10-07: of 81 `auto-apply` transcripts, the tracker knows 1 (the interactive one).

### Value

Answer the user's questions with data instead of guesses:

| Question | Today |
|---|---|
| How much of my quota goes to project X? | unknown |
| For project X, how much is **development** (me + Claude interactively) vs its **scheduled runs** vs **test runs** I start by hand? | unknown |
| Is a scheduled job getting more expensive over time (more turns, bigger prompts)? | unknown |
| Which step of a pipeline is worth turning into plain code? | had to be computed by hand (see example below) |

### What to build (proposal; the agent here decides the details)

Read the transcripts, which already hold everything, for every project (not just auto-apply):

```
~/.claude/projects/<encoded cwd>/<session-uuid>.jsonl            main sessions
~/.claude/projects/<encoded cwd>/<session-uuid>/subagents/*.jsonl subagents
```

Group each session by:

| Field | From | Values seen |
|---|---|---|
| project | the `cwd` field inside the records (more reliable than the lossy folder name), reduced to the repo name: `~/dev/<x>` or `~/opt/<x>` → `<x>`. Folders like `-private-tmp-claude-502--Users-jeanlescut-dev-auto-apply-<uuid>-scratchpad` belong to `auto-apply` too | `auto-apply`, … |
| kind of use | `entrypoint` + where `cwd` is | `cli` in `~/dev/<x>` = **development**; `sdk-*` in `~/dev/<x>` = **test run**; `sdk-*` in `~/opt/<x>` = **scheduled run** |
| model | `message.model` of assistant records | `claude-opus-…`, `claude-sonnet-…`, `claude-haiku-…` |

`entrypoint` values: `cli` (interactive), `sdk-cli` (`claude -p`, per `USAGE_DATA_SOURCES.md` §3.4) and
**`sdk-py`** (Python Agent SDK, seen in every auto-apply pipeline transcript, Claude Code 2.1.292).

Per group and day: sessions, requests, input / cache-write / cache-read / output tokens, `cost-state` totals when
present, else a $-equivalent from list prices. Remember that streamed assistant records repeat the same
`message.id`: count each id once.

Output: rows in `data/` (usage fields only), plus a small command, e.g.
`usage-by-project [--since 2026-10-01] [--project auto-apply]`, printing a table. This fits the
"nightly extraction" already proposed above under *Retention*: one job can do both.

**Privacy:** these transcripts contain CVs, job ads and email bodies (auto-apply). Copy **only** usage fields
(ids, timestamps, cwd, entrypoint, model, token counts, cost); never prompt or answer text.

**Limit to state in the output:** quota percent cannot be split per project (Anthropic only gives the account
total); tokens and $-equivalent can, and the quota follows them roughly.

### Example (computed by hand on 2026-10-07; the tool should reproduce it)

auto-apply, API-price equivalent (haiku $1/$5, sonnet $3/$15, opus $5/$25 per Mtok; cache write ×1.25, read ×0.1):

| Kind | Sessions | Model | ≈ $ |
|---|---|---|---|
| development (`cli`, `~/dev/auto-apply`) | 1 | opus | 48.94 |
| test runs (`sdk-py`, `~/dev/auto-apply`) | 71 | sonnet / haiku | 3.62 |
| scheduled runs (`sdk-py`, `~/opt/auto-apply`) | 9 | haiku | 0.06 |

Inside test + scheduled runs, by the first words of the session's first prompt (a project could later tag its
sessions explicitly instead): judging jobs 51 × $0.035, apply in browser 4 × ~$0.20, drafting 5 × $0.074,
Sapling detector in Chrome 1 × $0.29, fact-check review 4 × $0.045, visual check 2 × $0.035, Gmail check 9 × $0.007.

Script used (single pass, ~50 lines; a starting point, not a spec):
`adhoc_quotas_analysis/usage_by_project_draft.py` (groups by folder + `entrypoint` + first-prompt prefix + model;
hard-coded to the two auto-apply folders).

### Optional, later

A project could label its own sessions (e.g. auto-apply passing a step name), so "by step" does not depend on
prompt prefixes. Agree on the convention with the user before asking other projects to adopt it.

## Remaining usage-data gaps

Parked 2026-09-30. Everything important that upstream exposes is now persisted (see `USAGE_DATA_REFERENCE.md` §1.2 and §7); these are what is left, in rough order of value. Details of each source are in `USAGE_DATA_SOURCES.md`.

### Retention of data we rely on but do not own

| Data | Lives in | Risk |
|---|---|---|
| Claude per-message tokens, per-session `cost-state` | `~/.claude/projects/**/*.jsonl` | Claude Code deletes transcripts after `cleanupPeriodDays` (365 on this machine). After that, only the push rows' `session_cost_usd` remains per session |
| Codex per-turn tokens and quota snapshots | `~/.codex/sessions/**/*.jsonl` | Codex's own retention rules — not checked yet |

Proposed fix, if history beyond a year matters: a small nightly job extracting only the usage records (transcript `cost-state` records and deduplicated assistant `usage`, Codex `token_count` events) into `data/`, a few MB instead of the ~517 MB of full transcripts. First step: check Codex's session-file retention.

### Still capturable, not captured

| Gap | Source | Value | Effort |
|---|---|---|---|
| Codex usage per day × model × client (CLI, IDE, web, …) | ChatGPT backend `usage/daily-token-usage-breakdown` (`USAGE_DATA_SOURCES.md` §4.4) | Low to medium: the only per-client split, but **relative** (peak day = 100); `dailyUsageBuckets` gives the scale | One more request in `poll_codex_plan_history.py` |
| Claude quota status beyond the percent (`status` allowed / warning / rejected, `representative-claim`, overage) | `/v1/messages` response headers (`USAGE_DATA_SOURCES.md` §3.6) | Low: the percent already carries most of it | Needs a billed request of our own (≈ $0.0025) or a proxy |

### Debian VM not covered

Every collector (push path, pollers, telemetry receiver) is deployed on the MacBook only, as LaunchAgents. Claude Code or Codex usage on the VM shows only as account-wide percent movement — no telemetry, no push rows. Covering it needs systemd `--user` units for the pollers and receiver, and the telemetry env keys in the VM's `~/.claude/settings.json`. Only worth doing if agents actually run there.

### Not obtainable from any source

For the record, so nobody re-investigates: Codex spend at any granularity on a Plus account (403 or `null` everywhere); account-wide spend for either agent; quota percent per session or thread (Codex's `thread_usage/query_v2` returns `unavailable`); usage while the Mac is asleep or off (Codex's `plan_limit_history` recovers the last 7 days, Claude has no equivalent); a per-client breakdown for Claude (claude.ai web / mobile, other machines).

## Re-measure the Claude budgets on Max 5x

The plan changed from Pro to Max 5x on 2026-10-07 about 16:51Z (`USAGE_DATA_REFERENCE.md` §6). Every Claude figure in `adhoc_quotas_analysis/CONCLUSIONS.md` is for Pro. Once a few Max 5x windows have peaked at 30% or more:

- add a plan cutoff to `quota_model.py` and `window_gaps.py` so Pro and Max 5x windows are never mixed;
- re-measure the 5-hour budget in list-price $ (Pro: median $32; 5× would be about $160), the 7-day/5-hour meter ratio (Pro: 8.85) and the token-type weights (Pro: cache reads count about 4–5× less than list price);
- answer whether the multiplier is really 5× for both meters.

## Claude poller cadence without agent-statusline

Parked 2026-10-04. `claude_quota_api_poller.py` (then `poll_claude.py`) polls every tick only while agent-statusline's heartbeat says a status line is on screen; without agent-statusline it stays on its ~5 min idle cadence even while Claude sessions are active. A second liveness signal, recently modified transcripts under `~/.claude/projects/**/*.jsonl`, would fix that, the way `poll_codex.py` uses Codex session files.
