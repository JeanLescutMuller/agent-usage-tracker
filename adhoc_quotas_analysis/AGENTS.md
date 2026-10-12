# adhoc_quotas_analysis: working here

Research into what the Claude quota percent means, run by hand, never deployed: a **reader** of Category 1 (the readings) and Category 3 (the `slots`). The answers: `CONCLUSIONS.md`; drawn for Jean, with the scripts and how to run them: `README.html`. The repo's rules (`../AGENTS.md`) apply too.

## Rules

- **A reader, never a writer.** Open a database only through `load.py` (`PRAGMA query_only`, `../FOR_READERS.md`). Read Category 1 (the readings) and Category 3 (`slots`); never `sessions_usages.db`, `intermediary.db` or the transcripts, except in `verify_token_costs.py`, which checks those sources.
- **No $ computed.** A $ is Claude Code's own `usd`. Where it is missing, show the tokens; never price them.
- **Always per plan.** Never mix two plans in a window, a budget or a ratio. When the plan changes, add a row to `load.PLANS` and to `../doc/changes.md`.
- **Never attribute a percent to a project, kind or machine**: only the $ and tokens split (`../FOR_READERS.md` rule 5).
- **One question, one script.** The question is the first line of its docstring; add a row to the files table of `README.html`, a section to `CONCLUSIONS.md`, and a test to `../tests/test_analysis.sh`.
- **Every number in `CONCLUSIONS.md` comes from a script here**, with the date of the run. Re-run the scripts and update the numbers and the date together. Frozen findings (§4) are never edited, only removed when wrong.
- **`README.html` is `CONCLUSIONS.md` drawn** (then the folder's scripts): the same sections and numbers; change both together.
- **Standard library only**: no numpy, scipy or notebooks.
- **A layout change of the runtime** is a new subcommand of `migrate_to_target.py` (or a script in its pattern): hand-run, idempotent, archives first, prints counts.

## Vocabulary

| Word | Means | Never say |
|---|---|---|
| **B** | a window's budget: the $ that would take it to 100%, fitted per window as `pct = 100 × usd / B` on the readings that first showed a new highest percent | — |
| **5-hour windows per week** | within one 5-hour window, (5-hour % gained) / (7-day % gained): how many full 5-hour windows one week holds; needs no $ | — |
| **plan** | the account's subscription over time: `pro+promo`, `pro`, `max5x` (`load.PLANS`) | — |
| **project** | a slot's folder reduced to its repo: `~/dev/<x>/…`, `~/opt/<x>/…` and Claude Code's scratchpads of `<x>` → `<x>` | — |
| **kind** (of use) | `development` = interactive (`cli`); `test run` = `claude -p` or the Agent SDK outside `~/opt/`; `scheduled run` = the same under `~/opt/` | — |

## Tests

`bash tests/test_analysis.sh` (and `tests/test_migrate_to_target.sh` for the migration steps). The fixture is written through the gates into a temp runtime; every python call points at it.
