#!/usr/bin/env bash
# Deploys the utilization/cost poller to ~/opt/claude-utilization-cost-tracker
# and schedules it via a launchd LaunchAgent (every 5 minutes). Idempotent -
# safe to re-run any time, including on a fresh machine. Self-migrating -
# detects either prior layout (oldest: ~/opt/utilization-tracker,
# com.jeanlescut.utilization-tracker; pre-2026-08-26 rename:
# ~/opt/claude-utilization-tracker, com.jeanlescut.claude-utilization-tracker)
# and moves it to the current one, carrying data/utilization-log.jsonl
# forward (the one file here that can't be recomputed - see poll.py's
# docstring).
#
# Usage: ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOLDER_PROD="$HOME/opt/claude-utilization-cost-tracker"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LABEL="com.jeanlescut.claude-utilization-cost-tracker"
REAL_PLIST="$FOLDER_PROD/$LABEL.plist"
LINK_PLIST="$LAUNCH_AGENTS/$LABEL.plist"

# --- one-time migration off each prior name, oldest first ---
migrate_legacy() {
    local old_folder="$1" old_label="$2"
    local old_link_plist="$LAUNCH_AGENTS/$old_label.plist"
    if [ -d "$old_folder" ]; then
        echo "Migrating $old_folder -> $FOLDER_PROD..."
        launchctl bootout "gui/$(id -u)" "$old_link_plist" 2>/dev/null || true
        rm -f "$old_link_plist"
        mkdir -p "$FOLDER_PROD"
        if [ -d "$old_folder/data" ]; then
            mkdir -p "$FOLDER_PROD/data"
            cp -n "$old_folder/data/"* "$FOLDER_PROD/data/" 2>/dev/null || true
        fi
        rm -rf "$old_folder"
    fi
}
migrate_legacy "$HOME/opt/utilization-tracker" "com.jeanlescut.utilization-tracker"
migrate_legacy "$HOME/opt/claude-utilization-tracker" "com.jeanlescut.claude-utilization-tracker"

command -v python3 >/dev/null 2>&1 || { echo "python3 not found on PATH"; exit 1; }
PYTHON3="$(command -v python3)"

# Real files live in ~/opt/claude-utilization-cost-tracker/ (code + data +
# the plist itself); ~/Library/LaunchAgents/ only ever holds a symlink into it
# - see ~/dev/CLAUDE.md. Deliberately NOT wiping FOLDER_PROD before copying
# (unlike auto-commit's deploy script) - data/utilization-log.jsonl is the
# append-only log this tool exists to accumulate, and must survive a
# re-install (it can't be recomputed - see poll.py's docstring).
mkdir -p "$FOLDER_PROD/data"
mkdir -p "$LAUNCH_AGENTS"

cp "$SRC/poll.py" "$FOLDER_PROD/poll.py"
chmod +x "$FOLDER_PROD/poll.py"

# Not scheduled - recompute_token_events.py is run by hand at analysis
# time, rebuilding data/token-events.jsonl fresh from the transcripts
# Claude Code already keeps under ~/.claude/projects/. Deployed here anyway
# so it's available wherever poll.py's data/ actually lives.
cp "$SRC/recompute_token_events.py" "$FOLDER_PROD/recompute_token_events.py"
chmod +x "$FOLDER_PROD/recompute_token_events.py"

echo "(Over-)writing $REAL_PLIST..."
tee "$REAL_PLIST" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON3</string>
        <string>$FOLDER_PROD/poll.py</string>
    </array>

    <!-- Run every 5 minutes -->
    <key>StartInterval</key>
    <integer>300</integer>

    <key>StandardOutPath</key>
    <string>$FOLDER_PROD/poll.log</string>
    <key>StandardErrorPath</key>
    <string>$FOLDER_PROD/poll.err</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF

ln -sf "$REAL_PLIST" "$LINK_PLIST"

echo "Bootout..."
launchctl bootout "gui/$(id -u)" "$LINK_PLIST" 2>/dev/null || true

echo "Bootstrap..."
launchctl bootstrap "gui/$(id -u)" "$LINK_PLIST"

echo "Deployed $REAL_PLIST (symlinked from $LINK_PLIST), polling every 5 min."
echo "Log: $FOLDER_PROD/data/utilization-log.jsonl"
echo "Check status: launchctl print gui/$(id -u)/$LABEL"
