from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fleet_node_observability.agent import (
    COLLECTOR_RELEASES,
    COLLECTOR_VERSION,
    HEARTBEAT_METRIC,
    LocalNodeContext,
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
            "config_schema_version": 3,
            "node_label": "Mini-03",
            "telemetry_endpoint": "https://telemetry.example.com",
            "codex_usage_enabled": True,
        }
        payload.update(overrides)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "node.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_agent_config(
                path,
                node_user="fleet-mini-03",
                node_home="/Users/fleet-mini-03",
                architecture="arm64",
                homebrew_prefix="/opt/homebrew",
            )

    def test_example_is_exact_schema_3_contract(self) -> None:
        source = json.loads(
            (ROOT / "examples" / "node-agent.example.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(source),
            {
                "config_schema_version",
                "node_label",
                "telemetry_endpoint",
                "codex_usage_enabled",
            },
        )
        config = load_agent_config(
            ROOT / "examples" / "node-agent.example.json",
            node_user="fleet-mini-03",
            node_home="/Users/fleet-mini-03",
            architecture="arm64",
            homebrew_prefix="/opt/homebrew",
        )
        self.assertEqual(config.node_label, "mini_03")
        self.assertTrue(config.codex_usage_enabled)

    def test_rejects_old_missing_and_extra_fields(self) -> None:
        cases = [
            {"config_schema_version": 2},
            {"telemetry_mode": "push"},
            {"network": "lan"},
            {"node_user": "central-must-not-own-this"},
            {"codex_usage_enabled": "true"},
        ]
        for override in cases:
            with self.subTest(override=override), self.assertRaises(ConfigError):
                self.load(**override)
        with self.assertRaisesRegex(ConfigError, "must be a boolean"):
            self.load(codex_usage_enabled=None)

        complete = {
            "config_schema_version": 3,
            "node_label": "mini_03",
            "telemetry_endpoint": "https://telemetry.example.com",
            "codex_usage_enabled": True,
        }
        for missing in complete:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmpdir:
                payload = complete.copy()
                payload.pop(missing)
                path = Path(tmpdir) / "node.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_agent_config(
                        path,
                        node_user="fleet-mini-03",
                        node_home="/Users/fleet-mini-03",
                        architecture="arm64",
                        homebrew_prefix="/opt/homebrew",
                    )

    def test_local_context_is_derived_or_explicit_as_one_complete_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "node.json"
            path.write_text(
                json.dumps(
                    {
                        "config_schema_version": 3,
                        "node_label": "mini-03",
                        "telemetry_endpoint": "https://telemetry.example.com",
                        "codex_usage_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            local = LocalNodeContext(
                node_user="local-node",
                node_home=Path(tmpdir),
                architecture="arm64",
                homebrew_prefix=Path("/opt/homebrew"),
            )
            with patch(
                "fleet_node_observability.agent.resolve_current_node_context",
                return_value=local,
            ):
                config = load_agent_config(path)
            self.assertEqual(config.node_user, "local-node")
            with self.assertRaisesRegex(ConfigError, "explicit context requires"):
                load_agent_config(path, node_user="local-node")

    def test_implicit_root_context_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "node.json"
            path.write_text(
                json.dumps(
                    {
                        "config_schema_version": 3,
                        "node_label": "mini-03",
                        "telemetry_endpoint": "https://telemetry.example.com",
                        "codex_usage_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            with patch("fleet_node_observability.agent.os.geteuid", return_value=0), self.assertRaisesRegex(
                ConfigError, "unprivileged node account, not root"
            ):
                load_agent_config(path)

    def test_local_paths_and_listeners_are_derived(self) -> None:
        config = self.load()
        self.assertEqual(config.node_exporter_target, "127.0.0.1:9100")
        self.assertEqual(config.local_otlp_endpoint, "127.0.0.1:4318")
        self.assertEqual(
            config.node_exporter_textfile_dir,
            Path("/opt/homebrew/var/lib/node_exporter/textfile_collector"),
        )
        self.assertEqual(
            config.openclaw_config_path,
            Path("/Users/fleet-mini-03/.openclaw/openclaw.json"),
        )

    def test_requires_https_base_endpoint(self) -> None:
        for endpoint in [
            "http://telemetry.example.com",
            "https://telemetry.example.com/v1/metrics",
            "https://user@telemetry.example.com",
            "https://telemetry.example.com:0",
            " https://telemetry.example.com",
        ]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ConfigError):
                self.load(telemetry_endpoint=endpoint)
        self.assertEqual(
            self.load(telemetry_endpoint="https://telemetry.example.com:8443/").telemetry_endpoint,
            "https://telemetry.example.com:8443",
        )

    def test_all_six_pipelines_are_unconditional(self) -> None:
        for codex_enabled in (True, False):
            rendered = render_collector_config(
                self.load(codex_usage_enabled=codex_enabled)
            )
            self.assertEqual(
                set(rendered["service"]["pipelines"]),
                {
                    "logs/openclaw",
                    "traces/openclaw",
                    "metrics/openclaw",
                    "metrics/agent",
                    "metrics/host",
                    "metrics/heartbeat",
                },
            )
            serialized = json.dumps(rendered)
            self.assertNotIn("fleet.transport", serialized)
            self.assertIn("fleet.claimed_node", serialized)

    def test_batches_compresses_and_uses_bounded_persistent_queues(self) -> None:
        rendered = render_collector_config(self.load())
        self.assertIn("file_storage/fleet", rendered["service"]["extensions"])
        for name, exporter in rendered["exporters"].items():
            with self.subTest(exporter=name):
                self.assertEqual(exporter["compression"], "gzip")
                queue = exporter["sending_queue"]
                self.assertEqual(queue["num_consumers"], 1)
                self.assertEqual(queue["sizer"], "bytes")
                self.assertGreater(queue["queue_size"], 0)
                self.assertEqual(queue["storage"], "file_storage/fleet")
                self.assertFalse(queue["block_on_overflow"])
                self.assertLess(queue["batch"]["max_size"], queue["queue_size"])

    def test_heartbeat_has_independent_receiver_and_queue(self) -> None:
        rendered = render_collector_config(self.load())
        scrape = rendered["receivers"]["prometheus/heartbeat"]["config"]["scrape_configs"][0]
        self.assertEqual(scrape["metric_relabel_configs"][0], {
            "source_labels": ["__name__"],
            "regex": HEARTBEAT_METRIC,
            "action": "keep",
        })
        self.assertEqual(
            rendered["service"]["pipelines"]["metrics/heartbeat"]["exporters"],
            ["otlphttp/heartbeat"],
        )

    def test_config_is_secret_free_and_openclaw_is_loopback_only(self) -> None:
        config = self.load()
        rendered = render_collector_config(config)
        for exporter in rendered["exporters"].values():
            self.assertRegex(exporter["headers"]["Authorization"], r"^\$\{file:/")
        self.assertEqual(
            render_openclaw_local_settings(config),
            {"endpoint": "http://127.0.0.1:4318"},
        )

    def test_authorization_header_uses_normalized_node_identity(self) -> None:
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
