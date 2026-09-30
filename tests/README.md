# Tests

Pure bash, no external test framework - `harness.sh` is a small assert-style helper (`assert_eq`, `assert_contains`, `assert_file_exists`, ...).

```bash
bash tests/run.sh                           # everything, a few seconds
bash tests/test_ingest_claude_statusline.sh # one file at a time; each is independently runnable
```

Every test is hermetic: a temp `$HOME`, fixture transcripts and payloads, monkeypatched `urlopen`, a free local port for the receiver, and `AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1` for install/uninstall. Nothing here touches your real `~/.claude`, `~/.codex`, `~/opt/agent-usage-tracker`, Keychain or network.

## What's intentionally not covered

- Live API calls and the real macOS Keychain read (`poll_claude.py`'s `fetch_token`, the pollers' real requests) - re-verified by hand, see `USAGE_DATA_REFERENCE.md` §8.
- agent-statusline. Its side of the contract (piping the payload in, reading the state file back) is tested in that repo against a stub of this one.

## Files

| File | Covers |
|---|---|
| `harness.sh` | The assert helpers every test file sources |
| `test_ingest_claude_statusline.sh` | `bin/ingest-claude-statusline.sh` fed built payloads: account row (unrounded percents, `observed_by_session`, no cost), session row (cost, model, raw `prompt_cache`, never a percent), `observed_at` from the transcript including the DST regression, the `state/quota/claude` freshness-compared write (tag `X`), unsafe session ids, a non-JSON payload |
| `test_quota_common.sh` | `src/quota_polling/_quota_common.py`'s `write_state_if_newer` - the Python-side mirror of the ingest script's state-file writer |
| `test_poll_claude.sh` | `poll_claude.py`'s `fetch_usage` error handling - every transport failure (including `RemoteDisconnected`) becomes an error row instead of crashing the run |
| `test_poll_codex_plan_history.sh` | `poll_codex_plan_history.py` - the raw row it logs, its 24h / 1h-retry cadence, error rows, and that the bearer token never reaches the log; fake `auth.json` |
| `test_otlp_receiver.sh` | `src/telemetry/otlp_receiver.py` on a free local port: usage events kept and flattened, prompt/tool events dropped, protobuf / malformed / metrics requests answered without writing, localhost-only bind |
| `test_split_by_scope.sh` | `adhoc_quotas_analysis/split_by_scope.py` on a fixture with every historical row shape: counts reconcile, untouched rows byte-identical, no percent in session files, idempotent re-run, in-progress marker |
| `test_install.sh` | `install.sh` and `uninstall.sh` - what is deployed where, symlinked plists, idempotency, the `env` merge and unmerge, `data/` preserved, orphan reporting |
| `test_utils.sh` | `utils.sh` |
| `test_repo_hygiene.sh` | `bash -n` and `py_compile` on everything, shellcheck if installed, and the boundary with agent-statusline |
