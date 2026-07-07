#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hmac
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fleet_node_observability.paths import DEFAULT_NODE_EXPORTER_SCRAPE_TOKEN_FILE


PROXY_HOST = os.environ.get("FLEET_NODE_EXPORTER_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("FLEET_NODE_EXPORTER_PROXY_PORT", "19100"))
UPSTREAM_HOST = os.environ.get("FLEET_NODE_EXPORTER_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("FLEET_NODE_EXPORTER_UPSTREAM_PORT", "9100"))
TOKEN_FILE = Path(
    os.environ.get(
        "FLEET_NODE_EXPORTER_SCRAPE_TOKEN_FILE",
        str(DEFAULT_NODE_EXPORTER_SCRAPE_TOKEN_FILE),
    )
)
HEADER_NAME = "X-Fleet-Scrape-Token"


def read_token() -> str:
    try:
        content = TOKEN_FILE.read_text().strip()
    except OSError as exc:
        raise RuntimeError(f"unable to read scrape token file {TOKEN_FILE}: {exc}") from exc
    if "=" in content:
        _, content = content.split("=", 1)
    token = content.strip().strip('"').strip("'")
    if not token:
        raise RuntimeError(f"scrape token file {TOKEN_FILE} is empty")
    return token


class MetricsProxy(BaseHTTPRequestHandler):
    server_version = "FleetNodeExporterProxy/1.0"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/metrics":
            self.send_error(404)
            return
        try:
            token = read_token()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            self.send_error(503)
            return
        token_headers = self.headers.get_all(HEADER_NAME, [])
        if len(token_headers) != 1 or not hmac.compare_digest(token_headers[0], token):
            self.send_error(403)
            return
        upstream = f"http://{UPSTREAM_HOST}:{UPSTREAM_PORT}/metrics"
        try:
            with urllib.request.urlopen(upstream, timeout=10) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "text/plain; version=0.0.4")
        except urllib.error.URLError as exc:
            print(f"upstream node_exporter failed: {exc}", file=sys.stderr)
            self.send_error(502)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the token-gated node_exporter metrics proxy.")
    parser.parse_args(argv)
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), MetricsProxy)
    print(f"fleet node_exporter proxy listening on {PROXY_HOST}:{PROXY_PORT}", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
