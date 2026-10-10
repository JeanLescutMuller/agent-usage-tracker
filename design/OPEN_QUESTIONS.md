# Open questions, concerns and comments on the target design

Written 2026-10-10, before a context compaction, by the agent that drew `design/DESIGN_MAP.html` with the user. It records everything still open; the decisions themselves are in that page (section 9) and, once validated, in `TODO.md`. Delete each point here once it is settled.

## Where things stand

| Item | State |
|---|---|
| `design/DESIGN_MAP.html` (current design, 4 categories, big picture, deep dive) | written, **not committed**; waiting for the user's validation |
| `design/CODEX_DESIGN.md`, `TODO.md` "Someday: Codex in the four categories" | written, **not committed** |
| Last commits in this repo | `ef0898c` (one name per machine; a 429 waits on its own machine), `a7003a5` (docs: target design table, §5.8 accepted gap) |
| job-runner (`~/dev/job-runner`) | 3 files changed for the per-machine 429 (`DESIGN.md`, `examples/agent-usage-tracker/claude-quota.sh`, `test/examples_test.sh`, 29/29 pass); **the user said not to commit job-runner** |
| Prompt for another agent: one machine-name method in every project | `/tmp/hostname-consistency-prompt.md` (outside the repo: `/tmp` may be wiped; copy it if it is still needed) |
| `adhoc_quotas_analysis/usage_by_project_draft.py` | the user's own draft, untracked, never touched |

## Decided with the user (target design, Claude only)

1. Four categories: **1 Quotas** (raw, account-wide, real time, shared both ways every 2 min) · **2 Machine raw** (`sessions_usages.db` + transcripts, never leaves its machine, one copy accepted) · **3 Aggregates** (one table `slots` in `aggregates.db`, from Category 2; the Mac sends its rows to the VM every 5 min, into their own folder `central/<machine>/`) · **4 Meta-analysis** (built by the quota maximizer, not here).
2. A machine polls the quota API only if no machine has a fresh reading; the VM waits longer than the Mac plus the 2-min sync; a 429 waits on its own machine (done).
3. No $ computed by us (drop `requests.usd`, `usage_db.PRICES`); Claude Code's own $ kept (telemetry, per request); `cost-state` not stored for now.
4. agent-statusline calls `statusline_payload_reader.py` directly; it prints the freshest reading; `bin/` and `state/quota/claude` go; auto-apply reads the `latest` view.
5. Reset rule per window, when reading: `observation < reset_dt <= now` → 0%.
6. No reading while nobody uses Claude Code anywhere for more than 8 h: accepted (`USAGE_DATA_REFERENCE.md` §5.8).
7. Codex is outside this first target and keeps running as today (`design/CODEX_DESIGN.md`).

## Decided after the page was validated (2026-10-10)

