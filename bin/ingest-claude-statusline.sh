#!/bin/bash
# The Claude statusline's ingest point into agent-usage-tracker. The
# statusline (the separate agent-statusline project) pipes its raw stdin
# payload - the JSON Claude Code hands every statusline render - into this
# script, unchanged, on every render. That payload is the only free,
# per-render source of Claude's quota percent (USAGE_DATA_SOURCES.md §3.1),
# which is why the statusline forwards it rather than this project polling.
#
# The contract with agent-statusline is deliberately that small: it knows
# this script's path and nothing else about the payload's fields, the data
# layout, or the state file's writer side. It reads back only
# state/quota/claude (see README.md's "Contract with agent-statusline").
#
# The payload carries no timestamp, and an idle session re-renders the same
# frozen rate_limits for days. So a reading is dated by the transcript's
# last `assistant` entry - the last API response, the only thing that
# updates rate_limits - and a reading with no such entry is not written
# anywhere: no date is better than a wrong one (see observed_at below).
#
# With a date, and only then, whenever the payload has rate_limits (no-op
# otherwise - a session that hasn't sent a message yet):
#   1. Append a row to the append-only account-scope history,
#      data/claude/account.jsonl (shared with poll_claude.py, disambiguated
#      by `source`), and a row to this session's own session-scope file,
#      data/claude/<session_id>.jsonl - each only when it differs from the
#      last one this session wrote (dedup below), so re-renders of the same
#      reading add nothing.
#   2. Update state/quota/claude (write_quota_if_newer below), source
#      "claude_statusline" (the rows' own source name), only if this reading is actually newer than whatever's
#      already there, so a slow/delayed render or an idle session can't
#      regress a fresher poll or another session's more recent push.
#
# Free: rides `rate_limits`, already present on every render's stdin
# payload, no network call of its own. See adhoc_quotas_analysis/AGENTS.md's
# "GET /api/oauth/usage 429s" investigation for why this exists - the
# poller's endpoint is unreliable (~21% 429 rate), this path never is.
#
# Runs synchronously on every render, before the statusline reads the state
# file back, so a render displays its own reading. Always exits 0: a failure
# here must never break the visible statusline.
#
# The scope split (USAGE_DATA_REFERENCE.md §1) is a hard rule: quota
# percent is account-scope only - the meter is one account-level number, so
# a percent in a session file would be read as "this session's usage", which
# is undefined. Hence:
#   - account row: the percents and resets, plus `observed_by_session` (the
#     session that was rendering when the reading was taken - who observed
#     it, not whose usage it is). No per-session cost.
#   - session row: session_cost_usd (the payload's cumulative
#     cost.total_cost_usd), model_id, and the raw `prompt_cache` object
#     (session cache statistics and miss diagnostics, persisted nowhere else
#     - USAGE_DATA_SOURCES.md §3.1; `null` when absent). Never a percent.
#     Written only when session_id is a plain UUID-like token, since it
#     becomes a file name.
# The two scopes join on observed_at.
#
# Percents arrive unrounded (Claude Code sends floats): the account row
# keeps them as-is, the state file gets them rounded, since the statusline
# that reads it for display does integer arithmetic.
#
# Usage: <claude statusline payload JSON> | ingest-claude-statusline.sh
set -uo pipefail

runtime_dir="$HOME/opt/agent-usage-tracker"
payload="$(cat)"
now="$(date +%s)"

# One jq pass for the fields this script needs, NUL-separated so empty
# fields survive. These feed the state file: percents rounded, resets as
# text. The history rows below read the payload again, percents unrounded.
fields=()
while IFS= read -r -d '' value; do
    fields+=("$value")
