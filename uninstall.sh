#!/bin/bash
# Removes everything install.sh deploys: the jobs' entrypoints and triggers
# and the telemetry receiver (LaunchAgents or systemd --user units), the telemetry
# keys in ~/.claude/settings.json's `env` (only those still holding our
# values), and the code/state/logs under ~/opt/agent-usage-tracker.
#
# Deliberately preserves data/ - irreplaceable usage history, not
# reproducible if deleted - and, on the VM, central/ (every machine's pushed
# copy). Remove them yourself if you really want them gone.
#
# With this uninstalled, agent-statusline keeps working: its Claude render
# finds no ingest script to pipe into and no state/quota/claude to read, and
# falls back to the rate_limits on its own stdin.
#
# Anything left under ~/opt/agent-usage-tracker after that is an orphan -
# not something this script recognizes - and gets listed for you to check
# by hand rather than being silently deleted or ignored.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

RUNTIME="$HOME/opt/agent-usage-tracker"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
JOBS="claude-quota codex-quota codex-plan-history transcripts push"   # as in install.sh
LABELS="com.jeanlescut.agent-usage-tracker.otel"
for job in $JOBS; do LABELS="$LABELS com.jeanlescut.agent-usage-tracker.$job"; done
SYSTEMD_USER="$HOME/.config/systemd/user"
OS="${AGENT_USAGE_TRACKER_OS:-$(uname -s)}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} agent-usage-tracker uninstall${NC}"
echo -e "${GREEN}========================================${NC}"

step "scheduled jobs"
for label in $LABELS; do
    if [ "$OS" = Darwin ]; then
        if [ -L "$LAUNCH_AGENTS/$label.plist" ] || [ -f "$LAUNCH_AGENTS/$label.plist" ] || [ -f "$RUNTIME/$label.plist" ]; then
            [ -n "${AGENT_USAGE_TRACKER_SKIP_LAUNCHD:-}" ] || \
                launchctl bootout "gui/$(id -u)" "$LAUNCH_AGENTS/$label.plist" 2>/dev/null || true
            rm -f "$LAUNCH_AGENTS/$label.plist" "$RUNTIME/$label.plist"
            installed "unloaded and removed $label"
        else
            ok "$label already absent"
        fi
    else
        if [ -e "$SYSTEMD_USER/$label.service" ] || [ -L "$SYSTEMD_USER/$label.service" ] || [ -f "$RUNTIME/$label.service" ]; then
            [ -n "${AGENT_USAGE_TRACKER_SKIP_LAUNCHD:-}" ] || \
                systemctl --user disable --now "$label.timer" "$label.service" >/dev/null 2>&1 || true
            rm -f "$SYSTEMD_USER/$label.service" "$SYSTEMD_USER/$label.timer" "$RUNTIME/$label.service" "$RUNTIME/$label.timer"
            installed "stopped and removed $label"
        else
            ok "$label already absent"
        fi
    fi
done
[ "$OS" = Darwin ] || [ -n "${AGENT_USAGE_TRACKER_SKIP_LAUNCHD:-}" ] || systemctl --user daemon-reload 2>/dev/null || true

step "claude telemetry settings"
case "$(python3 "$SCRIPT_DIR/src/merge_claude_env.py" unset)" in
    changed) installed "removed the telemetry env vars from ~/.claude/settings.json" ;;
    *) ok "telemetry env vars already absent" ;;
esac

step "runtime tree ($RUNTIME)"
if [ -d "$RUNTIME" ]; then
    # Nothing here is written on every render except state/quota/claude (an
    # atomic replace) and the databases under data/ (kept), so a plain
    # rm -rf is enough - no retry loop needed.
    known_targets="bin src state logs"
    for target in $known_targets; do
        rm -rf "${RUNTIME:?}/$target"
    done
    for job in $JOBS; do
        rm -f "${RUNTIME:?}/$job.sh"
    done
    installed "removed deployed code, state and logs"
    # rmdir only succeeds on an empty directory - real history is kept.
    rmdir "$RUNTIME/data/claude" "$RUNTIME/data/codex" 2>/dev/null
    rmdir "$RUNTIME/data" 2>/dev/null
    [ -d "$RUNTIME/data" ] && skip "preserved $RUNTIME/data (irreplaceable usage history)"
    # The central store (on the VM): every machine's pushed history.
    [ -d "$RUNTIME/central" ] && skip "preserved $RUNTIME/central (every machine's pushed usage history)"

    # A known target back already means a live Claude render ran the ingest
    # script mid-uninstall (it mkdirs state/quota/) - not an orphan.
    orphans="$(find "$RUNTIME" -mindepth 1 -maxdepth 1 ! -name data ! -name central \
        ! -name bin ! -name src ! -name state ! -name logs 2>/dev/null)"
    if [ -n "$orphans" ]; then
        fail "orphan files/dirs under $RUNTIME - not recognized by this script, check by hand:"
        printf '%s\n' "$orphans" | sed 's/^/      /'
    else
        rmdir "$RUNTIME" 2>/dev/null || true
        ok "no orphans found"
    fi
else
    ok "$RUNTIME already absent"
fi

echo ""
