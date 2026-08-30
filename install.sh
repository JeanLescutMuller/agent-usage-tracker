#!/usr/bin/env bash
# Deploys the quota pollers to ~/opt/agent-quota-tracker and schedules them
# via a single launchd LaunchAgent (every 60s, running poll_all.py - see its
# docstring for why one job runs two subprocesses instead of two jobs).
# Each poller's own docstring covers why a 60s tick isn't a 60s poll rate:
# both self-throttle to roughly every 5 minutes while idle, and poll_claude.py
# additionally speeds back up to every tick while agent-statusline reports a
# live session. Idempotent - safe to re-run any time, including on a fresh
# machine. Self-migrating - detects any prior layout (oldest:
# ~/opt/utilization-tracker, com.jeanlescut.utilization-tracker;
# 2026-08-26: ~/opt/claude-utilization-tracker,
# com.jeanlescut.claude-utilization-tracker; later 2026-08-26:
# ~/opt/claude-utilization-cost-tracker, com.jeanlescut.claude-utilization-cost-tracker
# - renamed again 2026-08-27 to drop the Claude-specific name, since this
# project now also tracks other coding agents' quotas, e.g. Codex, which
# landed 2026-08-30 as poll_codex.py) and moves it to the current one,
# carrying data/utilization-log.jsonl forward (the one file here that
# can't be recomputed - see poll_claude.py's docstring).
#
# Usage: ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOLDER_PROD="$HOME/opt/agent-quota-tracker"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LABEL="com.jeanlescut.agent-quota-tracker"
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
migrate_legacy "$HOME/opt/claude-utilization-cost-tracker" "com.jeanlescut.claude-utilization-cost-tracker"

command -v python3 >/dev/null 2>&1 || { echo "python3 not found on PATH"; exit 1; }
PYTHON3="$(command -v python3)"

# Real files live in ~/opt/agent-quota-tracker/ (code + data +
# the plist itself); ~/Library/LaunchAgents/ only ever holds a symlink into it
# - see ~/dev/CLAUDE.md. Deliberately NOT wiping FOLDER_PROD before copying
# (unlike auto-commit's deploy script) - data/utilization-log.jsonl is the
# append-only log this tool exists to accumulate, and must survive a
# re-install (it can't be recomputed - see poll_claude.py's docstring).
mkdir -p "$FOLDER_PROD/data"
mkdir -p "$LAUNCH_AGENTS"

for f in poll_claude.py poll_codex.py poll_all.py; do
    cp "$SRC/$f" "$FOLDER_PROD/$f"
    chmod +x "$FOLDER_PROD/$f"
done
rm -f "$FOLDER_PROD/poll.py"  # stale pre-2026-08-30 name, before the poll_claude.py/poll_codex.py split

# Not scheduled - recompute_token_events.py is run by hand at analysis
# time, rebuilding data/token-events.jsonl fresh from the transcripts
# Claude Code already keeps under ~/.claude/projects/. Deployed here anyway
# so it's available wherever the pollers' data/ actually lives.
cp "$SRC/recompute_token_events.py" "$FOLDER_PROD/recompute_token_events.py"
chmod +x "$FOLDER_PROD/recompute_token_events.py"

# Same rationale, Codex side: rebuilds data/codex-token-events.jsonl from
# the token_count events already durable in ~/.codex/sessions/ - no API
# call needed (see AGENTS.md's "What this project is" correction note).
cp "$SRC/recompute_codex_events.py" "$FOLDER_PROD/recompute_codex_events.py"
chmod +x "$FOLDER_PROD/recompute_codex_events.py"

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
        <string>$FOLDER_PROD/poll_all.py</string>
    </array>

    <!-- Tick every 60s - NOT the same as polling every 60s. Both pollers
         self-throttle most of these ticks away (see each one's module
         docstring): poll_claude.py polls every tick while agent-statusline
         reports a live session, settling to ~5 min once idle; poll_codex.py
         has no live-session signal wired up and always settles to ~5 min. -->
    <key>StartInterval</key>
    <integer>60</integer>

    <!-- Take a reading immediately when the job is loaded, i.e. at login and
         every time this script re-bootstraps it - otherwise the first reading
         after a reboot is late, and ./install.sh gives no immediate signal
         that polling actually works.
         Note this does NOT address sleep gaps: StartInterval doesn't fire
         while the Mac is asleep, though launchd does fire once on wake. Those
         gaps are mostly benign (no local usage happens while asleep either). -->
    <key>RunAtLoad</key>
    <true/>

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

echo "Deployed $REAL_PLIST (symlinked from $LINK_PLIST), ticking every 60s"
echo "(Claude polls speed up to ~60s while a statusline is live, both settle to ~5 min otherwise)."
echo "Log: $FOLDER_PROD/data/utilization-log.jsonl"
echo "Check status: launchctl print gui/$(id -u)/$LABEL"
