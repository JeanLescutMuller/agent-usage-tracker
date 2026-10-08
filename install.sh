#!/bin/bash
# Idempotent installer for agent-usage-tracker, for a machine with no prior
# install. Carries no one-time migration logic on purpose: to move between
# incompatible on-disk layouts, run uninstall.sh first - it removes
# everything this script deploys, preserves data/ (irreplaceable usage
# history), and flags anything left over as an orphan to check by hand -
# then re-run this script.
#
# Deploys, under ~/opt/agent-usage-tracker:
# - bin/ingest-claude-statusline.sh, the entry point the Claude statusline
#   (the separate agent-statusline project) pipes its stdin payload into;
# - src/*.py: the two gates (ingest_*.py, the only writers of the
#   databases), the collectors, and usage_db.py;
# - three LaunchAgents: the pollers (60s tick), the telemetry receiver
#   (KeepAlive) and the transcript reader (every 5 min), plus the telemetry
#   keys in ~/.claude/settings.json's `env`.
# The databases (data/<agent>/account_quotas.db, sessions_usages.db) are
# created by the gates on first write.
# Does NOT deploy adhoc_quotas_analysis/: run-by-hand research tooling stays
# in ~/dev/agent-usage-tracker and runs from there (~/opt is for what a
# scheduler runs unattended).
#
# Independent of agent-statusline: either can be installed first, or alone.
# Without the statusline there are no push rows and the Claude poller stays
# on its idle cadence (README.md's "Contract with agent-statusline").
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

command -v python3 >/dev/null 2>&1 || { echo "python3 not found on PATH"; exit 1; }
PYTHON3="$(command -v python3)"

RUNTIME="$HOME/opt/agent-usage-tracker"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} agent-usage-tracker install${NC}"
echo -e "${GREEN}========================================${NC}"

_deploy() {
    local src="$1" target="$2"
    mkdir -p "$(dirname "$target")"
    if [ -f "$target" ] && diff -q "$src" "$target" >/dev/null 2>&1; then
        ok "$(basename "$target")"
        return
    fi
    cp "$src" "$target"
    chmod +x "$target" 2>/dev/null || true
    installed "$(basename "$target")"
}

# Like _deploy, with __PYTHON3__ replaced by this machine's interpreter.
_deploy_rendered() {
    local src="$1" target="$2" tmp
    tmp="$(mktemp)"
    sed -e "s#__PYTHON3__#$PYTHON3#g" "$src" > "$tmp"
    _deploy "$tmp" "$target"
    rm -f "$tmp"
}

# Renders a LaunchAgent template into the runtime tree (the real file) and
# symlinks it from ~/Library/LaunchAgents, then (re)loads it - always
# restarting, so redeployed code takes effect.
_launch_agent() {
    local template="$1" label="$2" what="$3"
    local real="$RUNTIME/$label.plist" link="$LAUNCH_AGENTS/$label.plist" tmp
    mkdir -p "$LAUNCH_AGENTS"
    tmp="$(mktemp)"
    sed -e "s#__PYTHON3__#$PYTHON3#g" -e "s#__RUNTIME__#$RUNTIME#g" "$template" > "$tmp"
    if [ -f "$real" ] && diff -q "$tmp" "$real" >/dev/null 2>&1; then
        rm -f "$tmp"
        ok "$label"
    else
        mv "$tmp" "$real"
        installed "$label ($what)"
    fi
    ln -sf "$real" "$link"
    if [ -z "${AGENT_USAGE_TRACKER_SKIP_LAUNCHD:-}" ]; then
        launchctl bootout "gui/$(id -u)" "$link" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$link"
    fi
}

step "runtime layout"
# data/<agent>/account_quotas.db + sessions_usages.db - see
# USAGE_DATA_REFERENCE.md §1.
mkdir -p "$RUNTIME/data/claude" "$RUNTIME/data/codex" "$RUNTIME/state" "$RUNTIME/logs"
ok "data/, state/, logs/"

step "code"
_deploy_rendered "$SCRIPT_DIR/bin/ingest-claude-statusline.sh" "$RUNTIME/bin/ingest-claude-statusline.sh"
for f in "$SCRIPT_DIR"/src/*.py; do
    # The settings merge helper runs from the repo (below), never deployed.
    [ "$(basename "$f")" = merge_claude_env.py ] && continue
    _deploy "$f" "$RUNTIME/src/$(basename "$f")"
done

step "LaunchAgents"
_launch_agent "$SCRIPT_DIR/src/launchd/com.jeanlescut.agent-usage-tracker.plist.template" \
    com.jeanlescut.agent-usage-tracker "pollers: ticks every 60s, every poller self-throttles"
_launch_agent "$SCRIPT_DIR/src/launchd/com.jeanlescut.agent-usage-tracker.otel.plist.template" \
    com.jeanlescut.agent-usage-tracker.otel "telemetry receiver on 127.0.0.1:4318"
_launch_agent "$SCRIPT_DIR/src/launchd/com.jeanlescut.agent-usage-tracker.transcripts.plist.template" \
    com.jeanlescut.agent-usage-tracker.transcripts "transcript reader, every 5 min"

step "claude telemetry settings"
# Only the keys in src/claude_telemetry_env.json, inside `env`.
# Takes effect for Claude sessions started after this.
case "$("$PYTHON3" "$SCRIPT_DIR/src/merge_claude_env.py" set)" in
    changed) installed "telemetry env vars in ~/.claude/settings.json (new sessions only)" ;;
    unchanged) ok "telemetry env vars in ~/.claude/settings.json" ;;
    *) fail "telemetry env vars in ~/.claude/settings.json" ;;
esac

echo ""
