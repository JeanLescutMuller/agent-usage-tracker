"""What every analysis here reads, loaded the way FOR_READERS.md says: read-only,
from the runtime (~/opt/agent-usage-tracker, or AGENT_USAGE_TRACKER_RUNTIME).

  readings()  Category 1: every Claude quota reading, both machines' (the same rows on each)
  windows()   the 5-hour or 7-day windows rebuilt from the readings, one per plan x reset time
  slots()     Category 3: the 5-min slots of every machine this runtime holds,
              data/claude/aggregates/<machine>.db (the Mac: its own; the VM: its own + the Mac's)

Claude only. Nothing here computes a $: `usd` is Claude Code's own, where it exists.
"""
import datetime as dt
import os
import re
import sqlite3
from pathlib import Path

RUNTIME = Path(os.environ.get("AGENT_USAGE_TRACKER_RUNTIME") or "~/opt/agent-usage-tracker").expanduser()
SPAN_S = {"5h": 5 * 3600, "7d": 7 * 86400}
# Reset times jitter by a second between readings (…8999 / …9000) and Claude rounds a 5-hour end to
# 10 min, a 7-day end to the hour: one window = one reset time on this grid.
GRID_S = 600


def epoch(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def fmt(ts: float | None) -> str:
    return "-" if ts is None else dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%m-%d %H:%M")


# The account's plan over time (doc/changes.md). A percent of one plan is not comparable with
# another's: windows, budgets and ratios are always per plan. Add a row when the plan changes.
#   pro+promo  Claude Pro with Anthropic's +50% weekly promo, which ended 2026-08-31 23:59 PT (the 7-day meter
#              only; it did not reset the meter, so the 7-day window that week is cut in two)
#   max5x      Claude Max 5x from 2026-10-07 about 16:51Z: both meters went to 0% mid-window
PLANS = [(0, "pro+promo"), (epoch("2026-09-01T06:59:00Z"), "pro"), (epoch("2026-10-07T16:51:00Z"), "max5x")]


def plan_at(ts: float) -> str:
    return [name for start, name in PLANS if ts >= start][-1]


def plan_bounds(plan: str) -> tuple[int, float]:
    """[start, end) of a plan's period."""
    i = [name for _, name in PLANS].index(plan)
    return PLANS[i][0], PLANS[i + 1][0] if i + 1 < len(PLANS) else float("inf")


def connect(path: Path):
    """FOR_READERS.md §1: query_only, never a mode=ro URI; a busy timeout."""
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.execute("PRAGMA busy_timeout = 10000")
    db.execute("PRAGMA query_only = ON")
    return db


def readings() -> list[dict]:
    """Every reading, oldest first: ts, source, machine, pct and end (snapped reset time, None = the reading
    says "no window") per window kind. `claude_api` is the server's answer; a `claude_statusline` reading is a
    session's copy, so its None only means that copy expired, never that no window runs."""
    db = connect(RUNTIME / "data/claude/account_quotas.db")
    snap = lambda end: None if end is None else round(end / GRID_S) * GRID_S
    return [dict(ts=ts, source=source, machine=machine, plan=plan_at(ts),
                 pct={"5h": p5, "7d": p7}, end={"5h": snap(e5), "7d": snap(e7)})
            for ts, source, machine, p5, e5, p7, e7 in db.execute(
                "SELECT ts, source, machine, five_hour_pct, five_hour_resets_ts, seven_day_pct, seven_day_resets_ts "
                "FROM account_quotas ORDER BY ts, rowid")]


def windows(l_readings: list[dict], kind: str) -> list[dict]:
    """The windows of one kind ("5h" or "7d"), oldest first, one per (plan, reset time):
      start, end    end - span .. end, cut to the plan's period (the plan change reset both meters mid-window;
                    `cut` is then True)
      peak          the highest percent read
      steps         [(ts, pct)]: the first reading of each new highest percent. Within one window the true
                    percent never decreases, so a reading at or below the running highest is stale (an idle
                    session's old copy) or a repeat: dropping them makes the dense statusline readings usable."""
    d_win = {}
    for d_r in l_readings:
        end, pct = d_r["end"][kind], d_r["pct"][kind]
        if end is None or pct is None:
            continue
        d_w = d_win.setdefault((d_r["plan"], end), dict(plan=d_r["plan"], end=end, l_ts_pct=[]))
        d_w["l_ts_pct"].append((d_r["ts"], pct))
    l_out = []
    for d_w in d_win.values():
        lo, hi = plan_bounds(d_w["plan"])
        start, end = max(d_w["end"] - SPAN_S[kind], lo), min(d_w["end"], hi)
        d_w.update(start=start, end=end, cut=(start, end) != (d_w["end"] - SPAN_S[kind], d_w["end"]))
        d_w["steps"], top = [], -1
        for ts, pct in sorted(d_w.pop("l_ts_pct")):
            if pct > top:
                d_w["steps"].append((ts, pct))
                top = pct
        d_w["peak"] = top
        l_out.append(d_w)
    return sorted(l_out, key=lambda d_w: (d_w["end"], d_w["start"]))


TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_5m_tokens",
                 "cache_write_1h_tokens", "cache_write_unknown_tokens")


