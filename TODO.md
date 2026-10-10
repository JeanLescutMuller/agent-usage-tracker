# TODO

## After the migration to the target design (built 2026-10-10)

The design (`design/TARGET_SPEC.md`) is built: phases 0–5 of `design/MIGRATION_PLAN.md`, each recorded there. Left open:

| # | Item | Why |
|---|---|---|
| 1 | Check after a night with the Mac asleep that the VM polled then, and only then (`account_quotas` rows with `source = 'claude_api'` by `machine`) | so far the VM has not polled once while the Mac was awake, as designed; its polling while the Mac sleeps is not yet seen |
| 2 | The VM's `~/AGENTS.md` (Oct 9) still describes `state/quota/claude`: refreshed by the next `bootstrap-home` run there | the template (`bootstrap-home` `17404ef`) and the Mac's copy are current |
| 3 | `design/DESIGN_MAP.html`: its "target" is now the current design; turn it into `README.html` (+ `README.md` stub), dropping the "current" section | concern C14 |
| 4 | `adhoc_quotas_analysis/AGENTS.md` is 9,835 words (docs checker limit 600): move the investigation history to `doc/` | concern C13; pre-existing |
| 5 | auto-apply's Agent SDK sessions send no telemetry, so their slots carry `tokens_without_usd` only: give them the telemetry env keys (in auto-apply) | concern C7 |
| 6 | Parked: who runs the transcript reader on the VM (a reader's own job, eventually) | — |

## Usage per project and per kind of use (development / test runs / scheduled runs)

Requested 2026-10-07 by the user, from the `auto-apply` project. Prompt for the agent working here:

**Status 2026-10-10:** the data this asks for is built: `slots` in `data/claude/aggregates.db` (per 5-min slot × machine × folder × entrypoint × model, every request once, Claude Code's own `usd` and `tokens_without_usd`; `FOR_READERS.md`). What remains is the report itself (the user's draft `adhoc_quotas_analysis/usage_by_project_draft.py`). No $ is computed here any more: where `usd` is missing, report tokens, not a list-price estimate. The decisions and build plan of 2026-10-07 → 10-08 are in git history and `USAGE_DATA_REFERENCE.md` §6.

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

### No Claude reading while every machine's token has expired

Accepted by the user on 2026-10-10 (`USAGE_DATA_REFERENCE.md` §5.8). Optional test, if it ever matters: once the VM's token has expired (401 in its `poll_errors`), run a Claude Code command that sends no message (`claude auth status`, `claude doctor`) and check whether `~/.claude/.credentials.json`'s `expiresAt` moved. If it did, a job running it every 6 h on the VM would keep the VM's poller alive without opening a 5-hour window.

### Not obtainable from any source

For the record, so nobody re-investigates: Codex spend at any granularity on a Plus account (403 or `null` everywhere); account-wide spend for either agent; quota percent per session or thread (Codex's `thread_usage/query_v2` returns `unavailable`); usage while the Mac is asleep or off (Codex's `plan_limit_history` recovers the last 7 days, Claude has no equivalent); a per-client breakdown for Claude (claude.ai web / mobile, other machines).

## Someday: Codex in the four categories

Parked by the user on 2026-10-10: the first target design is Claude only. Until this is picked up, Codex keeps running exactly as today (jobs `codex-quota` and `codex-plan-history`, the Codex part of `transcript_reader.py`), and the first target must not break it. What exists, the side effects of the first target on Codex (its rows stop reaching the VM once the `push` job goes), and a sketch of Codex in each category: `design/CODEX_DESIGN.md`. Un-park when the Claude target is built and running.

## Re-measure the Claude budgets on Max 5x

The plan changed from Pro to Max 5x on 2026-10-07 about 16:51Z (`USAGE_DATA_REFERENCE.md` §6). Every Claude figure in `adhoc_quotas_analysis/CONCLUSIONS.md` is for Pro. Once a few Max 5x windows have peaked at 30% or more:

- add a plan cutoff to `quota_model.py` and `window_gaps.py` so Pro and Max 5x windows are never mixed;
- re-measure the 5-hour budget in list-price $ (Pro: median $32; 5× would be about $160), the 7-day/5-hour meter ratio (Pro: 8.85) and the token-type weights (Pro: cache reads count about 4–5× less than list price);
- answer whether the multiplier is really 5× for both meters.

## Claude poller cadence without agent-statusline

Parked 2026-10-04. `claude_quota_api_poller.py` (then `poll_claude.py`) polls every tick only while agent-statusline's heartbeat says a status line is on screen; without agent-statusline it stays on its ~5 min idle cadence even while Claude sessions are active. A second liveness signal, recently modified transcripts under `~/.claude/projects/**/*.jsonl`, would fix that, the way `poll_codex.py` uses Codex session files.
