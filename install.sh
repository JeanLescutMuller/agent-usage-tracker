#!/usr/bin/env bash
# Deploys the utilization poller to ~/opt/utilization-tracker and schedules
# it via a launchd LaunchAgent (every 5 minutes). Idempotent - safe to
# re-run any time, including on a fresh machine.
#
# Usage: ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOLDER_PROD="$HOME/opt/utilization-tracker"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LABEL="com.jeanlescut.utilization-tracker"
REAL_PLIST="$FOLDER_PROD/$LABEL.plist"
LINK_PLIST="$LAUNCH_AGENTS/$LABEL.plist"

command -v python3 >/dev/null 2>&1 || { echo "python3 not found on PATH"; exit 1; }
PYTHON3="$(command -v python3)"

# Real files live in ~/opt/utilization-tracker/ (code + data + the plist
# itself); ~/Library/LaunchAgents/ only ever holds a symlink into it - see
# ~/dev/CLAUDE.md. Deliberately NOT wiping FOLDER_PROD before copying (unlike
# auto-commit's deploy script) - data/ holds the append-only log and byte
# offsets this tool exists to accumulate, and must survive a re-install.
mkdir -p "$FOLDER_PROD/data"
mkdir -p "$LAUNCH_AGENTS"

cp "$SRC/poll.py" "$FOLDER_PROD/poll.py"
chmod +x "$FOLDER_PROD/poll.py"

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