def slots() -> list[dict]:
    """Every slot this runtime holds, oldest first, plus `tokens` (all six token columns). `<synthetic>` rows
    are Claude Code's own error stubs (a rejected request): no quota used, no window opened, left out."""
    l_paths = sorted(RUNTIME.glob("data/claude/aggregates/*.db"))
    l_out = []
    for path in l_paths:
        if not path.exists():
            continue
        db = connect(path)
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM slots WHERE model != '<synthetic>'"):
            d_s = dict(row)
            d_s["tokens"] = sum(d_s[c] for c in TOKEN_COLUMNS)
            l_out.append(d_s)
    return sorted(l_out, key=lambda d_s: d_s["ts"])


def project(folder: str) -> str:
    """A slot's folder reduced to its repo: ~/dev/<x>/… or ~/opt/<x>/… -> <x>; a Claude Code scratchpad
    (/private/tmp/claude-<uid>/-Users-<me>-dev-<x>/…) -> <x>; the home folder and ~/dev itself -> "~", "~/dev";
    no folder (telemetry of a session without transcript) -> "?"; anything else as it is."""
    if not folder:
        return "?"
    m = re.match(r"^/(?:Users|home)/[^/]+(/.*)?$", folder)
    rest = (m.group(1) or "") if m else None
    if rest is not None:
        m2 = re.match(r"^/(?:dev|opt)/([^/]+)", rest)
        return m2.group(1) if m2 else "~" + rest
    m = re.match(r"^/private/tmp/claude-\d+/-(?:Users|home)-[^/]*?-(?:dev|opt)-([^/]+)", folder)
    return m.group(1) if m else folder


def kind(folder: str, entrypoint: str) -> str:
    """The kind of use (as Jean asked on 2026-10-07, `TODO/0005`): `cli` = development (interactive);
    `sdk-*` (claude -p, the Agent SDK) under ~/opt/ = scheduled run, elsewhere = test run; no entrypoint = "?"."""
    if not entrypoint:
        return "?"
    if not entrypoint.startswith("sdk"):
        return "development"
    return "scheduled run" if re.match(r"^/(?:Users|home)/[^/]+/opt/", folder or "") else "test run"


def quantiles(l_vals: list[float]) -> str:
    """median [p10-p90]"""
    l_v = sorted(l_vals)
    q = lambda f: l_v[min(len(l_v) - 1, round(f * (len(l_v) - 1)))]
    return f"{q(0.5):.2f} [{q(0.1):.2f}-{q(0.9):.2f}]" if l_v else "-"
