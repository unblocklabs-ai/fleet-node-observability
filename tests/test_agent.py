from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fleet_node_observability.agent import (
    COLLECTOR_RELEASES,
    COLLECTOR_VERSION,
    HEARTBEAT_METRIC,
    load_agent_config,
    render_authorization_header,
    render_collector_config,
    render_openclaw_local_settings,
)
from fleet_node_observability.config import ConfigError


ROOT = Path(__file__).resolve().parents[1]


class AgentConfigTest(unittest.TestCase):
    def load(self, **overrides: object):
        payload: dict[str, object] = {
            "node_label": "Mini-03",
            "node_user": "fleet-mini-03",
            "node_home": "/Users/fleet-mini-03",
            "telemetry_mode": "dual",
            "telemetry_endpoint": "https://telemetry.example.com",
        }
        payload.update(overrides)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "node.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_agent_config(path)

    def test_example_loads_without_network_topology(self) -> None:
        config = load_agent_config(ROOT / "examples" / "node-agent.example.json")
        self.assertEqual(config.telemetry_mode, "dual")
        self.assertEqual(config.node_exporter_target, "127.0.0.1:9100")
        source = (ROOT / "examples" / "node-agent.example.json").read_text(encoding="utf-8")
        self.assertNotIn('"network"', source)
        self.assertNotIn("tunnel", source)
        self.assertNotIn("cloudflare", source.lower())

    def test_requires_https_base_endpoint(self) -> None:
        invalid_endpoints = [
            "http://telemetry.example.com",
            "https://telemetry.example.com/v1/metrics",
            "https://user@telemetry.example.com",
            "https://user:password@telemetry.example.com",
            "https://telemetry.example.com:not-a-port",
            "https://telemetry.example.com:0",
            "https://telemetry.example.com:65536",
            "https://telemetry.example.com:",
            " https://telemetry.example.com",
        ]
        for endpoint in invalid_endpoints:
            with self.subTest(endpoint=endpoint), self.assertRaises(ConfigError):
                self.load(telemetry_endpoint=endpoint)
        for endpoint in [
            "https://telemetry.example.com",
            "https://telemetry.example.com:443",
            "https://telemetry.example.com:8443/",
        ]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.load(telemetry_endpoint=endpoint).telemetry_endpoint,
                    endpoint.rstrip("/"),
                )

    def test_all_local_listeners_and_scrapes_must_be_loopback(self) -> None:
        for field in [
            "node_exporter_target",
            "local_otlp_endpoint",
            "collector_metrics_endpoint",
            "health_endpoint",
        ]:
            with self.subTest(field=field), self.assertRaises(ConfigError):
                self.load(**{field: "0.0.0.0:9999"})

    def test_runtime_paths_cannot_escape_managed_node_directory(self) -> None:
        with self.assertRaises(ConfigError):
            self.load(queue_directory="/tmp/fleet-queue")

    def test_managed_file_and_state_paths_must_not_overlap(self) -> None:
        base = Path("/Users/fleet-mini-03/.openclaw/fleet-node-observability")
        cases = [
            {
                "collector_config_path": str(base / "same"),
                "authorization_header_path": str(base / "same"),
            },
            {
                "collector_config_path": str(base / "same"),
                "collector_binary_path": str(base / "same"),
            },
            {
                "authorization_header_path": str(base / "same"),
                "collector_binary_path": str(base / "same"),
            },
            {"collector_config_path": str(base / "bin")},
            {"queue_directory": str(base / "state")},
            {"queue_directory": str(base / "state" / "queue")},
            {
                "queue_directory": str(base / "queue-file"),
                "collector_config_path": str(base / "queue-file"),
            },
            {"queue_directory": str(base)},
            {
                "collector_config_path": str(base / "config-root"),
                "queue_directory": str(base / "config-root" / "queue"),
            },
            {"collector_config_path": str(base / "state" / "collector.json")},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ConfigError):
                self.load(**overrides)

    def test_textfile_directory_is_limited_to_homebrew_or_node_home(self) -> None:
        with self.assertRaises(ConfigError):
            self.load(node_exporter_textfile_dir="/etc/fleet-textfiles")
        config = self.load(
            node_exporter_textfile_dir="/Users/fleet-mini-03/.openclaw/textfiles"
        )
        self.assertEqual(
            config.node_exporter_textfile_dir,
            Path("/Users/fleet-mini-03/.openclaw/textfiles"),
        )

    def test_mode_controls_only_host_pipelines(self) -> None:
        pull = render_collector_config(self.load(telemetry_mode="pull"))
        dual = render_collector_config(self.load(telemetry_mode="dual"))
        push = render_collector_config(self.load(telemetry_mode="push"))

        for rendered in [pull, dual, push]:
            pipelines = rendered["service"]["pipelines"]
            self.assertIn("logs/openclaw", pipelines)
            self.assertIn("traces/openclaw", pipelines)
            self.assertIn("metrics/openclaw", pipelines)
            self.assertIn("metrics/agent", pipelines)
        self.assertNotIn("metrics/host", pull["service"]["pipelines"])
        self.assertNotIn("metrics/heartbeat", pull["service"]["pipelines"])
        for rendered in [dual, push]:
            self.assertIn("metrics/host", rendered["service"]["pipelines"])
            self.assertIn("metrics/heartbeat", rendered["service"]["pipelines"])

    def test_batches_compresses_and_uses_bounded_persistent_queues(self) -> None:
        rendered = render_collector_config(self.load())
        self.assertIn("file_storage/fleet", rendered["service"]["extensions"])
        for name, exporter in rendered["exporters"].items():
            with self.subTest(exporter=name):
                self.assertEqual(exporter["compression"], "gzip")
                self.assertEqual(exporter["sending_queue"]["num_consumers"], 1)
                self.assertEqual(exporter["sending_queue"]["sizer"], "bytes")
                self.assertGreater(exporter["sending_queue"]["queue_size"], 0)
                self.assertEqual(exporter["sending_queue"]["storage"], "file_storage/fleet")
                self.assertFalse(exporter["sending_queue"]["block_on_overflow"])
                request_batch = exporter["sending_queue"]["batch"]
                self.assertEqual(request_batch["sizer"], "bytes")
                self.assertEqual(request_batch["flush_timeout"], "1s")
                self.assertGreater(request_batch["max_size"], 0)
                self.assertLessEqual(request_batch["min_size"], request_batch["max_size"])
                self.assertLess(request_batch["max_size"], exporter["sending_queue"]["queue_size"])
                self.assertGreater(int(exporter["retry_on_failure"]["max_elapsed_time"].rstrip("mh")), 0)
        self.assertLess(
            rendered["exporters"]["otlp_http/heartbeat"]["sending_queue"]["queue_size"],
            rendered["exporters"]["otlp_http/logs"]["sending_queue"]["queue_size"],
        )
        self.assertEqual(
            rendered["exporters"]["otlp_http/heartbeat"]["sending_queue"]["batch"]["max_size"],
            64 * 1024,
        )

    def test_heartbeat_has_own_receiver_pipeline_and_queue(self) -> None:
        rendered = render_collector_config(self.load())
        heartbeat_scrape = rendered["receivers"]["prometheus/heartbeat"]["config"]["scrape_configs"][0]
        self.assertEqual(heartbeat_scrape["metric_relabel_configs"][0]["regex"], HEARTBEAT_METRIC)
        self.assertEqual(heartbeat_scrape["metric_relabel_configs"][0]["action"], "keep")
        self.assertEqual(
            rendered["service"]["pipelines"]["metrics/heartbeat"]["exporters"],
            ["otlp_http/heartbeat"],
        )

    def test_config_contains_no_secret_and_reads_protected_header_file(self) -> None:
        rendered = render_collector_config(self.load())
        serialized = json.dumps(rendered)
        self.assertNotIn("token", serialized.lower())
        for exporter in rendered["exporters"].values():
            self.assertRegex(exporter["headers"]["Authorization"], r"^\$\{file:/")

    def test_openclaw_sends_to_loopback_without_headers(self) -> None:
        settings = render_openclaw_local_settings(self.load())
        self.assertEqual(settings["endpoint"], "http://127.0.0.1:4318")
        self.assertEqual(settings["headers"], {})

    def test_authorization_header_uses_normalized_node_identity(self) -> None:
        import base64

        header = render_authorization_header("Mini-03", "secret")
        decoded = base64.b64decode(header.removeprefix("Basic ")).decode("utf-8")
        self.assertEqual(decoded, "mini_03:secret")

    def test_collector_release_is_pinned_for_both_mac_architectures(self) -> None:
        self.assertEqual(COLLECTOR_VERSION, "0.157.0")
        self.assertEqual(set(COLLECTOR_RELEASES), {"darwin_amd64", "darwin_arm64"})
        for release in COLLECTOR_RELEASES.values():
            self.assertIn(f"v{COLLECTOR_VERSION}", release["url"])
            self.assertRegex(release["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
