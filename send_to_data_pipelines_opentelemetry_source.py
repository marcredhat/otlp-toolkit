git clone --depth 1 https://github.com/open-telemetry/opentelemetry-proto /tmp/otlp


for svc in logs metrics trace; do
  echo "=== $svc ==="
  grpcurl \
    -import-path /tmp/otlp \
    -proto opentelemetry/proto/collector/$svc/v1/${svc}_service.proto \
    <ingest URL>:443 \
    list opentelemetry.proto.collector.$svc.v1.${svc^}Service 2>&1 | sed 's/^/  /'
done

=== logs ===
  opentelemetry.proto.collector.logs.v1.LogsService.Export
=== metrics ===
  opentelemetry.proto.collector.metrics.v1.MetricsService.Export
=== trace ===
  opentelemetry.proto.collector.trace.v1.TraceService.Export


grpcurl \
  -import-path /tmp/otlp \
  -proto opentelemetry/proto/collector/logs/v1/logs_service.proto \
  -d '{
    "resourceLogs": [{
      "resource": {"attributes":[{"key":"service.name","value":{"stringValue":"grpcurl-test"}}]},
      "scopeLogs": [{
        "logRecords": [{
          "timeUnixNano": "'$(date +%s)000000000'",
          "severityNumber": 9,
          "severityText": "INFO",
          "body": {"stringValue": "hello from grpcurl"}
        }]
      }]
    }]
  }' \
  <ingest URL>:<port for Data Pipelines OpenTelemetry source> \
  opentelemetry.proto.collector.logs.v1.LogsService/Export
