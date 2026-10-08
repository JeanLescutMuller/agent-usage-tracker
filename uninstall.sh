#!/bin/bash
# Removes everything install.sh deploys: both LaunchAgents, the telemetry
# keys in ~/.claude/settings.json's `env` (only those still holding our
# values), and the code/state/logs under ~/opt/agent-usage-tracker.
#
# Deliberately preserves data/ - irreplaceable usage history, not
# reproducible if deleted. Remove it yourself if you really want it gone.
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
LABELS="com.jeanlescut.agent-usage-tracker com.jeanlescut.agent-usage-tracker.otel com.jeanlescut.agent-usage-tracker.transcripts"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} agent-usage-tracker uninstall${NC}"
echo -e "${GREEN}========================================${NC}"

step "LaunchAgents"
for label in $LABELS; do
    if [ -L "$LAUNCH_AGENTS/$label.plist" ] || [ -f "$LAUNCH_AGENTS/$label.plist" ] || [ -f "$RUNTIME/$label.plist" ]; then
        [ -n "${AGENT_USAGE_TRACKER_SKIP_LAUNCHD:-}" ] || \
            launchctl bootout "gui/$(id -u)" "$LAUNCH_AGENTS/$label.plist" 2>/dev/null || true
        rm -f "$LAUNCH_AGENTS/$label.plist" "$RUNTIME/$label.plist"
        installed "unloaded and removed $label"
    else
        ok "$label already absent"
    fi
done

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
    installed "removed deployed code, state and logs"
    # rmdir only succeeds on an empty directory - real history is kept.
    rmdir "$RUNTIME/data/claude" "$RUNTIME/data/codex" 2>/dev/null
    rmdir "$RUNTIME/data" 2>/dev/null
    [ -d "$RUNTIME/data" ] && skip "preserved $RUNTIME/data (irreplaceable usage history)"

    # A known target back already means a live Claude render ran the ingest
    # script mid-uninstall (it mkdirs state/quota/) - not an orphan.
    orphans="$(find "$RUNTIME" -mindepth 1 -maxdepth 1 ! -name data \
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
