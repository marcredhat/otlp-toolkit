#!/usr/bin/env bash
# send_otlp_grpcurl.sh - single-batch OTLP/gRPC LOGS sender using grpcurl.
#
# Why this exists alongside send_otlp_grpc.py:
#   - No Python / pip / venv required, only `grpcurl` + `git` on PATH.
#   - Useful from minimal containers, CI runners, and quick shell-loop drivers
#     (e.g. eps_alert_test.sh that wraps this script).
#
# What it does:
#   1. Clones opentelemetry-proto into ~/.cache/otlp-toolkit/proto (first run only).
#      grpcurl needs the LogsService.proto descriptors because most managed OTLP
#      endpoints do NOT expose gRPC reflection.
#   2. Synthesizes a single ExportLogsServiceRequest containing N log records.
#   3. POSTs it via grpcurl to opentelemetry.proto.collector.logs.v1.LogsService/Export.
#
# Usage:
#   ./send_otlp_grpcurl.sh --endpoint host:port [--count N] [--service NAME]
#                          [--insecure] [--header 'k: v'] [--run-id ID]
#
# Examples:
#   ./send_otlp_grpcurl.sh --endpoint otlp.example.com:4317 --count 5
#   ./send_otlp_grpcurl.sh --endpoint localhost:4317 --insecure --count 1
#
# Env overrides:
#   PROTO_DIR   - where to keep the opentelemetry-proto clone (default ~/.cache/otlp-toolkit/proto)
set -euo pipefail

ENDPOINT=""
COUNT=1
SERVICE="otlp-grpcurl-test"
INSECURE=0
RUN_ID=""
HEADERS=()

usage() { sed -n '2,28p' "$0" >&2; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --count)    COUNT="$2"; shift 2 ;;
    --service)  SERVICE="$2"; shift 2 ;;
    --insecure) INSECURE=1; shift ;;
    --header)   HEADERS+=("-H" "$2"); shift 2 ;;
    --run-id)   RUN_ID="$2"; shift 2 ;;
    -h|--help)  usage 0 ;;
    *) echo "unknown arg: $1" >&2; usage 2 ;;
  esac
done

[ -n "$ENDPOINT" ] || { echo "--endpoint is required" >&2; usage 2; }
command -v grpcurl >/dev/null || { echo "[!] grpcurl not on PATH" >&2; exit 3; }
command -v git     >/dev/null || { echo "[!] git not on PATH" >&2; exit 3; }

PROTO_DIR="${PROTO_DIR:-$HOME/.cache/otlp-toolkit/proto}"
if [ ! -d "$PROTO_DIR/opentelemetry/proto/collector/logs/v1" ]; then
  echo "[*] fetching opentelemetry-proto into $PROTO_DIR (one-time, ~2 MB)" >&2
  mkdir -p "$(dirname "$PROTO_DIR")"
  git clone --depth 1 --quiet https://github.com/open-telemetry/opentelemetry-proto "$PROTO_DIR"
fi

[ -n "$RUN_ID" ] || RUN_ID="$(date +%s)-$$"
# date +%s%N is GNU-only (BSD date returns literal 'N'). Prefer python3.
if command -v python3 >/dev/null 2>&1; then
  NOW_NS="$(python3 -c 'import time;print(int(time.time()*1e9))')"
else
  _ns="$(date +%s%N)"; case "$_ns" in *N) NOW_NS="$(date +%s)000000000" ;; *) NOW_NS="$_ns" ;; esac
fi

# Build the ExportLogsServiceRequest JSON in pure bash (no jq dependency).
build_payload() {
  local i sep="" recs=""
  for i in $(seq 0 $((COUNT - 1))); do
    # 1-ns offset between records so they don't collide on time_unix_nano
    local ts=$((NOW_NS + i))
    recs+="${sep}{\"time_unix_nano\":\"${ts}\",\"severity_number\":9,\"severity_text\":\"INFO\",\"body\":{\"string_value\":\"otlp-grpcurl run=${RUN_ID} seq=${i}\"},\"attributes\":[{\"key\":\"run_id\",\"value\":{\"string_value\":\"${RUN_ID}\"}},{\"key\":\"seq\",\"value\":{\"int_value\":\"${i}\"}}]}"
    sep=","
  done
  cat <<JSON
{"resource_logs":[{"resource":{"attributes":[{"key":"service.name","value":{"string_value":"${SERVICE}"}},{"key":"run_id","value":{"string_value":"${RUN_ID}"}}]},"scope_logs":[{"scope":{"name":"otlp-grpcurl"},"log_records":[${recs}]}]}]}
JSON
}

GRPCURL_FLAGS=(
  -import-path "$PROTO_DIR"
  -proto opentelemetry/proto/collector/logs/v1/logs_service.proto
  -connect-timeout 30
  -max-time 60
)
[ "$INSECURE" -eq 1 ] && GRPCURL_FLAGS+=(-plaintext)

build_payload | grpcurl "${GRPCURL_FLAGS[@]}" ${HEADERS[@]+"${HEADERS[@]}"} -d @ \
  "$ENDPOINT" opentelemetry.proto.collector.logs.v1.LogsService/Export
