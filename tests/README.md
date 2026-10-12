# Tests

Pure bash, no external test framework - `harness.sh` is a small assert-style helper (`assert_eq`, `assert_contains`, `assert_file_exists`, ...).

```bash
bash tests/run.sh                           # everything, a few seconds
bash tests/test_ingest_claude_statusline.sh # one file at a time; each is independently runnable
```

Every test is hermetic: a temp `$HOME`, and every python call pointed at a temp runtime (`AGENT_USAGE_TRACKER_RUNTIME` or `--runtime`), fixture transcripts and payloads, monkeypatched `urlopen`, a fake `ssh` for the other machine, a free local port for the receiver, and `AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1` for install/uninstall. Nothing here touches your real `~/.claude`, `~/.codex`, `~/opt/agent-usage-tracker`, Keychain or network.

## What's intentionally not covered

- Live API calls and the real macOS Keychain read (`claude_quota_api_poller.py`'s `fetch_token`, the pollers' real requests) - re-verified by hand, see `USAGE_DATA_REFERENCE.md` §8.
- agent-statusline and auto-apply. Their side of the contract (piping the payload in and showing the printed line; reading the view `latest`) is tested in their repos against a stub of this one.
- The job entrypoints (`jobs/*.sh`): their copies in job-runner's `examples/agent-usage-tracker/` are tested there (`test/examples_test.sh`), the work stubbed.

## Files

| File | Covers |
|---|---|
| `harness.sh` | The assert helpers every test file sources |
| `test_ingest_claude_statusline.sh` | `statusline_payload_reader.py` fed built payloads: account row (unrounded percents, `observed_by_session`, `machine`, no cost), session snapshot (cost, model, raw `prompt_cache`, never a percent), `observed_at` from the transcript including the DST regression, per-session dedup, nothing stored without a date; the six-field line it prints (rounded percents, the reset rule, also for a payload without `rate_limits`, nothing without a database); its last failure in `logs/statusline_reader.err`, none after working runs |
| `test_account_gate.sh` | `ingest_account_quota.py`: typed columns, `raw` verbatim, duplicate / redundant / rejected rows, `poll_errors`, the `latest` view, `machine` (from raw, given, checked), Codex window mapping, a column added later, idempotency |
| `test_session_gate.sh` | `ingest_session_usage.py`: snapshot dedup and percent keys rejected (Claude); requests once per id and the views `usage_requests` / `usage_5m` (Codex, the only agent that keeps them) |
| `test_transcript_reader.sh` | `transcript_reader.py` on fixture transcripts with a faked clock: Claude requests into `intermediary.db` (largest-output line, the 15-min rule, resumed copies, incremental offsets, half-written lines, no `usd` or `machine`, rebuilt identical after `intermediary/` is deleted); Codex turns |
| `test_aggregate_slots.sh` | `aggregate_slots.py` and its gate `ingest_aggregates.py`: final slots only, telemetry's cost joined on request id (a re-sent batch counted once), telemetry-only requests in their session's folder with cache writes of unknown duration, `tokens_without_usd`, each slot written once, the gate's checks (a bad row rejects its batch); end to end from transcript files to `slots` |
| `test_poll_claude.sh` | `claude_quota_api_poller.py`: `fetch_usage` error handling (every transport failure becomes a failed-attempt row), exit codes and last lines (0 / 10 / 75 / 1), `--if-older-than`, `machine` in raw and column, and the 2-min sync through a fake `ssh` to a second runtime: each machine's own readings both ways, none echoed back, not before it is due, a new reading at the next sync, the VM unreachable (the run goes on; a failure after 30 min; a 429 still waits) |
| `test_codex_quota_poller.sh` | `codex_quota_api_poller.py`: a skip exits 10 after its `skip:` line |
| `test_codex_plan_history_poller.sh` | `codex_plan_history_poller.py` - the raw row it stores, no interval of its own, failed attempts (exit 1), and that the bearer token never reaches the database; fake `auth.json` |
| `test_telemetry_receiver.sh` | `telemetry_receiver.py` on a free local port: usage events kept and flattened into the `telemetry` table, prompt/tool events dropped, protobuf / malformed / metrics requests answered without writing, a re-sent batch, localhost-only bind |
| `test_push_to_central.sh` | `push_to_central.py` and `receive_from_machine.py` through a fake `ssh`: the slots reach the VM's `aggregates/<machine>.db` through the gate, slots sent as the receiver itself refused, nothing re-sent, only new slots, a dead link keeps the watermark, a bad line refuses its whole chunk, a bad machine name is refused; `aggregate_slots.py --send-to` |
| `test_install.sh` | `install.sh` and `uninstall.sh` - what is deployed where, one trigger per job running its entrypoint, the reader's first line and machine name, symlinked plists / units, idempotency, the `env` merge and unmerge, `data/` preserved but `intermediary.db`, orphan reporting |
| `test_utils.sh` | `utils.sh` |
| `test_repo_hygiene.sh` | `bash -n` and `py_compile` on everything, shellcheck if installed, only the gates write to a database (and `transcript_reader.py` to `intermediary.db`), and the boundary with agent-statusline |
| `test_analysis.sh` | `adhoc_quotas_analysis/` on a fixture written through the gates: Q1 an idle gap with the server's "no window" reading and the window opened by the next request, the Monday 7-day end, the plan change cutting a window; Q2 `B` and its R², an older reading dropped, too few readings, 5-hour windows per week; Q3 the split by project (subfolders, scratchpads) and kind, `--span day`, `<synthetic>` left out; the databases unchanged |
| `test_migrate_to_target.sh` | `adhoc_quotas_analysis/migrate_to_target.py`, the steps of `git show 1ced0c4:design/MIGRATION_PLAN.md`: `backup` (every database copied whole, WAL included, and checked); phase 1 on two fake machines (`cat1-shared`, `sync-init`, `cat1-machine` with the watermark moved back, `cat1-seed-vm`); `remove-trigger`, `check-intermediary`, `archive-central`, `view-latest`, `remove-legacy`, `drop-old-requests`; every step idempotent; `data-layout` (P13, P14: `intermediary.db`, one aggregates file per machine, rowids kept, `central/` gone; a file with another machine's slots refused) |
