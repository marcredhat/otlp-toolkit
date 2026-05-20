#!/usr/bin/env python3
"""
Send OTLP/HTTP (protobuf + gzip) logs/traces to an OTLP receiver.

This matches what the otel-collector `otlphttp` exporter sends.

Examples:
    # Direct to OTLP/HTTP receiver
    python3 send_otlp_http_proto.py \\
        --endpoint http://backend.example:10100 --signal logs --count 3

    # Through an nginx TLS-terminating proxy (server cert from public CA)
    python3 send_otlp_http_proto.py \\
        --endpoint https://gateway.example:10005 --signal logs --count 3

    # With self-signed/internal CA
    python3 send_otlp_http_proto.py \\
        --endpoint https://gateway.example:10005 --signal logs --count 3 \\
        --insecure

    # With server-CA pinning + mTLS
    python3 send_otlp_http_proto.py \\
        --endpoint https://gateway.example:10005 --signal logs --count 3 \\
        --ca-cert     /etc/pki/observo/ca.crt \\
        --client-cert /etc/pki/observo/client.crt \\
        --client-key  /etc/pki/observo/client.key
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid


def _maybe_disable_tls_verify(insecure: bool) -> None:
    if not insecure:
        return
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _orig = requests.Session.request
    def _req(self, method, url, **kw):
        kw["verify"] = False
        return _orig(self, method, url, **kw)
    requests.Session.request = _req


def send_logs(endpoint: str, count: int, service: str, tls: dict) -> int:
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

    resource = Resource.create({"service.name": service})
    provider = LoggerProvider(resource=resource)
    exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", timeout=15, **tls)
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter, schedule_delay_millis=500))
    set_logger_provider(provider)

    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    log = logging.getLogger("otlp-http-proto-test")
    log.setLevel(logging.INFO)
    log.addHandler(handler)

    run_id = uuid.uuid4().hex[:8]
    for i in range(count):
        log.info(f"otlp http proto message #{i} run={run_id}")

    print(f"emitted {count} logs to {endpoint}/v1/logs (run={run_id}, service={service})")
    provider.shutdown()
    return 0


def send_traces(endpoint: str, count: int, service: str, tls: dict) -> int:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create({"service.name": service})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=15, **tls)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("otlp-http-proto-test")

    for i in range(count):
        with tracer.start_as_current_span(f"test-span-{i}") as span:
            span.set_attribute("test.index", i)

    print(f"emitted {count} spans to {endpoint}/v1/traces (service={service})")
    provider.shutdown()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--endpoint", required=True,
                   help="Base URL (no path), e.g. http://host:10100 or https://gw:10005")
    p.add_argument("--signal", choices=["logs", "traces"], default="logs")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--service", default="otlp-http-proto-test")
    p.add_argument("--insecure", action="store_true", help="Skip TLS cert verification")
    p.add_argument("--ca-cert",     help="Path to CA bundle (server cert verification)")
    p.add_argument("--client-cert", help="Path to client certificate (mTLS)")
    p.add_argument("--client-key",  help="Path to client private key (mTLS)")
    args = p.parse_args()

    _maybe_disable_tls_verify(args.insecure)

    tls: dict = {}
    if args.ca_cert:
        tls["certificate_file"] = args.ca_cert
    if args.client_cert:
        tls["client_certificate_file"] = args.client_cert
    if args.client_key:
        tls["client_key_file"] = args.client_key

    if args.signal == "logs":
        return send_logs(args.endpoint, args.count, args.service, tls)
    return send_traces(args.endpoint, args.count, args.service, tls)


if __name__ == "__main__":
    sys.exit(main())
