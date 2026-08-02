# Install off-LAN Node Observability

off-LAN installs are intentionally stricter because the node is not on the same
LAN as the collector endpoint.

Status: current `0.1.x` compatibility runbook. The selected product direction removes the per-node
tunnel, scrape proxy, scrape token, Cloudflare Access credentials, and separate off-LAN installer
after the unified outbound OTLP path is validated.

## What it configures

- Binds a local `node_exporter` listener (`127.0.0.1`) for token-protected scraping.
- Sets up a proxy process that enforces `X-Fleet-Scrape-Token`.
- Installs same collectors as the LAN flow (Codex usage, thermal, readiness).

## Security defaults

- Node-exporter listener is local-only by default.
- Proxy requires token validation before forwarding `/metrics`.
- OTLP configuration includes:
  - Basic auth header from `node_label:ingest_token`
  - CF-Access headers when `off_lan` mode is enabled.

Configure OTLP with `./bin/configure-openclaw-otel` after host metrics are
installed. The host-metrics installer intentionally does not edit OpenClaw OTLP
settings.

## Quick Start

1. Generate and review a sanitized off-LAN config:

```bash
cp examples/node-config.off-lan.example.json /tmp/node-config.off-lan.json
```

2. Run installer with explicit off-LAN details:

```bash
sudo ./bin/install-off-lan-host-metrics \
  --config /tmp/node-config.off-lan.json \
  --node-label mini-03 \
  --node-user fleet-mini-03 \
  --node-exporter-port 9100 \
  --node-exporter-tunnel-hostname "node-exporter-mini-03.example.net" \
  --openclaw-ready-url "http://127.0.0.1:18789/readyz"
```

3. Confirm scrape path only accepts tokened requests:

```bash
curl -fsS "http://127.0.0.1:19100/metrics"
curl -fsS -H "X-Fleet-Scrape-Token: $(cat /Library/OpenClaw/fleet-node-exporter-scrape-token)" \
  "http://127.0.0.1:19100/metrics" | head
```

## Validation

- `curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:19100/metrics` should
  print `403` without a token. The proxy implements `GET`; `curl -I` sends `HEAD` and is not a valid
  authorization probe for this compatibility service.
- Proxy log should be in the install path configured by the installer (often `~/Library/Logs` or `/Library/Logs`).
- Central scrape target should include only `/metrics` and a token-bearing header.

## Notes

- Tunnel names should be stable across redeploys to avoid duplicate LaunchDaemon
  load entries.
- Keep real hostnames and tokens out of source control and issue comments.
