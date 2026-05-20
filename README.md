# OTLP Send & Troubleshoot Toolkit

A small set of self-contained scripts to send OTLP logs/traces/metrics to any
OTLP receiver (Observo data-plane, OpenTelemetry Collector, vendor gateways)
and a runbook for diagnosing why ingestion is failing.

## Contents

| File | Purpose |
|---|---|
| `send_otlp_http_proto.py` | OTLP/HTTP **protobuf+gzip** (what the otel-collector `otlphttp` exporter sends). Supports logs and traces. mTLS-capable. **Use this first.** |
| `send_otlp_http_json.py`  | OTLP/HTTP **JSON** (logs/traces/metrics). Useful for manual debugging and packet inspection. |
| `send_otlp_grpc.py`       | OTLP/gRPC for logs/traces/metrics. Useful when the receiver is gRPC-only. |
| `otelcol-forward.yaml`    | Local OTel Collector config: receives on `4318`/`4317`, forwards to downstream via `otlphttp`, mirrors to debug log. |
| `requirements.txt`        | Pip dependencies. |

## Setup

```bash
python3 -m pip install --user -r requirements.txt
# On PEP-668 distros (newer Debian/Ubuntu/RHEL):
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

## Common usage

```bash
# Plain HTTP OTLP receiver
python3 send_otlp_http_proto.py --endpoint http://backend:10100 --signal logs --count 3

# Through a TLS-terminating gateway with self-signed cert
python3 send_otlp_http_proto.py --endpoint https://gateway:10005 --signal logs --insecure

# mTLS
python3 send_otlp_http_proto.py --endpoint https://gateway:10005 --signal logs \
    --ca-cert /etc/pki/ca.crt --client-cert /etc/pki/client.crt --client-key /etc/pki/client.key

# OTLP/gRPC
python3 send_otlp_grpc.py --host backend --port 4317 --insecure --signal logs
```

Switch signals with `--signal logs|traces|metrics` (metrics only in JSON and gRPC scripts).

---

# Troubleshooting runbook (OTLP/HTTP)

Symptoms observed during ingestion problems and what they mean.

## 1. `Read timed out` / `Operation timed out, 0 bytes received`

**TLS handshake succeeds; the server never sends an HTTP response.** Almost always one of:

- The connection is **TCP-proxied** to a backend that does not have a listener on that port (kube-proxy will RST after a moment; some L4 proxies just hold open).
- The backend exists but expects a different protocol (e.g. you sent HTTP to a TLS port, or HTTP/1.1 to a gRPC-only server).
- Source-IP allowlist / firewall holds the connection open then drops it.

Verify with curl:

```bash
curl -kv -m 5 https://host:PORT/v1/logs -H 'Content-Type: application/json' -d '{}'
```

If you see no response headers at all, the backend isn't speaking HTTP back.

## 2. `Connection reset by peer` immediately after POST

**Backend exists but rejects the request.** Usually:

- Wrong port (nothing listening; kube-proxy / iptables RST).
- TLS expected, you sent plain HTTP.
- Strict SNI/HostHeader checks failing.
- Some L7 proxy that closes on malformed payload.

Run on the host side to see what's bound:

```bash
sudo lsof -nP -iTCP:PORT -sTCP:LISTEN
ss -tlnp | grep :PORT
```

## 3. `SSL_ERROR_SYSCALL` / `SSLEOFError`

**TLS handshake itself fails.** Possible causes:

- Server requires **mTLS** and you didn't present a client cert.
- Client offered cipher/version the server doesn't accept.
- Frontend's SNI matching dropped your ClientHello.

Inspect the server cert and look for mTLS in nginx:

```bash
openssl s_client -connect host:PORT -servername host </dev/null 2>&1 | head -40
sudo grep -rE 'ssl_verify_client|ssl_client_certificate|listen.*PORT' /etc/nginx/
```

## 4. HTTP/0.9 banner / unexpected bytes after POST

The port speaks something **other than HTTP**. Examples we've seen:

- `15 03 03 00 02 02 16` → that's a TLS fatal alert (`record_overflow`). Port expects TLS, not plain HTTP.
- Other binary garble → could be Splunk HEC, syslog, gRPC, custom protocol.

Capture exactly what the server sent:

```bash
curl -s -m 5 --http0.9 -o /tmp/resp.bin http://host:PORT/v1/logs -d '{}'
xxd /tmp/resp.bin
```

## 5. `200 OK` with empty body or `partialSuccess: {}`

Successful ingestion. Check Observo UI / receiver logs to confirm records landed.

## 6. `404 Not Found` on `/v1/logs`

The server speaks HTTP but isn't an OTLP receiver — try plain `/`, `/health`, or check it's the OTLP port and not e.g. an admin/UI port.

## 7. `500` with `Content-Type: application/x-protobuf`

It **is** an OTLP receiver and rejected the payload. Send a real OTLP body (use `send_otlp_http_proto.py`, not curl with an empty `{}`).

---

# Layer-by-layer debug methodology

When ingestion is broken, walk these layers in order. Don't skip ahead.

## L1 — Network reachability

```bash
ping -c 3 HOST
nc -vz HOST PORT       # or: nc -vzu HOST PORT for UDP
```

## L2 — TLS

```bash
openssl s_client -connect HOST:PORT -servername HOST </dev/null 2>&1 | head -20
# Look for: handshake completes, valid cert, expected SAN, ALPN (h2 / http/1.1)
```

If ALPN says `h2` and your client is HTTP/1.1, that's fine — most servers accept both.
If you see `tlsv1 alert handshake failure` → mTLS, cipher, or SNI issue.

## L3 — HTTP

```bash
curl -kv -m 10 https://HOST:PORT/ -H 'Host: HOST'
curl -kv -m 10 https://HOST:PORT/v1/logs -H 'Content-Type: application/json' -d '{}'
```

Note status code, response headers (especially `Content-Type` and `Server`), and any error body.

## L4 — OTLP payload

Once HTTP is responding:

```bash
python3 send_otlp_http_proto.py --endpoint https://HOST:PORT --signal logs --count 1 --insecure
```

A success looks like:

```
emitted 1 logs to https://HOST:PORT/v1/logs (run=..., service=otlp-http-proto-test)
```

with **no** retry/error lines. Add `--service my-test-yyyy-mm-dd` to make the events easy to grep in the destination.

---

# Bypass tricks when a gateway is broken

If the public/edge gateway is misrouted but the backend is reachable some other way:

## Through an SSH tunnel

```bash
ssh -fN user@bastion -L 10005:backend.internal:10100
python3 send_otlp_http_proto.py --endpoint http://localhost:10005 --signal logs --count 3
```

## Direct to a Kubernetes ClusterIP from a node

```bash
kubectl -n NAMESPACE get svc
# e.g. data-plane-gateway-service  ClusterIP  10.43.x.y  8686/TCP,10100/TCP
python3 send_otlp_http_proto.py --endpoint http://10.43.x.y:10100 --signal logs --count 3
```

## Via the bundled local OTel Collector

```bash
docker run -d --rm --name otelcol-fwd \
    -p 4318:4318 -p 4317:4317 \
    -v $PWD/otelcol-forward.yaml:/etc/otelcol/config.yaml \
    otel/opentelemetry-collector:latest \
    --config=/etc/otelcol/config.yaml

