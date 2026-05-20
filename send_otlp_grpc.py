#!/usr/bin/env python3
"""
Send OTLP/gRPC logs/traces/metrics to a collector.

Usage:
    python3 send_otlp_grpc.py --host backend.example --port 4317 --signal logs --count 3
    OTLP_TOKEN=... python3 send_otlp_grpc.py --host gw --port 4317 --signal traces
    python3 send_otlp_grpc.py --host localhost --port 4317 --insecure --signal logs
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
import uuid


def send_logs(endpoint, headers, count, service, insecure):
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    import logging
    provider = LoggerProvider(resource=Resource.create({"service.name": service}))
    provider.add_log_record_processor(BatchLogRecordProcessor(
        OTLPLogExporter(endpoint=endpoint, headers=headers, insecure=insecure)))
    set_logger_provider(provider)
    log = logging.getLogger("otlp-grpc-test")
    log.setLevel(logging.INFO)
    log.addHandler(LoggingHandler(level=logging.INFO, logger_provider=provider))
    run_id = uuid.uuid4().hex[:8]
    for i in range(count):
        log.info(f"otlp grpc test #{i} run={run_id}")
    print(f"emitted {count} logs to {endpoint} (run={run_id}, service={service})")
    provider.shutdown()


def send_traces(endpoint, headers, count, service, insecure):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=endpoint, headers=headers, insecure=insecure)))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("otlp-grpc-test")
    for i in range(count):
        with tracer.start_as_current_span(f"test-span-{i}") as s:
            s.set_attribute("test.index", i)
            time.sleep(random.uniform(0.001, 0.01))
    print(f"emitted {count} spans to {endpoint} (service={service})")
    provider.shutdown()


def send_metrics(endpoint, headers, count, service, insecure):
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, headers=headers, insecure=insecure),
        export_interval_millis=1000)
    provider = MeterProvider(resource=Resource.create({"service.name": service}),
                             metric_readers=[reader])
    metrics.set_meter_provider(provider)
    counter = metrics.get_meter("otlp-grpc-test").create_counter("test.counter")
    for i in range(count):
        counter.add(1, {"index": i})
    time.sleep(2)
    print(f"emitted {count} counter increments to {endpoint} (service={service})")
    provider.shutdown()


SIGNALS = {"logs": send_logs, "traces": send_traces, "metrics": send_metrics}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=4317)
    p.add_argument("--signal", choices=list(SIGNALS), default="logs")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--service", default="otlp-grpc-test")
    p.add_argument("--token", default=os.environ.get("OTLP_TOKEN"))
    p.add_argument("--header", action="append")
    p.add_argument("--insecure", action="store_true")
    args = p.parse_args()

    headers = []
    if args.token:
        headers.append(("authorization", f"Bearer {args.token}"))
    for h in args.header or []:
        if ":" not in h:
            print(f"bad header '{h}'", file=sys.stderr); sys.exit(2)
        k, v = h.split(":", 1)
        headers.append((k.strip().lower(), v.strip()))

    endpoint = f"{args.host}:{args.port}"
    print(f"endpoint={endpoint} tls={'no' if args.insecure else 'yes'}")
    SIGNALS[args.signal](endpoint, tuple(headers), args.count, args.service, args.insecure)


if __name__ == "__main__":
    main()
