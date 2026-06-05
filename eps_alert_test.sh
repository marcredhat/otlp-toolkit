#!/usr/bin/env bash
# eps_alert_test.sh - sustained OTLP/gRPC traffic generator for alert testing.
#
# Two phases:
#   Phase A: HIGH_EPS for HIGH_MIN minutes
#   Phase B: LOW_EPS  for LOW_MIN  minutes (HIGH/2 by default)
#
# The drop from Phase A -> Phase B is what should trip an Observo "EPS dropped
# below threshold" or "sudden volume change" alert if one is configured.
#
# How it works:
#   We send one OTLP/gRPC log BATCH per second; each batch contains <EPS>
#   records (so effective EPS == records/sec). Per-batch wall-clock is
#   measured and we sleep the remainder to stay aligned to 1 Hz. If a single
#   batch takes >1s (slow link, huge EPS) we warn and keep going without
#   sleep so the average stays as close as possible to target.
#
# Usage:
#   ./eps_alert_test.sh [--endpoint host:port] [--high N] [--low N]
#                       [--high-min M] [--low-min M]
#                       [--header 'k: v'] [--insecure] [--service NAME]
#
# Defaults:
#   --endpoint  otlp.example.com:4317
#   --high      50
#   --low       HIGH/2
#   --high-min  10
#   --low-min   5
#
# Examples:
#   ./eps_alert_test.sh                                # 50 EPS x10m, 25 EPS x5m
#   ./eps_alert_test.sh --high 200 --low 100           # 200/100
#   ./eps_alert_test.sh --high 50 --high-min 1 --low-min 1   # quick smoke
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SENDER="$HERE/send_otlp_grpcurl.sh"
[ -x "$SENDER" ] || { echo "[!] $SENDER not executable" >&2; exit 3; }

ENDPOINT="otlp.example.com:4317"
HIGH_EPS=50
LOW_EPS=""
HIGH_MIN=10
LOW_MIN=5
SERVICE="eps-alert-test-$(date +%Y%m%d-%H%M)"
EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --high)     HIGH_EPS="$2"; shift 2 ;;
    --low)      LOW_EPS="$2"; shift 2 ;;
    --high-min) HIGH_MIN="$2"; shift 2 ;;
    --low-min)  LOW_MIN="$2"; shift 2 ;;
    --service)  SERVICE="$2"; shift 2 ;;
    --header)   EXTRA+=(--header "$2"); shift 2 ;;
    --insecure) EXTRA+=(--insecure); shift ;;
    -h|--help)  sed -n '2,32p' "$0" >&2; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$LOW_EPS" ] || LOW_EPS=$(( HIGH_EPS / 2 ))

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
LOG="${EPS_LOG:-/tmp/eps_alert_test-${RUN_ID}.log}"

banner() { echo; echo "===== $* ====="; }

phase() {
  local label="$1" eps="$2" minutes="$3"
  local total=$(( minutes * 60 ))
  local sent_records=0 sent_batches=0 slow_batches=0
  local t_start t_now t_end batch_start batch_dur
  t_start=$(date +%s)
  t_end=$(( t_start + total ))

  banner "$label  eps=${eps}/s  duration=${minutes} min  end=$(date -d @"$t_end" +%H:%M:%S 2>/dev/null || date -r "$t_end" +%H:%M:%S)"

  while :; do
    t_now=$(date +%s)
    [ "$t_now" -ge "$t_end" ] && break

    batch_start=$(now_ns)
    if "$SENDER" --endpoint "$ENDPOINT" --service "$SERVICE" \
                 --count "$eps" --run-id "${RUN_ID}-${label}-${sent_batches}" \
                 ${EXTRA[@]+"${EXTRA[@]}"} >>"$LOG" 2>&1; then
      sent_records=$(( sent_records + eps ))
      sent_batches=$(( sent_batches + 1 ))
    else
      echo "[$(date +%H:%M:%S)] $label batch FAILED (see $LOG)"
    fi
    batch_dur=$(( ( $(now_ns) - batch_start ) / 1000000 ))   # ms

    # progress line every 10s
    if [ $(( sent_batches % 10 )) -eq 0 ]; then
      printf "[%s] %s sent_batches=%d sent_records=%d last_batch=%dms remaining=%ds\n" \
        "$(date +%H:%M:%S)" "$label" "$sent_batches" "$sent_records" "$batch_dur" \
        "$(( t_end - $(date +%s) ))"
    fi

    # sleep the remainder of the 1-second tick (if any)
    if [ "$batch_dur" -lt 1000 ]; then
      sleep "$(awk -v ms="$batch_dur" 'BEGIN{printf "%.3f", (1000-ms)/1000}')"
    else
      slow_batches=$(( slow_batches + 1 ))
    fi
  done

  echo "[$(date +%H:%M:%S)] $label DONE  batches=$sent_batches records=$sent_records slow_batches=$slow_batches"
}

echo "OTLP/gRPC EPS alert test"
echo "  endpoint   : $ENDPOINT"
echo "  service    : $SERVICE"
echo "  phase A    : ${HIGH_EPS} eps for ${HIGH_MIN} min"
echo "  phase B    : ${LOW_EPS} eps for ${LOW_MIN} min   (drop = $(( 100 - LOW_EPS*100/HIGH_EPS ))%)"
echo "  per-batch  : send_otlp_grpcurl.sh -> 1 batch/sec, <eps> records/batch"
echo "  per-batch log : $LOG"
echo

# quick smoke: 1-record batch to confirm the endpoint is reachable BEFORE we
# commit to 15 minutes of traffic.
echo "==> smoke test (1 record)..."
if ! "$SENDER" --endpoint "$ENDPOINT" --service "$SERVICE" --count 1 \
               --run-id "${RUN_ID}-smoke" "${EXTRA[@]}"; then
  echo "[!] smoke test FAILED - aborting before phase A" >&2
  exit 4
fi
echo "==> smoke OK"

phase "PHASE_A_HIGH" "$HIGH_EPS" "$HIGH_MIN"
phase "PHASE_B_LOW"  "$LOW_EPS"  "$LOW_MIN"

banner "DONE  run_id=${RUN_ID}"
echo "Filter in Observo by service.name=${SERVICE} or run_id=${RUN_ID} to see this test in isolation."
