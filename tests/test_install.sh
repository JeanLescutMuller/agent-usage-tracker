#!/bin/bash
# End-to-end tests for install.sh and uninstall.sh, run against a temp $HOME
# so nothing ever touches the real machine. AGENT_USAGE_TRACKER_SKIP_LAUNCHD
# keeps both off the real gui/$(id -u) launchd domain, which a HOME override
# can't sandbox.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

run_script() {
    local script="$1" home="$2" err_file
    err_file="$(mktemp "${TMPDIR:-/tmp}/th-err.XXXXXX")"
    TH_OUT="$(HOME="$home" AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1 bash "$REPO_ROOT/$script" 2>"$err_file")"
    TH_STATUS=$?
    TH_ERR="$(cat "$err_file")"
    rm -f "$err_file"
}
new_home() { mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-installhome.XXXXXX"; }

section "install deploys the ingest script, gates, collectors, the job entrypoints and their LaunchAgents"
th_home="$(new_home)"
RT="$th_home/opt/agent-usage-tracker"
run_script install.sh "$th_home"
assert_status "exits 0" 0 "$TH_STATUS"
assert_file_exists "ingest script deployed" "$RT/bin/ingest-claude-statusline.sh"
[ -x "$RT/bin/ingest-claude-statusline.sh" ]
assert_status "ingest script is executable (agent-statusline checks -x)" 0 $?
for f in usage_db.py ingest_account_quota.py ingest_session_usage.py statusline_payload_reader.py \
    claude_quota_api_poller.py codex_quota_api_poller.py codex_plan_history_poller.py \
    telemetry_receiver.py transcript_reader.py; do
    assert_file_exists "$f deployed" "$RT/src/$f"
done
assert_not_contains "the ingest wrapper runs this machine's python, not a bare name" \
    "$(cat "$RT/bin/ingest-claude-statusline.sh")" '__PYTHON3__"'
assert_file_missing "no database before the first write" "$RT/data/claude/account_quotas.db"
assert_file_exists "data/claude/ created" "$RT/data/claude"
assert_file_exists "data/codex/ created" "$RT/data/codex"
assert_file_missing "adhoc_quotas_analysis/ is run-by-hand, never deployed" "$RT/adhoc_quotas_analysis"
for job in claude-quota:60 codex-quota:60 codex-plan-history:3600 transcripts:300 push:300; do
    label="com.jeanlescut.agent-usage-tracker.${job%%:*}"
    assert_file_exists "${job%%:*}.sh entrypoint deployed" "$RT/${job%%:*}.sh"
    assert_contains "$label runs its entrypoint with /bin/bash" "$(cat "$RT/$label.plist")" \
        "<string>/bin/bash</string>
        <string>$RT/${job%%:*}.sh</string>"
    assert_contains "$label every ${job#*:}s" "$(cat "$RT/$label.plist")" "<integer>${job#*:}</integer>"
    assert_eq "$label symlinked into ~/Library/LaunchAgents" "$RT/$label.plist" "$(readlink "$th_home/Library/LaunchAgents/$label.plist")"
done
assert_file_missing "no single pollers trigger any more" "$RT/com.jeanlescut.agent-usage-tracker.plist"
assert_not_contains "placeholders all rendered" "$(cat "$RT"/*.plist)" "__"
assert_file_missing "the settings merge helper runs from the repo, never deployed" "$RT/src/merge_claude_env.py"
assert_contains "receiver plist keeps it alive" "$(cat "$RT/com.jeanlescut.agent-usage-tracker.otel.plist")" "<key>KeepAlive</key>"
assert_eq "receiver plist symlinked into ~/Library/LaunchAgents" "$RT/com.jeanlescut.agent-usage-tracker.otel.plist" \
    "$(readlink "$th_home/Library/LaunchAgents/com.jeanlescut.agent-usage-tracker.otel.plist")"
assert_file_missing "no systemd units on macOS" "$th_home/.config/systemd"
assert_eq "telemetry env vars merged into ~/.claude/settings.json" \
    "$(jq -cS . "$REPO_ROOT/src/claude_telemetry_env.json")" "$(jq -cS .env "$th_home/.claude/settings.json")"
assert_file_missing "nothing of agent-statusline's is created" "$th_home/opt/agent-statusline"

section "idempotent re-run"
run_script install.sh "$th_home"
assert_status "exits 0" 0 "$TH_STATUS"
assert_not_contains "nothing re-installed on an unchanged re-run" "$TH_OUT" "[+]"

section "the deployed ingest script writes into the deployed runtime's databases"
printf '{"type":"assistant","timestamp":"2026-01-01T10:00:00Z"}\n' > "$th_home/t.jsonl"
printf '{"transcript_path":"%s","session_id":"s1","rate_limits":{"five_hour":{"used_percentage":12}}}' "$th_home/t.jsonl" \
    | HOME="$th_home" "$RT/bin/ingest-claude-statusline.sh"
assert_eq "one account row" "1" \
    "$(python3 -c "import sqlite3; print(sqlite3.connect('$RT/data/claude/account_quotas.db').execute('SELECT COUNT(*) FROM account_quotas').fetchone()[0])")"
assert_file_exists "state/quota/claude written" "$RT/state/quota/claude"
rm -rf "$th_home"

section "telemetry env merge leaves every other settings key alone"
th_home="$(new_home)"
mkdir -p "$th_home/.claude"
printf '{"theme":"dark","env":{"MY_VAR":"keep","OTEL_LOGS_EXPORTER":"console"}}\n' > "$th_home/.claude/settings.json"
run_script install.sh "$th_home"
assert_eq "other top-level keys untouched" "dark" "$(jq -r .theme "$th_home/.claude/settings.json")"
assert_eq "other env keys untouched" "keep" "$(jq -r .env.MY_VAR "$th_home/.claude/settings.json")"
assert_eq "an owned key is set to our value" "otlp" "$(jq -r .env.OTEL_LOGS_EXPORTER "$th_home/.claude/settings.json")"
# A value changed by hand after install is someone else's now - must survive.
jq '.env.OTEL_EXPORTER_OTLP_ENDPOINT = "http://elsewhere:4318"' "$th_home/.claude/settings.json" > "$th_home/s.tmp" \
    && mv "$th_home/s.tmp" "$th_home/.claude/settings.json"
run_script uninstall.sh "$th_home"
assert_eq "uninstall removes only our unchanged env keys" \
    '{"MY_VAR":"keep","OTEL_EXPORTER_OTLP_ENDPOINT":"http://elsewhere:4318"}' \
    "$(jq -cS .env "$th_home/.claude/settings.json")"
rm -rf "$th_home"

section "uninstalling a fresh install removes everything"
th_home="$(new_home)"
run_script install.sh "$th_home"
run_script uninstall.sh "$th_home"
assert_status "exits 0" 0 "$TH_STATUS"
for label in com.jeanlescut.agent-usage-tracker.otel com.jeanlescut.agent-usage-tracker.claude-quota com.jeanlescut.agent-usage-tracker.codex-quota \
    com.jeanlescut.agent-usage-tracker.codex-plan-history com.jeanlescut.agent-usage-tracker.transcripts com.jeanlescut.agent-usage-tracker.push; do
    assert_file_missing "$label symlink removed" "$th_home/Library/LaunchAgents/$label.plist"
done
assert_file_missing "the whole runtime dir is gone when data/ was never populated" "$th_home/opt/agent-usage-tracker"
assert_not_contains "no orphans reported" "$TH_OUT" "orphan files/dirs under"
rm -rf "$th_home"

section "uninstall preserves data/ and flags an unknown leftover"
th_home="$(new_home)"
RT="$th_home/opt/agent-usage-tracker"
run_script install.sh "$th_home"
printf 'db' > "$RT/data/claude/account_quotas.db"
mkdir -p "$RT/mystery-dir"
run_script uninstall.sh "$th_home"
assert_status "exits 0" 0 "$TH_STATUS"
assert_file_exists "data/ survives" "$RT/data/claude/account_quotas.db"
assert_contains "reports preserving data/" "$TH_OUT" "preserved"
assert_file_missing "deployed code gone" "$RT/src"
assert_contains "names the orphan" "$TH_OUT" "mystery-dir"
assert_file_exists "the orphan is left untouched" "$RT/mystery-dir"
rm -rf "$th_home"

section "uninstalling an already-clean machine is a harmless no-op"
th_home="$(new_home)"
run_script uninstall.sh "$th_home"
assert_status "exits 0" 0 "$TH_STATUS"
rm -rf "$th_home"

section "Linux (the VM): systemd --user units instead of LaunchAgents"
th_home="$(new_home)"
RT="$th_home/opt/agent-usage-tracker"
UNITS="$th_home/.config/systemd/user"
TH_OUT="$(HOME="$th_home" AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1 AGENT_USAGE_TRACKER_OS=Linux bash "$REPO_ROOT/install.sh" 2>&1)"
assert_status "exits 0" 0 $?
assert_file_missing "no LaunchAgents" "$th_home/Library/LaunchAgents"
for unit in com.jeanlescut.agent-usage-tracker.claude-quota.service com.jeanlescut.agent-usage-tracker.claude-quota.timer \
    com.jeanlescut.agent-usage-tracker.otel.service \
    com.jeanlescut.agent-usage-tracker.transcripts.service com.jeanlescut.agent-usage-tracker.transcripts.timer \
    com.jeanlescut.agent-usage-tracker.push.service com.jeanlescut.agent-usage-tracker.push.timer; do
    assert_eq "$unit: real file in the runtime, symlink in ~/.config/systemd/user" "$RT/$unit" "$(readlink "$UNITS/$unit")"
done
assert_contains "a job's service runs its entrypoint with /bin/bash" \
    "$(cat "$RT/com.jeanlescut.agent-usage-tracker.push.service")" "ExecStart=/bin/bash $RT/push.sh"
assert_file_exists "push.sh entrypoint deployed" "$RT/push.sh"
assert_file_missing "no Codex jobs on Linux" "$RT/com.jeanlescut.agent-usage-tracker.codex-quota.service"
assert_file_missing "no Codex entrypoint on Linux" "$RT/codex-quota.sh"
assert_not_contains "placeholders all rendered" "$(cat "$RT"/*.service "$RT"/*.timer)" "__"
assert_contains "the receiver restarts if it exits" "$(cat "$RT/com.jeanlescut.agent-usage-tracker.otel.service")" "Restart=always"
assert_contains "claude-quota ticks every 60s" "$(cat "$RT/com.jeanlescut.agent-usage-tracker.claude-quota.timer")" "OnUnitActiveSec=60s"
assert_contains "push every 5 min" "$(cat "$RT/com.jeanlescut.agent-usage-tracker.push.timer")" "OnUnitActiveSec=300s"
mkdir -p "$RT/central/some-mac/data/claude" && printf 'db' > "$RT/central/some-mac/data/claude/account_quotas.db"
TH_OUT="$(HOME="$th_home" AGENT_USAGE_TRACKER_SKIP_LAUNCHD=1 AGENT_USAGE_TRACKER_OS=Linux bash "$REPO_ROOT/uninstall.sh" 2>&1)"
assert_eq "uninstall removes the unit symlinks" "" "$(ls "$UNITS" 2>/dev/null)"
assert_file_exists "central/ survives (every machine's pushed history)" "$RT/central/some-mac/data/claude/account_quotas.db"
assert_not_contains "central/ is not an orphan" "$TH_OUT" "orphan files"
rm -rf "$th_home"

harness_summary