done < <(printf '%s' "$payload" | jq -j '
    def text: if . == null then "" else tostring end;
    [
        ((.rate_limits.five_hour != null or .rate_limits.seven_day != null) | tostring),
        (.transcript_path | text),
        (.session_id | text),
        ((.rate_limits.five_hour.used_percentage // 0) | round | tostring),
        (.rate_limits.five_hour.resets_at | text),
        ((.rate_limits.seven_day.used_percentage // 0) | round | tostring),
        (.rate_limits.seven_day.resets_at | text)
    ] | .[] | ., "\u0000"
' 2>/dev/null)
[ "${fields[0]:-false}" = "true" ] || exit 0
transcript_path="${fields[1]}" session_id="${fields[2]}"
five_pct_int="${fields[3]}" five_reset="${fields[4]}"
week_pct_int="${fields[5]}" week_reset="${fields[6]}"

# The one write path for state/quota/claude on the bash side - mirrored by
# src/quota_polling/_quota_common.py's write_state_if_newer for the poller.
# Compares on observed_at (epoch seconds the reading was actually true, NOT
# when it was written), so the freshest reading wins across every writer
# regardless of write order. No locking: a same-instant race could rarely
# clobber a fresher value with a slightly-less-fresh one, atomic mv still
# prevents a torn file, and the next write self-corrects. Fields, separated
# by $'\034': five_pct five_reset week_pct week_reset source observed_at.
write_quota_if_newer() {
    local state_file="$1" source="$2" observed_at="$3" sep=$'\034' existing="" tmp
    mkdir -p "${state_file%/*}"
    [ -f "$state_file" ] && IFS="$sep" read -r _ _ _ _ _ existing < "$state_file"
    if [ -n "$existing" ] && [ "$observed_at" -le "$existing" ] 2>/dev/null; then
        return 0
    fi
    tmp="${state_file}.tmp.$$-${RANDOM:-0}"
    printf '%s\n' "${five_pct_int}${sep}${five_reset}${sep}${week_pct_int}${sep}${week_reset}${sep}${source}${sep}${observed_at}" > "$tmp"
    mv "$tmp" "$state_file"
}

# observed_at is when Claude Code's in-memory rate-limit state actually
# became true: the timestamp of the transcript's last `assistant` entry (an
# API response). Not any entry: attachments, user messages and bookkeeping
# lines (`last-prompt`, `ai-title`, `mode`, ...) come later without changing
# rate_limits, and would make a frozen reading look newer than it is. Not
# "now" either: an idle session keeps re-rendering a days-old reading, and
# dating it "now" would let it beat every genuinely fresh one. Claude Code
# can also refresh rate_limits from a request it doesn't log (a one-token
# quota_check), so this date can be slightly too old - never too new, which
# is the safe direction for a newest-wins comparison.
#
# Only the last 256 KB is read (tail seeks, so a 150 MB transcript costs the
# same as a small one): bookkeeping tails can run past any fixed line count.
# grep narrows to assistant lines cheaply; jq confirms the top-level type,
# since a message's text can contain the same characters. The cut first
# line is skipped by `fromjson?` instead of aborting the parse. No
# assistant entry in that window -> no observed_at -> nothing written.
#
# The UTC timestamp is converted by plain calendar arithmetic (Howard
# Hinnant's days_from_civil), not jq's fromdateiso8601: jq 1.6 on macOS goes
# through the local timezone and returns an epoch one hour too late whenever
# that zone is in daylight-saving time. Only UTC timestamps ("Z" or "+00:00",
# optional fractional seconds) are accepted; anything else yields no
# observed_at, same as a transcript with no assistant entry.
observed_at=""
if [ -n "$transcript_path" ] && [ -f "$transcript_path" ]; then
    observed_at="$(tail -c 262144 "$transcript_path" 2>/dev/null \
        | grep -F '"type":"assistant"' \
        | jq -R -n -r '
        def epoch:
            capture("^(?<y>[0-9]{4})-(?<mo>[0-9]{2})-(?<d>[0-9]{2})T(?<h>[0-9]{2}):(?<mi>[0-9]{2}):(?<s>[0-9]{2})(\\.[0-9]+)?(Z|\\+00:00)$")
            | map_values(tonumber)
            | (if .mo <= 2 then .y - 1 else .y end) as $y
            | (($y / 400) | floor) as $era
            | ($y - $era * 400) as $yoe
            | (((153 * (if .mo > 2 then .mo - 3 else .mo + 9 end) + 2) / 5 | floor) + .d - 1) as $doy
            | ($yoe * 365 + (($yoe / 4) | floor) - (($yoe / 100) | floor) + $doy) as $doe
            | ($era * 146097 + $doe - 719468) * 86400 + .h * 3600 + .mi * 60 + .s;
        [inputs | fromjson? | select(type == "object" and .type == "assistant" and (.timestamp | type) == "string") | .timestamp]
        | if length == 0 then empty else last end
        | epoch
    ' 2>/dev/null)"
fi
[ -n "$observed_at" ] || exit 0

data_dir="$runtime_dir/data/claude"
mkdir -p "$data_dir"
valid_session=false
[[ "$session_id" =~ ^[A-Za-z0-9-]{1,128}$ ]] && [ "$session_id" != account ] && valid_session=true
# Line 1: account row. Line 2: session row (valid session id only). Lines
# 3-4: their dedup keys - only the fields that carry usage, never append
# time, so a re-render of the same reading yields the same keys.
rows="$(printf '%s' "$payload" | jq -c \
    --argjson ts "$now" \
    --arg iso "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --argjson observed_at "$observed_at" \
    --argjson valid_session "$valid_session" '
    def text: if . == null or . == "" then null else tostring end;
    {ts: $ts, iso: $iso, source: "claude_statusline", observed_at: $observed_at} as $common
    # Account row - percent lives here and only here.
    | {five_hour_pct: (.rate_limits.five_hour.used_percentage // 0),
       seven_day_pct: (.rate_limits.seven_day.used_percentage // 0),
       five_hour_resets_at: (.rate_limits.five_hour.resets_at | text),
       seven_day_resets_at: (.rate_limits.seven_day.resets_at | text)} as $quota
    # Session row - never a percent.
    | {model_id: (.model.id | text),
       session_cost_usd: (.cost.total_cost_usd | if . == null then null else (tostring | tonumber? // null) end),
       prompt_cache: (.prompt_cache // null)} as $usage
    | ($common + $quota + {observed_by_session: (.session_id | text)}),
      (if $valid_session then $common + $usage else "" end),
      ({observed_at: $observed_at} + $quota),
      $usage
' 2>/dev/null)"
{
    IFS= read -r account_row
    IFS= read -r session_row
    IFS= read -r account_key
    IFS= read -r session_key
} <<< "$rows"
[ "${session_row:-}" = '""' ] && session_row=""

# Dedup: state/ingest/<session-id> holds the two keys this session last
# wrote (line 1 account, line 2 session). A row is appended only when its key
# changed: a new API response always brings a new observed_at, so every
# genuine observation is kept, even when the percents didn't move; a
# re-render of the same reading adds nothing. Per session, not per account:
# two sessions seeing the same reading are two observations. Keys live in a
# tiny file rather than being read back from account.jsonl (large, shared by
# every session). A same-session race can at worst append one duplicate.
# Without a valid session id there is no key file, and rows are appended
# unconditionally as before.
last_account_key="" last_session_key=""
key_file=""
if [ "$valid_session" = true ]; then
    key_file="$runtime_dir/state/ingest/$session_id"
    [ -f "$key_file" ] && { IFS= read -r last_account_key; IFS= read -r last_session_key; } < "$key_file"
fi
keys_changed=false
# A single write() call under 4KB with the file opened O_APPEND is
# POSIX-atomic across processes - no locking needed even with many
# concurrent sessions' statuslines appending to the same account file.
if [ -n "${account_row:-}" ] && [ "${account_key:-}" != "$last_account_key" ]; then
    printf '%s\n' "$account_row" >> "$data_dir/account.jsonl"
    keys_changed=true
fi
if [ -n "${session_row:-}" ] && [ "${session_key:-}" != "$last_session_key" ]; then
    printf '%s\n' "$session_row" >> "$data_dir/$session_id.jsonl"
    keys_changed=true
fi
if [ "$keys_changed" = true ] && [ -n "$key_file" ]; then
    mkdir -p "${key_file%/*}"
    tmp="${key_file}.tmp.$$-${RANDOM:-0}"
    printf '%s\n%s\n' "${account_key:-}" "${session_key:-}" > "$tmp" && mv "$tmp" "$key_file"
    # Sessions end without notice: drop key files untouched for 30 days.
    find "${key_file%/*}" -type f -mtime +30 -delete 2>/dev/null
fi

write_quota_if_newer "$runtime_dir/state/quota/claude" claude_statusline "$observed_at"
exit 0
