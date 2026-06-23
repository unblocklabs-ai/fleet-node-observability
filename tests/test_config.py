from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fleet_node_observability.config import ConfigError, resolve_otlp_config


class ResolveOtlpConfigTest(unittest.TestCase):
    def test_config_json_supplies_defaults(self) -> None:
        payload = Path("/tmp/missing.json")
        with self.assertRaises(ConfigError):
            # path must exist for explicit --config
            resolve_otlp_config(config_path=payload)

    def test_merge_explicit_flags_with_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "node-config.json"
            config_path.write_text(
                (
                    '{"node_label":"mini-03","network":"off_lan","openclaw_service_name":"openclaw_gateway",'
                    '"otlp_http_endpoint":"https://from-config.example.com"}'
                ),
                encoding="utf-8",
            )
            cfg = resolve_otlp_config(
                config_path=config_path,
                endpoint="https://cli.example.com",
                service_name="cli-gateway",
                network="lan",
                cf_access_client_id="cli-id",
                cf_access_client_secret="cli-secret",
            )

        self.assertEqual(cfg.endpoint, "https://cli.example.com")
        self.assertEqual(cfg.network, "lan")
        self.assertEqual(cfg.service_name, "cli-gateway")
        self.assertEqual(cfg.cf_access_client_id, "cli-id")
        self.assertEqual(cfg.cf_access_client_secret, "cli-secret")

    def test_off_lan_requires_access_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "node-config.json"
            config_path.write_text(
                (
                    '{"node_label":"mini-03","network":"off_lan","openclaw_service_name":"openclaw_gateway",'
                    '"otlp_http_endpoint":"https://off-lan.example.com"}'
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                resolve_otlp_config(config_path=config_path)

    def test_off_lan_reads_access_headers_from_nested_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "node-config.json"
            config_path.write_text(
                (
                    '{"node_label":"mini-03","network":"off_lan","openclaw_service_name":"openclaw_gateway",'
                    '"otlp_http_endpoint":"https://off-lan.example.com",'
                    '"cloudflare_access_headers":{"CF-Access-Client-Id":"id.from.config","CF-Access-Client-Secret":"secret.from.config"}}'
                ),
                encoding="utf-8",
            )
            cfg = resolve_otlp_config(config_path=config_path)
            self.assertEqual(cfg.cf_access_client_id, "id.from.config")
            self.assertEqual(cfg.cf_access_client_secret, "secret.from.config")
