# Open questions, concerns and comments on the target design

Status 2026-10-10: **everything here is settled.** The decisions became `design/TARGET_SPEC.md` (validated, proposals P1–P12), built by `design/MIGRATION_PLAN.md` (phases 0–5, all done on 2026-10-10). What is still open is in `TODO.md` ("After the migration to the target design"). This file keeps the record of each concern raised while designing, and where it ended.

| # | Concern | Outcome |
|---|---|---|
| C1 | Labelling old quota rows by the database that holds them is wrong for readings `--peer` copied | NULL `machine` for those (72), decided with the user; done in phase 1 |
| C2 | The first quota sync would send 128 MB | the VM was filled from its central copy instead (phase 1); the first sync sent only the newer rows |
| C3 | `--peer` redundant with the sync | removed in phase 1 |
| C4 | Is telemetry late enough to break "final" slots? | measured: 8,089 of 8,090 events within 60 s, none after 15 min; no change needed |
| C5 | Category 4 rebuilt every 5 min | moot: Category 4 belongs to a reader (`FOR_READERS.md`) |
| C6 | Migrate `requests`, or rebuild them from the transcripts? | rebuilt in `intermediary/` (phase 2): every old request found again with the same tokens; the old table dropped in phase 5 |
| C7 | auto-apply's Agent SDK sessions send no telemetry | open: `TODO.md` item 5 |
| C8 | Codex rows stop reaching the VM | accepted (`design/CODEX_DESIGN.md`); true since phase 3 |
| C9 | Who runs the transcript reader on the VM | parked: `TODO.md` item 6 |
| C10 | Machine names could drift | settled by the rule "Machine name" in `~/AGENTS.md` (commit `f71f282`) |
| C11 | Poll thresholds | Mac 110 / 300 s, VM 290 / 480 s (P4), live since phase 1 |
| C12 | `TODO.md`'s target-design table outdated | rewritten in phase 5 |
| C13 | Docs checker failures | `AGENTS.md` trimmed to 581 words, the broken `PLAN.md` link fixed; left: `adhoc_quotas_analysis/AGENTS.md` too long (`TODO.md` item 4) |
| C14 | `DESIGN_MAP.html` is a design page, not a README | open: `TODO.md` item 3 |

Decided with the user along the way (all in `design/TARGET_SPEC.md`): the four categories; `slots` as one table (no `session_id`, no `session_totals`); intermediary tables kept local; `FOR_READERS.md` for every reader (agent-quota-maximizer gets no change: it will be decommissioned); Option A for the other repos (agent-statusline, auto-apply, bootstrap-home, job-runner): the agent edits, tests, deploys and commits.