# Then point anything OTLP-capable at http://localhost:4318
python3 send_otlp_http_proto.py --endpoint http://localhost:4318 --signal logs --count 3

# Inspect what the collector sees and what the downstream returns
docker logs -f otelcol-fwd | grep -iE 'logs|error|http'
```

---

# Notes specific to Observo data-plane deployments

- The edge port (e.g. `:10005`) is typically an **nginx stream proxy** to an internal Kubernetes
  `data-plane-gateway-service`.
- That Service usually exposes **`8686`** and **`10100`** (not the edge port itself).

  `kubectl -n observo-client get svc data-plane-gateway-service -o yaml | grep targetPort`


  ```
  targetPort: 8686
  targetPort: 10100
  ```


  The stream proxy is often configured `proxy_pass <svc>:<same_port>`, so for the proxy to work it needs a
  `map $server_port $destination_port { 10005 10100; ... }` entry — otherwise connections to
  unknown ports on the ClusterIP get RST by kube-proxy.


Example: for a Data Pipeline source listening on 10005, I added `10005 10100;`

```
cat /etc/nginx/conf.d/stream/tcp-proxy.conf
stream {
    map $server_port $destination_port {
        default 10001;
	    10005 10100;
        ~^(\d+)$ $1;
    }

    ## Stream supports TCP and UDP
    server {
        listen 10001-10009;
        proxy_pass 10.43.xx.xx:$destination_port;
        proxy_connect_timeout 1s;
    }

    server {
        listen 10001-10009 udp reuseport;
        proxy_pass 10.43.xx.xx:$destination_port;
        proxy_connect_timeout 1s;
    }
```

```
sudo nginx -t && sudo systemctl reload nginx
```
 
- The actual OTLP/HTTP receiver inside the data-plane Pod listens on **`:10100`**. Hitting it
  directly is the fastest way to verify the rest of the pipeline.
- nginx in this layout is **server-TLS only** (server.crt/server.key), no mTLS.
- Quick triage commands inside the host:
  ```bash
  sudo lsof -nP -iTCP:10005 -sTCP:LISTEN
  sudo cat /etc/nginx/conf.d/stream/tcp-proxy.conf
  kubectl -n observo-client get svc data-plane-gateway-service -o yaml
  kubectl -n observo-client logs -l app.kubernetes.io/name=data-plane --tail=100
  ```
