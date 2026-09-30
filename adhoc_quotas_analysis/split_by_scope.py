#!/usr/bin/env python3
"""One-time, idempotent migration of the data/ directory (then
~/opt/agent-statusline/data/, ~/opt/agent-usage-tracker/data/ since the
2026-09-30 repo split) from the
per-provider logs to the per-agent, per-scope layout (2026-09-30):

    data/claude-quota-history.jsonl  ->  data/claude/account.jsonl
                                         + data/claude/<session-id>.jsonl
    data/claude-telemetry.jsonl      ->  data/claude/<session-id>.jsonl
    data/codex-quota-history.jsonl   ->  data/codex/account.jsonl

The rule it enforces (USAGE_DATA_REFERENCE.md §1): quota percent is
account-scope only. A session file never carries a percent under any name;
an account row never carries per-session cost.

Row mapping:
- Claude poller rows (`source: "claude"` or no source) and Codex rows
  (`codex`, `codex_plan_limit_history`) -> account.jsonl, byte-identical.
- Claude push rows (`claude_statusline`) without session fields (everything
  before 2026-09-30's deploy) -> account.jsonl, byte-identical. No session
  file is synthesised for them.
- Push rows with session fields -> an account row (the same row minus
  `session_cost_usd` / `prompt_cache`, with `session_id` renamed
  `observed_by_session`) plus a session row
  {ts, iso, source, observed_at, session_cost_usd, prompt_cache}.
- Telemetry rows -> the session file named by `attributes["session.id"]`,
  with `source: "claude_otel"` and `observed_at` (epoch s) added; rows with
  no usable session id go to data/_unattributed/claude-otel.jsonl.
- Lines that do not parse go, verbatim, to data/_archive/unparsed-<name>.

Mechanics, per old file still present in data/:
1. Rename it into data/_archive/<name>.<UTC stamp> (atomic, same
   filesystem). Nothing is deleted; remove the archive by hand once the
   acceptance checks pass. A writer still running old code recreates the
   old file on its next append - re-run this after deploying the new code
   to sweep those rows too.
2. Append each output row with its own O_APPEND write(), so rows from
   writers already running the new code are never interleaved mid-line.
3. Print the counts and check they reconcile.

A data/_archive/.in-progress marker guards against a crash mid-file: if
it exists, this refuses to run until the named archive is checked by hand.

Idempotent: once no old file is left in data/, a run is a no-op.

Usage: python3 split_by_scope.py [--data-dir ~/opt/agent-usage-tracker/data]
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")
SESSION_FIELDS = ("session_cost_usd", "prompt_cache")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "opt" / "agent-usage-tracker" / "data")
    data_dir = parser.parse_args().data_dir
    archive_dir = data_dir / "_archive"
    marker = archive_dir / ".in-progress"
    if marker.exists():
        sys.exit(f"refusing to run: {marker} exists - a previous run stopped mid-file "
                 f"({marker.read_text().strip()}). Check that archive against the new files by hand, then delete the marker.")

    d_fds = {}  # output path -> O_APPEND fd, opened lazily

    def append(path: Path, line: str) -> None:
        if path not in d_fds:
            path.parent.mkdir(parents=True, exist_ok=True)
            d_fds[path] = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(d_fds[path], (line.rstrip("\n") + "\n").encode())

    def compact(d: dict) -> str:
        return json.dumps(d, separators=(",", ":"))

    l_todo = [n for n in ("claude-quota-history.jsonl", "codex-quota-history.jsonl", "claude-telemetry.jsonl")
              if (data_dir / n).exists()]
    if not l_todo:
        print("nothing to migrate: no old-layout file left in", data_dir)
        return

    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    ok = True
    for name in l_todo:
        archived = archive_dir / f"{name}.{stamp}"
        (data_dir / name).rename(archived)
        marker.write_text(str(archived))
        d_n = {"input": 0, "account": 0, "session": 0, "unattributed": 0, "unparsed": 0, "with_session_fields": 0}

        with archived.open() as f:
            for line in f:
                if not line.strip():
                    continue
                d_n["input"] += 1
                try:
                    d_row = json.loads(line)
                except json.JSONDecodeError:
                    append(archive_dir / f"unparsed-{name}", line)
                    d_n["unparsed"] += 1
                    continue

                if name == "codex-quota-history.jsonl":
                    append(data_dir / "codex" / "account.jsonl", line)
                    d_n["account"] += 1

                elif name == "claude-telemetry.jsonl":
                    sid = str(d_row.get("attributes", {}).get("session.id") or "")
                    d_row = {"source": "claude_otel",
                             "observed_at": int(d_row.get("time_unix_nano", 0)) // 10**9, **d_row}
                    if SESSION_ID_RE.match(sid) and sid != "account":
                        append(data_dir / "claude" / f"{sid}.jsonl", compact(d_row))
                        d_n["session"] += 1
                    else:
                        append(data_dir / "_unattributed" / "claude-otel.jsonl", compact(d_row))
                        d_n["unattributed"] += 1

                else:  # claude-quota-history.jsonl
                    sid = d_row.get("session_id")
                    if d_row.get("source") != "claude_statusline" or not any(k in d_row for k in SESSION_FIELDS + ("session_id",)):
                        append(data_dir / "claude" / "account.jsonl", line)  # byte-identical
                        d_n["account"] += 1
                        continue
                    d_n["with_session_fields"] += 1
                    d_account = {k: v for k, v in d_row.items() if k not in SESSION_FIELDS and k != "session_id"}
                    if sid is not None:
                        d_account["observed_by_session"] = sid
                    append(data_dir / "claude" / "account.jsonl", compact(d_account))
                    d_n["account"] += 1
                    if isinstance(sid, str) and SESSION_ID_RE.match(sid) and sid != "account":
                        d_session = {k: d_row[k] for k in ("ts", "iso", "source", "observed_at") if k in d_row}
                        d_session.update({k: d_row.get(k) for k in SESSION_FIELDS})
                        append(data_dir / "claude" / f"{sid}.jsonl", compact(d_session))
                        d_n["session"] += 1

        # Reconciliation: every parsed input row lands in exactly one
        # account row, except telemetry, which is session-scope only; a
        # session row exists for every push row that carried session fields.
        if name == "claude-telemetry.jsonl":
            balanced = d_n["session"] + d_n["unattributed"] + d_n["unparsed"] == d_n["input"]
        else:
            balanced = d_n["account"] + d_n["unparsed"] == d_n["input"]
        if name == "claude-quota-history.jsonl":
            balanced = balanced and d_n["session"] <= d_n["with_session_fields"]
        ok = ok and balanced
        print(f"{name}: " + ", ".join(f"{k}={v}" for k, v in d_n.items() if v or k in ("input", "account"))
              + f" -> {'reconciled' if balanced else 'MISMATCH'}; original kept at {archived}")
        marker.unlink()

    for fd in d_fds.values():
        os.close(fd)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