| # | Decision |
|---|---|
| A | ~~`session_totals`~~ dropped for now (it fits no 5-min slot); the `cost-state` records stay in the transcripts. Its only use was the $ of sessions without telemetry: see C and C7 |
| B | A 5-min slot counts transcript requests **plus** telemetry-only requests (session titles, prompt suggestions…), matched by `request_id` |
| C | Each slot carries the tokens of its requests that have no $ (no telemetry, e.g. auto-apply's SDK sessions), so a partial $ is visible |
| D | `aggregates.db` holds **one table, `slots`**: 5-min slot datetime, machine, folder (the session's working directory), `entrypoint` (`cli` / `sdk-cli` / `sdk-py`: the fact, not an interactive/scheduled label), model, 5 token columns (input, output, cache read, cache write 5m / 1h), Claude Code's $, tokens without $. No `session_id`. Intermediary tables (`requests`: one row per request counted once; `scans`: what is complete) live in a local `intermediary/` folder, never sent, deletable and rebuilt from Category 2 |
| E | Category 4 is **out of this project's scope**: the quota maximizer will own it. Category 3 must let it: (1) estimate the $ or tokens in a 5-hour window and a week, by correlating Category 1 % with Category 3 $ or tokens; (2) read past consumption in $ or tokens, split by project (folder), machine, and interactive vs scheduled (`entrypoint` + folder: facts only, the maximizer classifies). Caveat for (1): the % also counts usage no local file sees (claude.ai, the mobile and desktop apps, cloud sessions) |

## Open points of the page's section 9: all decided on 2026-10-10 (point 2: NULL for the 60 shared readings; the rest in `design/TARGET_SPEC.md`)

| # | Question | Proposal |
|---|---|---|
| 1 | Name of the Category 3 database | `aggregates.db` |
| 2 | Category 1 rows have no `machine` column yet | one-time migration; **but see concern C1** |
| 3 | The existing `central/` folders on the VM (raw rows pushed since 2026-10-08) | **decided**: a backup (tar), then frozen; their Category 1 rows are copied into the VM's `data/claude/account_quotas.db` so the new tables start with the full history (this is also C2's seed); the Mac's Category 3 history comes from its own first `aggregates` run (transcripts since 2026-07-30 + telemetry) |
| 4 | What the reader prints for agent-statusline | **decided**: the 6 fields of today's `state/quota/claude` |
| 5 | Order of work | **decided**: a migration plan is written once the target is fully specified; the order below is only a draft |
| 5′ | (draft) | (a) Category 3 on the Mac (`aggregates.db` with `slots`, `intermediary/`, no $ computed) · (b) the `aggregates` job sends to the VM · (c) Category 1 sync and polling rule · (d) statusline path + auto-apply |

## Concerns found while designing (not yet raised with the user, or only in passing)

| # | Concern | Why it matters | Suggestion |
|---|---|---|---|
| C1 | **Labelling existing Category 1 rows with a `machine` "by the database that holds it" is wrong since 2026-10-10 14:10 UTC**: the `--peer` exchange copies polled readings between the Mac's and the VM's databases (54 `claude_api` rows on the Mac since then, some of them the VM's) | rows would get the wrong machine | label by `row_hash`: a row present in both databases is the machine whose job wrote it first (compare `written_ts`); or accept "unknown" for those few rows |
| C2 | The first Category 1 sync sends the Mac's whole history (176,000+ readings, `raw` included, ~128 MB) to the VM's `data/claude/account_quotas.db` | one huge ssh call | send in chunks with a watermark, as `push_to_central.py` does today; the VM already holds the same rows in `central/chan-lescut-macbook-pro/`, which could seed it locally |
| C3 | The `--peer` mechanism of `claude_quota_api_poller.py` (added 2026-10-10) becomes redundant with the sync | two ways of sharing readings | replace it by the sync in step (c); keep the stagger of thresholds |
| C4 | Category 3 sends only final 5-min slots (first line ≥ 15 min old) | telemetry `$` for a slot arrives within seconds, but Claude Code exports in batches and may drop the last batch when a process is killed | measure how late telemetry rows arrive before relying on "final" for the `$` column |
| C6 | Existing data must move: today's `requests` (in `sessions_usages.db`, with `usd`) become Category 3 (`aggregates.db`, without `usd`) | a schema change of stored data | Category 3 is computed: rebuild it from the transcripts instead of migrating, then drop `requests` from `sessions_usages.db` with an archive; transcripts older than 365 days are gone, so check that none of today's `requests` rows predate the oldest transcript before dropping |
| C7 | auto-apply's Agent SDK sessions send **no telemetry** (114 sessions since 2026-09-30) | their per-request `$` and the requests transcripts miss are never seen; only `cost-state` gives their `$` | separate fix in auto-apply: give its SDK sessions the telemetry env keys |
| C8 | Once the `push` job is replaced, **Codex rows stop reaching the VM** | the VM's copy of Codex data freezes | accepted by putting Codex out of scope; recorded in `design/CODEX_DESIGN.md` |
| C9 | The VM's `transcripts` job vs the maximizer's own job calling `transcript_reader.py` | the user said the maximizer would read its transcripts on the VM | parked by the user with "fewer jobs" |
| C10 | Machine names: job-runner's rule (`LocalHostName`, lower case) can drift if macOS renames `LocalHostName` after a name clash on the network; `hostname -s` is now fixed by the hand-set `HostName` | a drift splits every per-machine key again | the prompt in `/tmp/hostname-consistency-prompt.md` asks another agent to settle one method for every project |
| C11 | Mac thresholds today: poll if the newest reading is older than 110 s (statusline on screen) / 300 s (idle); VM 170 s / 360 s | with a 2-min sync the VM's thresholds must exceed the Mac's by more than 120 s; with the reset rule the idle cadence can slow to ~10 min | set them in step (c) |
| C12 | `TODO.md`'s "Target design" table still describes the design from before the 4 categories | two versions of the truth | rewrite it from `design/DESIGN_MAP.html` section 9 once the user validates |
| C13 | Docs checker (`check_docs.sh`): `AGENTS.md` is 966 words (> 600; my "repeated reading is kept" rule added to it); `adhoc_quotas_analysis/AGENTS.md` 9,835 words and a broken link to `PLAN.md`; `TODO.md` lines 60–63 keep done items | pre-existing except the first | trim `AGENTS.md` (move explanations to `doc/`), fix or drop the `PLAN.md` link, delete the done rows when `TODO.md` is rewritten (C12) |
| C14 | `design/DESIGN_MAP.html` is a design page, not a README | the docs conventions pair a `README.html` with a `README.md` stub | once built, its "target" becomes the current design: turn it into `README.html` (+ stub) and drop the "current" section |

## Facts established in this session (already documented elsewhere)

| Fact | Where |
|---|---|
| Every $ figure anywhere is tokens × a list price; no server returns a usage $; Codex has none at all | `USAGE_DATA_SOURCES.md` §1, §4.5 |
| A 429 on `GET /api/oauth/usage` is per login token, i.e. per machine; the access token lasts 8 h and only Claude Code renews it | `USAGE_DATA_SOURCES.md` §3.5 |
| A repeated quota reading with a new observation time is kept; only re-renders are dropped | `AGENTS.md` rules |
| The Mac's three names, and their merge | `USAGE_DATA_REFERENCE.md` §6 (2026-10-10) |
