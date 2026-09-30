#!/bin/bash
# Tests for src/telemetry/otlp_receiver.py: runs it on a free local port and
# POSTs OTLP/JSON log batches shaped like Claude Code's (captured live
# 2026-09-30), checking what reaches the JSONL file.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/harness.sh"

TH_TMP2="$(mktemp -d "${TMPDIR:-/tmp}/agent-usage-tracker-otel.XXXXXX")"
DATA="$TH_TMP2/data"
OUT="$DATA/claude/8b301a30-3609-4e04-8a39-fb19a3b8be54.jsonl"
UNATTRIBUTED="$DATA/_unattributed/claude-otel.jsonl"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
python3 "$REPO_ROOT/src/telemetry/otlp_receiver.py" --port "$PORT" --data-dir "$DATA" >/dev/null 2>"$TH_TMP2/err" &
PID=$!
trap 'kill "$PID" 2>/dev/null; rm -rf "$TH_TMP2"' EXIT
for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null && break; sleep 0.1; done

post() { # path content-type body -> prints HTTP status
    curl -s -o /dev/null -w '%{http_code}' -X POST -H "Content-Type: $2" --data-binary "$3" "http://127.0.0.1:$PORT$1"
}
record() { # event-name extra-attrs-json-fragment (session.id included unless NO_SESSION=1)
    local sid=',{"key":"session.id","value":{"stringValue":"8b301a30-3609-4e04-8a39-fb19a3b8be54"}}'
    [ -n "${NO_SESSION:-}" ] && sid=""
    printf '{"timeUnixNano":"1790773229123000000","body":{"stringValue":"claude_code.%s"},"attributes":[{"key":"event.name","value":{"stringValue":"%s"}}%s%s]}' "$1" "$1" "$sid" "$2"
}
batch() {
    printf '{"resourceLogs":[{"resource":{"attributes":[{"key":"service.version","value":{"stringValue":"2.1.284"}}]},"scopeLogs":[{"scope":{"name":"com.anthropic.claude_code.events"},"logRecords":[%s]}]}]}' "$1"
}

section "usage events kept, flattened, and written to their session file; other events dropped"
API="$(record api_request ',{"key":"input_tokens","value":{"intValue":"10"}},{"key":"cost_usd","value":{"doubleValue":0.0210743}},{"key":"query_source","value":{"stringValue":"generate_session_title"}},{"key":"flags","value":{"arrayValue":{"values":[{"boolValue":true}]}}}')"
PROMPT="$(record user_prompt ',{"key":"prompt","value":{"stringValue":"secret prompt text"}}')"
assert_eq "accepted" "200" "$(post /v1/logs application/json "$(batch "$API,$PROMPT")")"
assert_eq "one row: only the usage event" "1" "$(wc -l < "$OUT" | tr -d ' ')"
row="$(cat "$OUT")"
assert_eq "event name without prefix" "api_request" "$(printf '%s' "$row" | jq -r .event)"
assert_eq "tagged claude_otel" "claude_otel" "$(printf '%s' "$row" | jq -r .source)"
assert_eq "observed_at is the event time in epoch seconds (the join key)" "1790773229" "$(printf '%s' "$row" | jq -r .observed_at)"
assert_file_missing "no account file is ever written by telemetry" "$DATA/claude/account.jsonl"
assert_eq "intValue string -> int" "10" "$(printf '%s' "$row" | jq -c .attributes.input_tokens)"
assert_eq "doubleValue kept exactly" "0.0210743" "$(printf '%s' "$row" | jq -c .attributes.cost_usd)"
assert_eq "query_source kept" "generate_session_title" "$(printf '%s' "$row" | jq -r .attributes.query_source)"
assert_eq "arrayValue flattened" "[true]" "$(printf '%s' "$row" | jq -c .attributes.flags)"
assert_eq "resource attributes kept" "2.1.284" "$(printf '%s' "$row" | jq -r '.resource["service.version"]')"
assert_eq "record timestamp kept as an int" "1790773229123000000" "$(printf '%s' "$row" | jq -r .time_unix_nano)"
assert_not_contains "prompt text never written" "$(cat "$OUT")" "secret prompt text"

section "all four usage event kinds are kept"
post /v1/logs application/json "$(batch "$(record api_error '')","$(record api_refusal '')","$(record api_retries_exhausted '')")" >/dev/null
assert_eq "three more rows" "4" "$(wc -l < "$OUT" | tr -d ' ')"

section "no usable session id -> the unattributed file, never a session or account file"
NO_SESSION=1 post /v1/logs application/json "$(NO_SESSION=1 batch "$(NO_SESSION=1 record api_request '')")" >/dev/null
assert_eq "one unattributed row" "1" "$(wc -l < "$UNATTRIBUTED" | tr -d ' ')"
BAD_SID="$(record api_request ',{"key":"session.id","value":{"stringValue":"../escape"}}' | sed 's#,{"key":"session.id","value":{"stringValue":"8b301a30-3609-4e04-8a39-fb19a3b8be54"}}##')"
post /v1/logs application/json "$(batch "$BAD_SID")" >/dev/null
assert_eq "a path-like session id is unattributed too" "2" "$(wc -l < "$UNATTRIBUTED" | tr -d ' ')"
assert_eq "no file created outside data/" "0" "$(find "$TH_TMP2" -name '*escape*' | wc -l | tr -d ' ')"

section "anything else is answered without writing"
assert_eq "metrics accepted and discarded" "200" "$(post /v1/metrics application/json '{"resourceMetrics":[]}')"
assert_eq "protobuf refused with 415" "415" "$(post /v1/logs application/x-protobuf 'xx')"
assert_eq "malformed JSON refused with 400" "400" "$(post /v1/logs application/json '{not json')"
assert_eq "still four rows" "4" "$(wc -l < "$OUT" | tr -d ' ')"
assert_eq "server still alive after bad input" "200" "$(post /v1/logs application/json "$(batch "")")"

section "binds to localhost only"
assert_contains "listening address" "$(lsof -nP -a -p "$PID" -iTCP -sTCP:LISTEN 2>/dev/null)" "127.0.0.1:$PORT"

harness_summary
