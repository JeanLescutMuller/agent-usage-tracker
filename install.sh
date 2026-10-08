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
# - four scheduled jobs: the pollers (60s tick), the telemetry receiver
#   (always running), the transcript reader and the push to the central
#   store on the VM (every 5 min) - LaunchAgents on macOS, systemd --user
#   units on Linux (the VM) - plus the telemetry keys in
#   ~/.claude/settings.json's `env`.
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
SYSTEMD_USER="$HOME/.config/systemd/user"
# Darwin -> LaunchAgents, anything else -> systemd --user. Overridable so the
# tests can render the Linux units on a Mac.
OS="${AGENT_USAGE_TRACKER_OS:-$(uname -s)}"

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

# Renders a systemd --user unit (and its timer, if the job has one) into the
# runtime tree (the real files), symlinks them from ~/.config/systemd/user,
# then enables and (re)starts them - always restarting, so redeployed code
# takes effect. Linger lets them run without a login session (best effort).
_systemd_unit() {
    local label="$1" what="$2" kind real link tmp changed=false
    mkdir -p "$SYSTEMD_USER"
    for kind in service timer; do
        [ -f "$SCRIPT_DIR/src/systemd/$label.$kind" ] || continue
        real="$RUNTIME/$label.$kind" link="$SYSTEMD_USER/$label.$kind"
        tmp="$(mktemp)"
        sed -e "s#__PYTHON3__#$PYTHON3#g" -e "s#__RUNTIME__#$RUNTIME#g" "$SCRIPT_DIR/src/systemd/$label.$kind" > "$tmp"
        if [ -f "$real" ] && diff -q "$tmp" "$real" >/dev/null 2>&1; then
            rm -f "$tmp"
        else
            mv "$tmp" "$real"
            changed=true
        fi
        ln -sf "$real" "$link"
    done
    if [ "$changed" = true ]; then installed "$label ($what)"; else ok "$label"; fi
    if [ -z "${AGENT_USAGE_TRACKER_SKIP_LAUNCHD:-}" ]; then
        systemctl --user daemon-reload
        if [ -f "$RUNTIME/$label.timer" ]; then
            systemctl --user enable --now "$label.timer" >/dev/null 2>&1
        else
            systemctl --user enable "$label.service" >/dev/null 2>&1
            systemctl --user restart "$label.service"
        fi
    fi
}

_schedule() {
    if [ "$OS" = Darwin ]; then
        _launch_agent "$SCRIPT_DIR/src/launchd/$1.plist.template" "$1" "$2"
    else
        _systemd_unit "$1" "$2"
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

step "scheduled jobs ($([ "$OS" = Darwin ] && echo LaunchAgents || echo 'systemd --user'))"
_schedule com.jeanlescut.agent-usage-tracker "pollers: ticks every 60s, every poller self-throttles"
_schedule com.jeanlescut.agent-usage-tracker.otel "telemetry receiver on 127.0.0.1:4318"
_schedule com.jeanlescut.agent-usage-tracker.transcripts "transcript reader, every 5 min"
_schedule com.jeanlescut.agent-usage-tracker.push "push to the central store on the VM, every 5 min"

if [ "$OS" != Darwin ] && [ -z "${AGENT_USAGE_TRACKER_SKIP_LAUNCHD:-}" ]; then
    loginctl enable-linger "$(id -un)" 2>/dev/null || true
fi

step "claude telemetry settings"
# Only the keys in src/claude_telemetry_env.json, inside `env`.
# Takes effect for Claude sessions started after this.
case "$("$PYTHON3" "$SCRIPT_DIR/src/merge_claude_env.py" set)" in
    changed) installed "telemetry env vars in ~/.claude/settings.json (new sessions only)" ;;
    unchanged) ok "telemetry env vars in ~/.claude/settings.json" ;;
    *) fail "telemetry env vars in ~/.claude/settings.json" ;;
esac

echo ""
