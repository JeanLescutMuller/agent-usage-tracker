#!/usr/bin/env python3
"""Adds or removes this project's telemetry variables in the `env` object of
~/.claude/settings.json, touching nothing else.

agent-usage-tracker owns only the keys listed in claude_telemetry_env.json;
other `env` keys and every other top-level key belong to other tools (see
the claude-config-ownership skill) and are left exactly as they are.

Usage: merge_claude_env.py set|unset
Env:   CLAUDE_SETTINGS (default ~/.claude/settings.json)
Prints one of: changed, unchanged, absent.
"""
import json
import os
import sys
from pathlib import Path

settings_path = Path(os.environ.get("CLAUDE_SETTINGS", Path.home() / ".claude" / "settings.json"))
d_owned = json.loads((Path(__file__).resolve().parent / "claude_telemetry_env.json").read_text())
mode = sys.argv[1]

if not settings_path.exists():
    if mode == "unset":
        print("absent")
        sys.exit(0)
    d_settings = {}
else:
    d_settings = json.loads(settings_path.read_text())

d_env = dict(d_settings.get("env") or {})
if mode == "set":
    d_env.update(d_owned)
else:
    # Only remove a key if it still holds our value - if someone changed it
    # by hand, it is theirs now.
    for key, value in d_owned.items():
        if d_env.get(key) == value:
            del d_env[key]

d_new = dict(d_settings)
if d_env:
    d_new["env"] = d_env
else:
    d_new.pop("env", None)

if d_new == d_settings:
    print("unchanged")
else:
    # Write via a temp file + rename so a concurrent Claude Code start never
    # reads a half-written settings file.
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(d_new, indent=2) + "\n")
    tmp.replace(settings_path)
    print("changed")
