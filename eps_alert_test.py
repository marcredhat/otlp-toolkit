#!/usr/bin/env python3
"""
eps_alert_test.py - sustained-EPS OTLP/gRPC traffic generator for alert testing.

Phase A: HIGH_EPS for HIGH_MIN minutes
Phase B: LOW_EPS  for LOW_MIN  minutes (default HIGH/2)

The drop A -> B is what should trip an Observo (or any backend) "sudden
volume drop" / "rate anomaly" alert if one is configured.

Why this over the bash version (eps_alert_test.sh):
  - one long-lived gRPC channel (BatchLogRecordProcessor) instead of
    spawning grpcurl per second -> lower per-batch overhead, sustains
    high EPS reliably.
  - direct visibility into per-batch send failures (logs come back via
    the OTel SDK retry/error path, not via subprocess exit codes).

Usage:
  ./eps_alert_test.py --endpoint host:port [--token TOKEN] [--high N] [--low N]
                      [--high-min M] [--low-min M] [--service NAME] [--insecure]

Examples:
  # Against a managed OTLP/gRPC backend that requires bearer auth:
  ./eps_alert_test.py --endpoint otlp.example.com:4317 \\
                      --token "$OTLP_TOKEN" --high 50 --high-min 10 --low-min 5

  # Against a local OTel Collector (plaintext):
  ./eps_alert_test.py --endpoint localhost:4317 --insecure \\
                      --high 20 --high-min 1 --low-min 1
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import uuid
from datetime import datetime

from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


def build_logger(endpoint: str, headers: tuple, service: str, run_id: str, insecure: bool) -> logging.Logger:
    resource = Resource.create({"service.name": service, "run_id": run_id})
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=endpoint, headers=headers, insecure=insecure),
            max_queue_size=200_000,
            schedule_delay_millis=500,
            max_export_batch_size=2048,
        )
    )
    set_logger_provider(provider)
    log = logging.getLogger(f"eps-{run_id}")
    log.setLevel(logging.INFO)
    log.addHandler(LoggingHandler(level=logging.INFO, logger_provider=provider))
    return log, provider


def run_phase(label: str, eps: int, minutes: int, log: logging.Logger, run_id: str) -> None:
    total = minutes * 60
    t_start = time.monotonic()
    t_end = t_start + total
    batch = 0
    sent = 0
    print(f"\n===== {label}  eps={eps}/s  duration={minutes} min  end={datetime.now().strftime('%H:%M:%S')}+{minutes}m =====")
    sys.stdout.flush()

    while time.monotonic() < t_end:
        tick_start = time.monotonic()
        # Emit `eps` records as fast as possible inside this 1-second tick.
        for i in range(eps):
            log.info(f"eps-test run={run_id} phase={label} batch={batch} seq={i}")
        batch += 1
        sent += eps

        if batch % 10 == 0:
            elapsed = time.monotonic() - tick_start
            remaining = max(0, int(t_end - time.monotonic()))
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] {label} batch={batch} sent={sent} tick={elapsed*1000:.0f}ms remaining={remaining}s")
            sys.stdout.flush()

        # sleep remainder of the 1-second tick (negative -> we're behind, skip sleep)
        sleep_for = 1.0 - (time.monotonic() - tick_start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {label} DONE  batches={batch} records={sent}")
    sys.stdout.flush()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--endpoint", required=True, help="host:port, e.g. otlp.example.com:4317")
    p.add_argument("--token", default=os.environ.get("OTLP_TOKEN"))
    p.add_argument("--header", action="append", default=[], help="extra gRPC metadata 'k: v'")
    p.add_argument("--high", type=int, default=50)
    p.add_argument("--low", type=int, default=None, help="default: HIGH/2")
    p.add_argument("--high-min", type=int, default=10)
    p.add_argument("--low-min", type=int, default=5)
    p.add_argument("--service", default=None)
    p.add_argument("--insecure", action="store_true", help="plaintext gRPC (no TLS)")
    args = p.parse_args()

    low = args.low if args.low is not None else args.high // 2
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    service = args.service or f"eps-alert-test-{run_id}"

    headers = []
    if args.token:
        headers.append(("authorization", f"Bearer {args.token}"))
    for h in args.header:
        if ":" not in h:
            print(f"bad header '{h}'", file=sys.stderr); sys.exit(2)
        k, v = h.split(":", 1)
        headers.append((k.strip().lower(), v.strip()))

    print("OTLP/gRPC EPS alert test")
    print(f"  endpoint   : {args.endpoint}")
    print(f"  service    : {service}")
    print(f"  run_id     : {run_id}")
    print(f"  phase A    : {args.high} eps for {args.high_min} min")
    print(f"  phase B    : {low} eps for {args.low_min} min   (drop = {100 - low*100//args.high}%)")
    print(f"  auth       : {'Bearer token' if args.token else 'NONE'}")
    print(f"  tls        : {'no (plaintext)' if args.insecure else 'yes'}")

    log, provider = build_logger(args.endpoint, tuple(headers), service, run_id, args.insecure)

    # smoke - 1 record before committing to the full run
    print("\n==> smoke (1 record)...")
    log.info(f"eps-test run={run_id} smoke")
    provider.force_flush(5_000)
    print("==> smoke flushed (no UNAVAILABLE = receiver accepted; check destination)")

    try:
        run_phase("PHASE_A_HIGH", args.high, args.high_min, log, run_id)
        run_phase("PHASE_B_LOW",  low,       args.low_min,  log, run_id)
    finally:
        print("\n==> flushing remaining batches...")
        provider.force_flush(10_000)
        provider.shutdown()

    print(f"\nDONE  run_id={run_id}")
    print(f"Filter in destination by:  service.name = '{service}'   or   run_id = '{run_id}'")


if __name__ == "__main__":
    main()
