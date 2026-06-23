from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class LanInstallerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (ROOT / "src/fleet_node_observability/installers/lan_host_metrics.sh").read_text(encoding="utf-8")
        cls.wrapper = (ROOT / "bin/install-lan-host-metrics").read_text(encoding="utf-8")

    def test_lan_installer_reads_node_local_config(self) -> None:
        self.assertIn("--config", self.installer)
        self.assertIn("node_label", self.installer)
        self.assertIn("node_exporter_tunnel_hostname", self.installer)
        self.assertIn("config_value", self.installer)

    def test_lan_installer_supports_explicit_override_flags(self) -> None:
        for token in [
            "--node-label",
            "--node-user",
            "--node-home",
            "--node-exporter-port",
            "--openclaw-ready-url",
            "--codex-profile",
            "--codex-usage-interval-secs",
            "--node-exporter-textfile-dir",
            "--force-user",
        ]:
            self.assertIn(token, self.installer)

    def test_lan_installer_contract(self) -> None:
        for required in [
            'grep -q \'^node_cpu_seconds_total\'',
            "Homebrew formula node_exporter",
            "Library/LaunchAgents/$NODE_EXPORTER_LABEL",
            "fleet_node_exporter_textfile_install_info",
            "com.unblocklabs.codex-usage-textfile",
            "com.unblocklabs.openclaw-gateway-health-textfile",
            "com.unblocklabs.macos-thermal-textfile",
            "StartInterval",
            "CODEX_PROFILE",
            "openclaw-gateway-health",
            "collect-macos-thermal",
        ]:
            self.assertIn(required, self.installer)

    def test_lan_installer_uses_self_contained_runtime(self) -> None:
        for required in [
            'RUNTIME_DIR="$NODE_HOME/.openclaw/fleet-node-observability"',
            "RUNTIME_BIN_DIR=\"$RUNTIME_DIR/bin\"",
            "RUNTIME_SRC_DIR=\"$RUNTIME_DIR/src\"",
            "install_runtime_tree \"$RUNTIME_DIR\" \"$RUNTIME_BIN_DIR\" \"$RUNTIME_SRC_DIR\"",
            "cp -R bin/. \"$runtime_bin_dir/\"",
            "cp -R src/. \"$runtime_src_dir/\"",
            '$SCRIPT_DIR/../../..',
            "CODEX_COLLECTOR=\"$RUNTIME_BIN_DIR/collect-codex-usage\"",
            "GATEWAY_HEALTH=\"$RUNTIME_BIN_DIR/openclaw-gateway-health\"",
            "THERMAL_COLLECTOR=\"$RUNTIME_BIN_DIR/collect-macos-thermal\"",
            '<string>$CODEX_COLLECTOR</string>',
            '<string>$GATEWAY_HEALTH</string>',
            '<string>$THERMAL_COLLECTOR</string>',
        ]:
            self.assertIn(required, self.installer)

    def test_lan_installer_does_not_copy_to_legacy_isolated_bins(self) -> None:
        self.assertNotIn("$HOME/bin/collect-codex-usage", self.installer)
        self.assertNotIn("$HOME/bin/collect-macos-thermal", self.installer)
        self.assertNotIn("$HOME/bin/openclaw-gateway-health", self.installer)
        self.assertNotIn("$HOME/bin/fleet-node-exporter-proxy", self.installer)
        self.assertNotIn("install -m", self.installer)

    def test_lan_installer_has_macos_guard_and_no_central_inventory_dependency(self) -> None:
        self.assertIn('if [[ "$(uname -s)" != "Darwin" ]]', self.installer)
        self.assertNotIn("fleet/nodes.json", self.installer)

    def test_lan_wrapper_executes_impl(self) -> None:
        self.assertIn("src/fleet_node_observability/installers/lan_host_metrics.sh", self.wrapper)


class OffLanInstallerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (ROOT / "src/fleet_node_observability/installers/off_lan_host_metrics.sh").read_text(
            encoding="utf-8"
        )
        cls.wrapper = (ROOT / "bin/install-off-lan-host-metrics").read_text(encoding="utf-8")

    def test_off_lan_installer_requires_root(self) -> None:
        self.assertIn('if [[ "$(id -u)" -ne 0 ]]', self.installer)

    def test_off_lan_installer_reads_node_local_config(self) -> None:
        self.assertIn("--config", self.installer)
        self.assertIn("node_exporter_tunnel_hostname", self.installer)
        self.assertIn("config_value", self.installer)

    def test_off_lan_installer_supports_explicit_override_flags(self) -> None:
        for token in [
            "--node-label",
            "--node-user",
            "--node-home",
            "--node-exporter-port",
            "--node-exporter-tunnel-hostname",
            "--openclaw-ready-url",
            "--codex-profile",
            "--fleet-node-exporter-scrape-token-file",
            "--node-exporter-textfile-dir",
            "--codex-usage-interval-secs",
        ]:
            self.assertIn(token, self.installer)

    def test_off_lan_installer_contract(self) -> None:
        for required in [
            "/Library/LaunchDaemons",
            "UserName",
            "com.unblocklabs.node-exporter",
            "com.unblocklabs.node-exporter-proxy",
            "com.unblocklabs.codex-usage-textfile",
            "com.unblocklabs.openclaw-gateway-health-textfile",
            "com.unblocklabs.macos-thermal-textfile",
            "127.0.0.1:$NODE_EXPORTER_PORT",
            "FLEET_NODE_EXPORTER_SCRAPE_TOKEN_FILE",
            "kill_tcp_listener \"$NODE_EXPORTER_PORT\"",
            "kill_tcp_listener 19100",
            "lsof is required to clear stale node_exporter/proxy listeners before install.",
            "http://127.0.0.1:19100/metrics",
            'curl -fsS --max-time 5 -H "X-Fleet-Scrape-Token: $TOKEN" -o "$METRICS_TMP" "http://127.0.0.1:19100/metrics"',
            'fleet-host-metrics-ensure[.]sh|fleet-host-textfiles-refresh[.]sh',
            'grep -q \'^node_cpu_seconds_total\' "$METRICS_TMP"',
            'curl -fsS --max-time 5 -o "$METRICS_TMP" "http://127.0.0.1:$NODE_EXPORTER_PORT/metrics"',
        ]:
            self.assertIn(required, self.installer)

    def test_off_lan_installer_uses_self_contained_runtime(self) -> None:
        for required in [
            'RUNTIME_DIR="$OPENCLAW_DIR/fleet-node-observability"',
            "RUNTIME_BIN_DIR=\"$RUNTIME_DIR/bin\"",
            "RUNTIME_SRC_DIR=\"$RUNTIME_DIR/src\"",
            "install_runtime_tree \"$RUNTIME_DIR\" \"$RUNTIME_BIN_DIR\" \"$RUNTIME_SRC_DIR\"",
            "cp -R bin/. \"$runtime_bin_dir/\"",
            "cp -R src/. \"$runtime_src_dir/\"",
            '$SCRIPT_DIR/../../..',
            "CODEX_COLLECTOR=\"$RUNTIME_BIN_DIR/collect-codex-usage\"",
            "GATEWAY_HEALTH=\"$RUNTIME_BIN_DIR/openclaw-gateway-health\"",
            "THERMAL_COLLECTOR=\"$RUNTIME_BIN_DIR/collect-macos-thermal\"",
            "PROXY_SCRIPT=\"$RUNTIME_BIN_DIR/fleet-node-exporter-proxy\"",
            '<string>$PROXY_SCRIPT</string>',
            '<string>$CODEX_COLLECTOR</string>',
            '<string>$GATEWAY_HEALTH</string>',
            '<string>$THERMAL_COLLECTOR</string>',
            "pkill -u \"$USER_NAME\" -f 'fleet-node-exporter-proxy[.]py'",
            "pkill -u \"$USER_NAME\" -f 'fleet-node-exporter-proxy$'",
        ]:
            self.assertIn(required, self.installer)

    def test_off_lan_installer_does_not_copy_to_legacy_isolated_bins(self) -> None:
        self.assertNotIn("OPENCLAW_BIN", self.installer)
        self.assertNotIn("USER_BIN", self.installer)
        self.assertNotIn("install -m 0755 \"$REPO_DIR/scripts", self.installer)
        self.assertNotIn("$OPENCLAW_DIR/bin/collect-codex-usage", self.installer)
        self.assertNotIn("$OPENCLAW_DIR/bin/collect-macos-thermal", self.installer)
        self.assertNotIn("$OPENCLAW_DIR/bin/openclaw-gateway-health", self.installer)
        self.assertNotIn("$OPENCLAW_DIR/bin/fleet-node-exporter-proxy", self.installer)

    def test_off_lan_installer_has_proxy_and_no_otlp_dependency(self) -> None:
        self.assertIn("FLEET_NODE_EXPORTER_SCRAPE_TOKEN_FILE", self.installer)
        self.assertNotIn("OTEL_EXPORTER_OTLP", self.installer)

    def test_off_lan_installer_has_macos_guard(self) -> None:
        self.assertIn('if [[ "$(uname -s)" != "Darwin" ]]', self.installer)

    def test_off_lan_wrapper_executes_impl(self) -> None:
        self.assertIn("src/fleet_node_observability/installers/off_lan_host_metrics.sh", self.wrapper)

    def test_installers_do_not_reference_private_inventory(self) -> None:
        for path in [self.installer, self.wrapper]:
            self.assertNotIn("fleet/nodes.json", path)


class SharedInstallerContractTest(unittest.TestCase):
    def test_cleanup_wrapper_exists(self) -> None:
        cleanup_wrapper = (ROOT / "bin/cleanup-legacy-shippers").read_text(encoding="utf-8")
        self.assertIn("src/fleet_node_observability/installers/cleanup_legacy_shippers.sh", cleanup_wrapper)
