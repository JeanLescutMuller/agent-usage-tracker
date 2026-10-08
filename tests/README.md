# Tests

Pure bash, no external test framework - `harness.sh` is a small assert-style helper (`assert_eq`, `assert_contains`, `assert_file_exists`, ...).

```bash
bash tests/run.sh                           # everything, a few seconds
bash tests/test_ingest_claude_statusline.sh # one file at a time; each is independently runnable
```

Every test is hermetic: a temp `$HOME`, fixture transcripts and payloads, monkeypatched `urlopen`, a free local port for the receiver, and `AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1` for install/uninstall. Nothing here touches your real `~/.claude`, `~/.codex`, `~/opt/agent-usage-tracker`, Keychain or network.

## What's intentionally not covered

- Live API calls and the real macOS Keychain read (`claude_quota_api_poller.py`'s `fetch_token`, the pollers' real requests) - re-verified by hand, see `USAGE_DATA_REFERENCE.md` §8.
- agent-statusline. Its side of the contract (piping the payload in, reading the state file back) is tested in that repo against a stub of this one.

## Files

| File | Covers |
|---|---|
| `harness.sh` | The assert helpers every test file sources |
| `test_ingest_claude_statusline.sh` | `bin/ingest-claude-statusline.sh` and `statusline_payload_reader.py` fed built payloads: account row (unrounded percents, `observed_by_session`, no cost), session snapshot (cost, model, raw `prompt_cache`, never a percent), `observed_at` from the transcript including the DST regression, per-session dedup in the database, the `state/quota/claude` freshness-compared write, unsafe session ids, a non-JSON payload, and that the reading is stored before the script returns (real time) |
| `test_account_gate.sh` | `ingest_account_quota.py`: typed columns, `raw` verbatim, duplicate / redundant / rejected rows, `poll_errors`, the `latest` view, Codex window mapping, the state-file writer (newest wins, rounding, empty resets), idempotency |
| `test_session_gate.sh` | `ingest_session_usage.py`: snapshot dedup, percent keys rejected, requests once per id, and the views `usage_requests` (telemetry cost wins, telemetry-only requests, a re-sent event counted once) and `usage_5m` (`final`) |
| `test_transcript_reader.sh` | `transcript_reader.py` on fixture transcripts with a faked clock: largest-output line, the 15-min rule, resumed copies, incremental offsets, half-written lines, unknown models |
| `test_poll_claude.sh` | `claude_quota_api_poller.py`: `fetch_usage` error handling (every transport failure becomes a failed-attempt row instead of crashing the run), the watched / idle cadence and Retry-After read back from the database, where each outcome is stored |
| `test_codex_plan_history_poller.sh` | `codex_plan_history_poller.py` - the raw row it stores, its 24h / 1h-retry cadence read from the database, failed attempts, and that the bearer token never reaches the database; fake `auth.json` |
| `test_telemetry_receiver.sh` | `telemetry_receiver.py` on a free local port: usage events kept and flattened into the `telemetry` table, prompt/tool events dropped, protobuf / malformed / metrics requests answered without writing, a re-sent batch, localhost-only bind |
| `test_push_to_central.sh` | `push_to_central.py` and `receive_from_machine.py`, central side run locally: every table arrives through the gates, nothing re-sent, only new rows, a dead link (fake `ssh`) keeps the watermark, a bad machine name is refused |
| `test_migrate_to_sqlite.sh` | `adhoc_quotas_analysis/migrate_to_sqlite.py`: every line lands verbatim, idempotent re-run, `--archive`, nothing archived on failure |
| `test_split_by_scope.sh` | `adhoc_quotas_analysis/split_by_scope.py` on a fixture with every historical row shape: counts reconcile, untouched rows byte-identical, no percent in session files, idempotent re-run, in-progress marker |
| `test_install.sh` | `install.sh` and `uninstall.sh` - what is deployed where, symlinked plists, idempotency, the `env` merge and unmerge, `data/` preserved, orphan reporting |
| `test_utils.sh` | `utils.sh` |
| `test_repo_hygiene.sh` | `bash -n` and `py_compile` on everything, shellcheck if installed, only the gates write to a database, and the boundary with agent-statusline |
