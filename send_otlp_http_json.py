#!/usr/bin/env python3
"""
Send OTLP/HTTP **JSON** payloads (logs, traces, metrics) to an OTLP receiver.

Useful for manual debugging: easy to read in pcaps, easy to tweak fields.
Most production collectors also accept JSON at /v1/{logs,traces,metrics}.

Usage:
    python send_otlp_http_json.py --endpoint http://host:10100 --signal logs --count 3
    python send_otlp_http_json.py --endpoint https://gw:10005 --signal traces --insecure
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from typing import Any, Dict, List

import requests
import urllib3


def _now_ns() -> int:
    return time.time_ns()


def _hex_id(n_bytes: int) -> str:
    return uuid.uuid4().hex[: n_bytes * 2]


def build_logs(count: int, service: str) -> Dict[str, Any]:
    severities = [("INFO", 9), ("WARN", 13), ("ERROR", 17), ("DEBUG", 5)]
    records = []
    for i in range(count):
        sev_text, sev_num = random.choice(severities)
        records.append({
            "timeUnixNano": str(_now_ns()),
            "severityNumber": sev_num,
            "severityText": sev_text,
            "body": {"stringValue": f"otlp http json test #{i} from {service}"},
            "attributes": [
                {"key": "test.run_id", "value": {"stringValue": uuid.uuid4().hex[:8]}},
                {"key": "test.index",  "value": {"intValue": str(i)}},
            ],
            "traceId": _hex_id(16),
            "spanId":  _hex_id(8),
        })
    return {"resourceLogs": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": service}},
        ]},
        "scopeLogs": [{
            "scope": {"name": "send_otlp_http_json.py", "version": "1.0.0"},
            "logRecords": records,
        }],
    }]}


def build_traces(count: int, service: str) -> Dict[str, Any]:
    spans = []
    trace_id = _hex_id(16)
    for i in range(count):
        start = _now_ns()
        end = start + random.randint(1_000_000, 50_000_000)
        spans.append({
            "traceId":           trace_id,
            "spanId":            _hex_id(8),
            "name":              f"test-span-{i}",
            "kind":              2,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano":   str(end),
            "attributes": [
                {"key": "http.method", "value": {"stringValue": "GET"}},
                {"key": "http.status_code", "value": {"intValue": "200"}},
                {"key": "test.index", "value": {"intValue": str(i)}},
            ],
            "status": {"code": 1},
        })
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": service}},
        ]},
        "scopeSpans": [{
            "scope": {"name": "send_otlp_http_json.py", "version": "1.0.0"},
            "spans": spans,
        }],
    }]}


def build_metrics(count: int, service: str) -> Dict[str, Any]:
    now = str(_now_ns())
    data_points = [{
        "asDouble": random.random() * 100,
        "timeUnixNano": now,
        "startTimeUnixNano": now,
        "attributes": [{"key": "test.index", "value": {"intValue": str(i)}}],
    } for i in range(count)]
    return {"resourceMetrics": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": service}},
        ]},
        "scopeMetrics": [{
            "scope": {"name": "send_otlp_http_json.py", "version": "1.0.0"},
            "metrics": [{
                "name": "test.value", "description": "OTLP test gauge", "unit": "1",
                "gauge": {"dataPoints": data_points},
            }],
        }],
    }]}


SIGNAL_PATHS = {"logs": "/v1/logs", "traces": "/v1/traces", "metrics": "/v1/metrics"}
BUILDERS = {"logs": build_logs, "traces": build_traces, "metrics": build_metrics}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--endpoint", required=True, help="Base URL, e.g. http://host:10100")
    p.add_argument("--signal", choices=list(SIGNAL_PATHS), default="logs")
    p.add_argument("--count",  type=int, default=3)
    p.add_argument("--service", default="otlp-http-json-test")
    p.add_argument("--token",  default=os.environ.get("OTLP_TOKEN"),
                   help="Bearer token; or set OTLP_TOKEN env var")
    p.add_argument("--header", action="append", help="Extra header 'Name: value' (repeatable)")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    url = args.endpoint.rstrip("/") + SIGNAL_PATHS[args.signal]
    payload = BUILDERS[args.signal](args.count, args.service)
    headers = {"Content-Type": "application/json", "User-Agent": "send_otlp_http_json.py/1.0"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    for h in args.header or []:
        if ":" not in h:
            print(f"bad --header '{h}'", file=sys.stderr); return 2
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if args.dry_run:
        print("URL:    ", url)
        print("HEADERS:", json.dumps(headers, indent=2))
        print("BODY:   ", json.dumps(payload, indent=2))
        return 0

    body = json.dumps(payload).encode("utf-8")
    print(f"POST {url}  ({len(body)} bytes, {args.count} {args.signal})")
    try:
        r = requests.post(url, data=body, headers=headers, timeout=args.timeout,
                          verify=not args.insecure)
    except requests.RequestException as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"HTTP {r.status_code}")
    ctype = r.headers.get("Content-Type", "")
    if "json" in ctype:
        try: print(json.dumps(r.json(), indent=2))
        except ValueError: print(r.text[:2000])
    else:
        print(r.text[:2000])
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
