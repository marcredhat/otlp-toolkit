#!/usr/bin/env bash
# diag_otlp_grpc.sh - verbose end-to-end diagnostic for an OTLP/gRPC endpoint.
# Runs four layers: TCP -> TLS -> ALPN -> gRPC Export, prints everything.
#
# Usage:
#   ./diag_otlp_grpc.sh <host> [port]
#
# Examples:
#   ./diag_otlp_grpc.sh otlp.example.com 4317
#   ./diag_otlp_grpc.sh localhost 4317
set -uo pipefail

HOST="${1:?host required, e.g. otlp.example.com}"
PORT="${2:-4317}"
PROTO_DIR="${PROTO_DIR:-$HOME/.cache/otlp-toolkit/proto}"

hr() { printf '\n========== %s ==========\n' "$*"; }

hr "1. DNS"
host -t A "$HOST" 2>&1 || dig +short "$HOST"

hr "2. TCP connect (nc -vz)"
nc -vz -G 10 "$HOST" "$PORT" 2>&1

hr "3. TLS + ALPN (openssl s_client)"
echo | openssl s_client -connect "${HOST}:${PORT}" -alpn h2 -servername "$HOST" </dev/null 2>&1 \
  | grep -E "ALPN|Protocol|Cipher|verify return|subject=|CN ="

hr "4. ensure opentelemetry-proto cached"
if [ ! -d "$PROTO_DIR/opentelemetry/proto/collector/logs/v1" ]; then
  mkdir -p "$(dirname "$PROTO_DIR")"
  git clone --depth 1 --quiet https://github.com/open-telemetry/opentelemetry-proto "$PROTO_DIR"
fi
echo "PROTO_DIR=$PROTO_DIR  (logs_service.proto: $(test -f "$PROTO_DIR/opentelemetry/proto/collector/logs/v1/logs_service.proto" && echo yes || echo MISSING))"

hr "5. grpcurl --version + plain dial test (verbose)"
grpcurl -version 2>&1
echo
grpcurl -v -connect-timeout 30 \
        -import-path "$PROTO_DIR" \
        -proto opentelemetry/proto/collector/logs/v1/logs_service.proto \
        "${HOST}:${PORT}" list 2>&1 | head -30

hr "6. minimal OTLP Export call (verbose)"
NOW_NS=$(python3 -c 'import time;print(int(time.time()*1e9))')
PAYLOAD=$(cat <<JSON
{"resource_logs":[{"resource":{"attributes":[{"key":"service.name","value":{"string_value":"diag-otlp-grpc"}}]},"scope_logs":[{"log_records":[{"time_unix_nano":"${NOW_NS}","severity_text":"INFO","body":{"string_value":"diag probe $(date +%T)"}}]}]}]}
JSON
)
echo "payload: $PAYLOAD"
echo "---"
echo "$PAYLOAD" | grpcurl -v -connect-timeout 30 -max-time 60 \
   -import-path "$PROTO_DIR" \
   -proto opentelemetry/proto/collector/logs/v1/logs_service.proto \
   -d @ "${HOST}:${PORT}" \
   opentelemetry.proto.collector.logs.v1.LogsService/Export 2>&1

hr "DONE"
