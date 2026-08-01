from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentInstallerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (
            ROOT / "src" / "fleet_node_observability" / "installers" / "fleet_node_agent.sh"
        ).read_text(encoding="utf-8")
        cls.wrapper = (ROOT / "bin" / "install-fleet-node-agent").read_text(encoding="utf-8")

    def test_one_topology_independent_entrypoint(self) -> None:
        self.assertIn("fleet_node_agent.sh", self.wrapper)
        self.assertNotIn("off_lan", self.installer)
        self.assertNotIn("--network", self.installer)
        self.assertNotIn("tunnel_hostname", self.installer)
        self.assertNotIn("CF-Access", self.installer)

    def test_token_is_file_only_and_never_argv_or_environment(self) -> None:
        self.assertIn("--ingest-token-file", self.installer)
        self.assertNotIn("--token)", self.installer)
        self.assertNotIn("FLEET_INGEST_TOKEN", self.installer)
        self.assertIn("write_agent_secret", self.installer)
        self.assertIn("chmod 0600 \"$AUTH_HEADER_FILE\"", self.installer)

    def test_pinned_binary_is_verified_and_config_is_validated(self) -> None:
        for required in [
            "COLLECTOR_URL",
            "COLLECTOR_SHA256",
            'shasum -a 256 "$ARCHIVE"',
            "Collector SHA256 mismatch",
            "render_agent_config",
            "--collector-binary",
        ]:
            self.assertIn(required, self.installer)

    def test_root_managed_paths_reject_symlink_components(self) -> None:
        self.assertIn("reject_symlink_components", self.installer)
        self.assertIn("must not contain symlink components", self.installer)
        self.assertIn('if [[ -L "$CONFIG_PATH" || ! -f "$CONFIG_PATH" ]]', self.installer)

    def test_agent_is_single_network_owner(self) -> None:
        self.assertIn("com.unblocklabs.fleet-node-agent", self.installer)
        self.assertIn("configure_openclaw_local_otel", self.installer)
        self.assertIn("fleet-node-agent-heartbeat", self.installer)
        self.assertNotIn("configure-openclaw-otel", self.installer)

    def test_cutover_retirement_is_explicit_and_push_only(self) -> None:
        self.assertIn("--retire-legacy-pull", self.installer)
        self.assertIn('"$TELEMETRY_MODE" != "push"', self.installer)
        self.assertIn("keeping legacy transport unchanged", self.installer)
        self.assertIn("per-node Cloudflare tunnel cleanup remains an operator cutover step", self.installer)

    def test_push_mode_rebinds_node_exporter_to_loopback_before_retiring_proxy(self) -> None:
        self.assertIn("install_loopback_node_exporter", self.installer)
        self.assertIn("--web.listen-address=127.0.0.1:9100", self.installer)
        self.assertIn("--collector.textfile.directory=", self.installer)
        install_index = self.installer.index("install_loopback_node_exporter\nfi")
        retire_index = self.installer.index('if [[ "$RETIRE_LEGACY_PULL" -eq 1 ]]')
        self.assertLess(install_index, retire_index)

    def test_health_and_local_sources_are_proved_before_openclaw_rewrite(self) -> None:
        health_index = self.installer.index("fleet-node-agent did not become healthy")
        exporter_index = self.installer.index("node_exporter loopback scrape failed")
        openclaw_index = self.installer.index("configure_openclaw_local_otel")
        self.assertLess(health_index, openclaw_index)
        self.assertLess(exporter_index, openclaw_index)


if __name__ == "__main__":
    unittest.main()
