# adhoc_quotas_analysis

Research into what the Claude quota percent means, run by hand and never deployed. It is a **reader** of this project's data (`../FOR_READERS.md`): Category 1 (the readings) and Category 3 (the `slots`), nothing else. The answers are in `CONCLUSIONS.md`.

```
 data/claude/account_quotas.db  (Category 1) ─┐
                                              ├─► load.py ─┬─► windows.py      Q1  when does a window start and end?
 data/claude/aggregates/*.db    (Category 3) ─┘            ├─► budgets.py      Q2  how many $ make 1% of a window?
 (one per machine; the VM: both)                          └─► usage_split.py  Q3  where do the $ of a window go?
                                                                     │
                                                                     ▼
                                                              CONCLUSIONS.md

 transcripts + telemetry + ccusage ─► verify_token_costs.py   are the local tokens and $ exact?  (../USAGE_DATA_SOURCES.md §3.4)
 the runtime's databases ───────────► migrate_to_target.py    the hand-run migration steps of 2026-10 (git show 1ced0c4:design/MIGRATION_PLAN.md)
```

## Files

| File | What it is |
|---|---|
| `load.py` | the one place that opens the databases: `readings()`, `windows()`, `slots()`, `project()`, `kind()`, the plans |
| `windows.py` | Q1: 5-hour gaps, the 7-day Monday time, a 7-day reset across a running 5-hour window |
| `budgets.py` | Q2: `B` per window, 5-hour windows per week, per plan |
| `usage_split.py` | Q3: per 7-day window, 5-hour window or day, the $ and tokens by project, kind, machine or model |
| `verify_token_costs.py` | a check of the data sources: transcripts against telemetry and ccusage |
| `migrate_to_target.py` | the migration steps to `../design/DESIGN.md`, all done; `check-intermediary` still checks the slots against their requests |
| `CONCLUSIONS.md` | the answers: re-measured, and frozen (Pro and Codex, 2026-08/09) |

## Words

| Word | Means |
|---|---|
| **B** | a window's budget: the $ that would take it to 100%, fitted per window as `pct = 100 × usd / B` on the readings that first showed a new highest percent |
| **5-hour windows per week** | within one 5-hour window, (5-hour % gained) / (7-day % gained): how many full 5-hour windows one week holds; needs no $ |
| **plan** | the account's subscription over time: `pro+promo`, `pro`, `max5x` (`load.PLANS`) |
| **project** | a slot's folder reduced to its repo: `~/dev/<x>/…`, `~/opt/<x>/…` and Claude Code's scratchpads of `<x>` → `<x>` |
| **kind** (of use) | `development` = interactive (`cli`); `test run` = `claude -p` or the Agent SDK outside `~/opt/`; `scheduled run` = the same under `~/opt/` |

## Run

```bash
python3 adhoc_quotas_analysis/windows.py
python3 adhoc_quotas_analysis/budgets.py
python3 adhoc_quotas_analysis/usage_split.py --span 7d --by project --since 2026-10-07
python3 adhoc_quotas_analysis/usage_split.py --span 5h --by kind
```

Each takes under a second, reads `~/opt/agent-usage-tracker` (or `$AGENT_USAGE_TRACKER_RUNTIME`), and needs only Python's standard library.

## History

Until 2026-10-11 this folder also held the notebook and scripts that priced transcript tokens themselves (`analysis.ipynb`, `quota_model.py`, `window_gaps.py`), the one-time migrations of 2026-08-30 → 10-10 (`split_quota_log.py`, `split_by_scope.py`, `split_errors.py`, `rename_sources.py`, `migrate_to_sqlite.py`, `merge_machine_names.py`, each recorded in `../USAGE_DATA_REFERENCE.md` §6) and the investigation notes of 2026-08/09. All of it is in git at `12956dc`:

```bash
git show 12956dc:adhoc_quotas_analysis/AGENTS.md     # the notes: the 429 investigation, naming history, gotchas
git checkout 12956dc -- adhoc_quotas_analysis/quota_model.py
```
