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
            '<string>$(xml_escape "$CODEX_COLLECTOR")</string>',
            '<string>$(xml_escape "$GATEWAY_HEALTH")</string>',
            '<string>$(xml_escape "$THERMAL_COLLECTOR")</string>',
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

    def test_lan_installer_escapes_plist_and_prometheus_values(self) -> None:
        for required in [
            "xml_escape()",
            "prom_escape()",
            'elif char == "\\n":',
            'out.append("\\\\n")',
            "codepoint < 0x20",
            "normalize_bool_flag()",
            "require_uint_range \"node_exporter_port\"",
            'CODEX_USAGE_ENABLED="$(normalize_bool_flag "codex_usage_enabled" "$CODEX_USAGE_ENABLED")"',
            "1|true|yes|on)",
            "0|false|no|off)",
            'fleet_node_exporter_textfile_install_info{node="$(prom_escape "$NODE")"} 1',
            '<string>$(xml_escape "$OPENCLAW_READY_URL")</string>',
        ]:
            self.assertIn(required, self.installer)

    def test_lan_installer_validates_node_home_against_system_account(self) -> None:
        for required in [
            "resolve_node_home()",
            "system_home_for_user()",
            "canonical_existing_dir()",
            "must not include symlinked parent directories",
            'NODE_HOME="$(resolve_node_home "$NODE_USER" "$NODE_HOME")"',
            "node_home must match the system home",
        ]:
            self.assertIn(required, self.installer)

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
            "require_allowed_prefix",
            "TCP $port is owned by unmanaged pid=$pid; refusing to kill it",
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
            '<string>$(xml_escape "$PROXY_SCRIPT")</string>',
            '<string>$(xml_escape "$CODEX_COLLECTOR")</string>',
            '<string>$(xml_escape "$GATEWAY_HEALTH")</string>',
            '<string>$(xml_escape "$THERMAL_COLLECTOR")</string>',
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

    def test_off_lan_installer_rejects_unsafe_paths_and_ports(self) -> None:
        for required in [
            "reject_unsafe_path()",
            "canonical_managed_path()",
            "require_allowed_prefix",
            "require_user_home_path()",
            "must not be a symlink",
            "must not include symlinked parent directories",
            'TEXTFILE_DIR="$(require_allowed_prefix \\',
            'TOKEN_FILE="$(require_allowed_prefix \\',
            'canonical_path="$(canonical_managed_path "$name" "$path")"',
            'canonical_prefix="$(canonical_managed_path "allowed prefix for $name" "$prefix")"',
            "require_uint_range \"node_exporter_port\"",
            "require_uint_range \"codex_usage_interval_secs\"",
            "chown \"$USER_NAME\" \"$TEXTFILE_DIR\"",
            'elif char == "\\n":',
            'out.append("\\\\n")',
            "codepoint < 0x20",
        ]:
            self.assertIn(required, self.installer)
        self.assertGreaterEqual(self.installer.count('TEXTFILE_DIR="$(require_allowed_prefix \\'), 2)
        self.assertGreaterEqual(self.installer.count('TOKEN_FILE="$(require_allowed_prefix \\'), 2)
        self.assertNotIn('chown -R "$USER_NAME" "$TEXTFILE_DIR"', self.installer)
        self.assertNotIn("chown -R", self.installer)

    def test_off_lan_installer_validates_root_mutated_user_home_paths(self) -> None:
        for required in [
            'OPENCLAW_DIR="$(require_user_home_path "openclaw_dir" "$OPENCLAW_DIR")"',
            'SECRET_DIR="$(require_user_home_path "secret_dir" "$SECRET_DIR")"',
            'LOG_DIR="$(require_user_home_path "log_dir" "$LOG_DIR")"',
            'RUNTIME_DIR="$(require_user_home_path "runtime_dir" "$RUNTIME_DIR")"',
            'RUNTIME_BIN_DIR="$(require_user_home_path "runtime_bin_dir" "$RUNTIME_BIN_DIR")"',
            'RUNTIME_SRC_DIR="$(require_user_home_path "runtime_src_dir" "$RUNTIME_SRC_DIR")"',
            'CRON_BACKUP_DIR="$(require_user_home_path "cron_backup_dir" "$OPENCLAW_DIR/backups")"',
            'BACKUP="$(require_user_home_path "cron_backup_file" "$CRON_BACKUP_DIR/crontab-before-off-lan-host-metrics-$(date +%Y%m%d%H%M%S)")"',
            'CODEX_TEXTFILE="$(require_allowed_prefix "codex_textfile" "$CODEX_TEXTFILE" "$TEXTFILE_DIR")"',
            'GATEWAY_TEXTFILE="$(require_allowed_prefix "gateway_textfile" "$GATEWAY_TEXTFILE" "$TEXTFILE_DIR")"',
            'THERMAL_TEXTFILE="$(require_allowed_prefix "thermal_textfile" "$THERMAL_TEXTFILE" "$TEXTFILE_DIR")"',
            'METRICS_INFO="$(require_allowed_prefix "metrics_info" "$METRICS_INFO" "$TEXTFILE_DIR")"',
            'plist="$(require_user_home_path "legacy LaunchAgent plist" "$USER_HOME/Library/LaunchAgents/$label.plist")"',
            'disabled="$(require_user_home_path "legacy LaunchAgent archive" "$plist.disabled-$(date -u +%Y%m%dT%H%M%SZ)")"',
            'ensure_user_dir "openclaw_dir" "$OPENCLAW_DIR" 0700',
            'ensure_user_dir "secret_dir" "$SECRET_DIR" 0700',
            'ensure_user_dir "log_dir" "$LOG_DIR" 0755',
            'ensure_user_dir "cron_backup_dir" "$CRON_BACKUP_DIR" 0700',
            'chown "$USER_NAME" "$runtime_dir" "$runtime_bin_dir" "$runtime_src_dir"',
        ]:
            self.assertIn(required, self.installer)

    def test_installers_accept_central_boolean_spellings_for_codex_usage(self) -> None:
        lan_installer = (ROOT / "src/fleet_node_observability/installers/lan_host_metrics.sh").read_text(
            encoding="utf-8"
        )
        for installer in [self.installer, lan_installer]:
            self.assertIn("normalize_bool_flag()", installer)
            self.assertIn("1|true|yes|on)", installer)
            self.assertIn("0|false|no|off)", installer)
            self.assertIn("must be a boolean value", installer)
            self.assertIn('CODEX_USAGE_ENABLED="$(normalize_bool_flag "codex_usage_enabled" "$CODEX_USAGE_ENABLED")"', installer)

    def test_off_lan_installer_validates_node_home_against_system_account(self) -> None:
        for required in [
            "resolve_node_home()",
            "system_home_for_user()",
            "canonical_existing_dir()",
            "must not include symlinked parent directories",
            'USER_HOME="$(resolve_node_home "$USER_NAME" "$USER_HOME")"',
            "node_home must match the system home",
        ]:
            self.assertIn(required, self.installer)
        self.assertNotIn('USER_HOME="/Users/${USER_NAME}"', self.installer)

    def test_off_lan_installer_revalidates_listener_before_sigkill(self) -> None:
        for required in [
            "listener_pid_is_managed()",
            'if ! listener_pid_is_managed "$pid"; then',
            "now has unmanaged pid=$pid after graceful stop; skipping SIGKILL",
            'kill -9 "$pid"',
        ]:
            self.assertIn(required, self.installer)

    def test_off_lan_wrapper_executes_impl(self) -> None:
        self.assertIn("src/fleet_node_observability/installers/off_lan_host_metrics.sh", self.wrapper)

    def test_installers_do_not_reference_private_inventory(self) -> None:
        for path in [self.installer, self.wrapper]:
            self.assertNotIn("fleet/nodes.json", path)


class SharedInstallerContractTest(unittest.TestCase):
    def test_cleanup_wrapper_exists(self) -> None:
        cleanup_wrapper = (ROOT / "bin/cleanup-legacy-shippers").read_text(encoding="utf-8")
        self.assertIn("src/fleet_node_observability/installers/cleanup_legacy_shippers.sh", cleanup_wrapper)
