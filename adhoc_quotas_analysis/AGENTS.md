# adhoc_quotas_analysis: working here

What is here and why: `README.md`. The answers: `CONCLUSIONS.md`. The repo's rules (`../AGENTS.md`) apply too.

## Rules

- **A reader, never a writer.** Open a database only through `load.py` (`PRAGMA query_only`, `../FOR_READERS.md`). Read Category 1 (the readings) and Category 3 (`slots`); never `sessions_usages.db`, `intermediary/` or the transcripts, except in `verify_token_costs.py`, which checks those sources.
- **No $ computed.** A $ is Claude Code's own `usd`. Where it is missing, show the tokens; never price them.
- **Always per plan.** Never mix two plans in a window, a budget or a ratio. When the plan changes, add a row to `load.PLANS` and to `../USAGE_DATA_REFERENCE.md` §6.
- **Never attribute a percent to a project, kind or machine**: only the $ and tokens split (`../FOR_READERS.md` rule 5).
- **One question, one script.** The question is the first line of its docstring; add a row to `README.md`, a section to `CONCLUSIONS.md`, and a test to `../tests/test_analysis.sh`.
- **Every number in `CONCLUSIONS.md` comes from a script here**, with the date of the run. Re-run the scripts and update the numbers and the date together. Frozen findings (§4) are never edited, only removed when wrong.
- **Standard library only**: no numpy, scipy or notebooks.
- **A layout change of the runtime** is a new subcommand of `migrate_to_target.py` (or a script in its pattern): hand-run, idempotent, archives first, prints counts.

## Tests

`bash tests/test_analysis.sh` (and `tests/test_migrate_to_target.sh` for the migration steps). The fixture is written through the gates into a temp runtime; every python call points at it.
