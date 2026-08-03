from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AgentInstallerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (
            ROOT / "src/fleet_node_observability/installers/fleet_node_agent.sh"
        ).read_text(encoding="utf-8")
        cls.wrapper = (ROOT / "bin/install-fleet-node-agent").read_text(encoding="utf-8")

    def test_one_topology_independent_entrypoint(self) -> None:
        self.assertIn("fleet_node_agent.sh", self.wrapper)
        for forbidden in [
            "--network",
            "off_lan",
            "CF-Access",
            "tunnel_hostname",
            "--retire-legacy-pull",
            "--skip-openclaw-config",
            "telemetry_mode",
        ]:
            self.assertNotIn(forbidden, self.installer)

    def test_token_is_file_only_and_collector_artifact_is_pinned(self) -> None:
        self.assertIn("--ingest-token-file", self.installer)
        self.assertNotIn("FLEET_INGEST_TOKEN", self.installer)
        self.assertNotIn("--token)", self.installer)
        for required in [
            "COLLECTOR_URL",
            "COLLECTOR_SHA256",
            'shasum -a 256 "$ARCHIVE"',
            "Collector SHA256 mismatch",
            "--collector-binary",
        ]:
            self.assertIn(required, self.installer)

    def test_root_snapshots_protected_token_before_managed_writes(self) -> None:
        snapshot = self.installer.index('TOKEN_SNAPSHOT="$TMP_DIR/ingest-token"')
        runtime = self.installer.index(
            'RUNTIME_DIR="$NODE_HOME/.openclaw/fleet-node-observability"'
        )
        self.assertLess(self.installer.index('if [[ "$(id -u)" -ne 0 ]]'), snapshot)
        self.assertLess(snapshot, runtime)
        snapshot_end = self.installer.index("\nPY\n", snapshot) + len("\nPY\n")
        snapshot_block = self.installer[snapshot:snapshot_end]
        for required in [
            '"$TOKEN_FILE" "$TOKEN_SNAPSHOT" "$NODE_UID"',
            "read_protected_token(Path(sys.argv[1]))",
            "os.fchown(descriptor, int(sys.argv[3]), -1)",
            "os.fchmod(descriptor, 0o400)",
            "handle.write(token.encode())",
            "os.fsync(handle.fileno())",
        ]:
            self.assertIn(required, snapshot_block)
        self.assertNotIn("sudo -u", snapshot_block)
        self.assertIn('--token-file "$TOKEN_SNAPSHOT"', self.installer)
        self.assertNotIn('--token-file "$TOKEN_FILE"', self.installer)

    def test_source_config_is_snapshotted_and_only_canonical_config_reaches_helpers(self) -> None:
        self.assertIn('SOURCE_CONFIG="$TMP_DIR/source-node-config.json"', self.installer)
        self.assertIn("os.O_RDONLY | os.O_NOFOLLOW", self.installer)
        self.assertIn("--config \"$SOURCE_CONFIG\" --token-file", self.installer)
        self.assertIn("--config \"$SOURCE_CONFIG\" --output", self.installer)
        self.assertIn("--config \"$SOURCE_CONFIG\"", self.installer)
        self.assertNotIn("--config \"$RESOLVED_MANIFEST\"", self.installer)
        self.assertIn('chmod 0444 "$SOURCE_CONFIG"', self.installer)
        self.assertIn("verify_resolved_manifest", self.installer)

    def test_node_context_and_homebrew_paths_are_resolved_locally(self) -> None:
        for required in [
            "--node-user",
            'dscl . -read "/Users/$NODE_USER" NFSHomeDirectory',
            'NODE_HOME="$(cd "$SYSTEM_HOME" && pwd -P)"',
            'arm64) ARCHITECTURE="arm64"; PLATFORM="darwin_arm64"',
            'x86_64) ARCHITECTURE="x86_64"; PLATFORM="darwin_amd64"',
            "select_homebrew()",
            'HOMEBREW_PREFIX="$(select_homebrew)"',
            'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1',
            '"$BREW_BIN" install node_exporter',
        ]:
            self.assertIn(required, self.installer)
        self.assertNotIn("--node-exporter-textfile-dir", self.installer)

    def test_managed_node_paths_reject_symlink_components(self) -> None:
        start = self.installer.index("reject_symlink_components() {")
        end = self.installer.index("\n}\n", start) + len("\n}\n")
        function = self.installer[start:end]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "target"
            target.mkdir()
            link = root / "runtime"
            link.symlink_to(target, target_is_directory=True)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    "set -euo pipefail\n" + function + 'reject_symlink_components path "$1"\n',
                    "path-test",
                    str(link / "collector.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink components", result.stderr)
            self.assertEqual(list(target.iterdir()), [])

    def test_runtime_writes_use_node_authority(self) -> None:
        runtime_start = self.installer.index(
            'RUNTIME_DIR="$NODE_HOME/.openclaw/fleet-node-observability"'
        )
        runtime_end = self.installer.index("escape_xml()", runtime_start)
        runtime = self.installer[runtime_start:runtime_end]
        for required in [
            'run_as_node mkdir -p "$RUNTIME_BIN"',
            'run_as_node chmod 0700 "$RUNTIME_DIR"',
            'run_as_node install -m 0755 "$STAGED_COLLECTOR_BIN" "$COLLECTOR_BIN"',
            "run_as_node env PYTHONPATH=",
        ]:
            self.assertIn(required, runtime)
        self.assertNotIn("chown -R", runtime)

    def test_scheduled_collectors_use_stable_installed_runtime(self) -> None:
        self.assertIn('RUNTIME_PYTHON="$RUNTIME_DIR/python"', self.installer)
        self.assertIn(
            'PATH_VALUE="$NODE_HOME/.npm-global/bin:$NODE_HOME/.local/bin:'
            '$NODE_HOME/.local/share/fnm/aliases/default/bin:$HOMEBREW_PREFIX/bin:'
            '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"',
            self.installer,
        )
        self.assertIn('$RUNTIME_BIN/openclaw-gateway-health', self.installer)
        self.assertIn(
            '$RUNTIME_PYTHON/fleet_node_observability/commands/collect_macos_thermal.py',
            self.installer,
        )
        self.assertIn(
            '$RUNTIME_PYTHON/fleet_node_observability/commands/collect_codex_usage.py',
            self.installer,
        )
        self.assertIn('configure_openclaw_local_otel.py; do', self.installer)
        self.assertIn(
            'run_as_node env PYTHONPATH="$RUNTIME_PYTHON" \\\n'
            '  "$PYTHON_BIN" -m fleet_node_observability.commands.configure_openclaw_local_otel',
            self.installer,
        )
        self.assertIn(
            'fleet_node_observability.commands.ensure_openclaw_diagnostics_otel',
            self.installer,
        )
        scheduled = self.installer[self.installer.index('GATEWAY_SCRIPT=') :]
        self.assertNotIn('$REPO_DIR/src', scheduled)

    def test_single_final_service_layout_and_codex_capability(self) -> None:
        for label in [
            "com.unblocklabs.fleet-node-agent",
            "com.unblocklabs.fleet-node-agent-heartbeat",
            "com.unblocklabs.node-exporter",
            "com.unblocklabs.openclaw-gateway-health-textfile",
            "com.unblocklabs.macos-thermal-textfile",
            "com.unblocklabs.codex-usage-textfile",
        ]:
            self.assertIn(label, self.installer)
        self.assertIn('--web.listen-address=127.0.0.1:9100', self.installer)
        self.assertIn('--no-collector.thermal', self.installer)
        self.assertIn('if [[ "$CODEX_USAGE_ENABLED" == "True" ]]', self.installer)
        self.assertIn('rm -f "$CODEX_PLIST"', self.installer)

    def test_health_and_sources_are_proved_before_openclaw_rewrite(self) -> None:
        health = self.installer.index("fleet-node-agent did not become healthy")
        exporter = self.installer.index("node_exporter loopback scrape failed")
        openclaw = self.installer.index(
            '"$PYTHON_BIN" -m fleet_node_observability.commands.configure_openclaw_local_otel'
        )
        self.assertLess(health, openclaw)
        self.assertLess(exporter, openclaw)
        self.assertIn("restart OpenClaw after reviewing", self.installer)


if __name__ == "__main__":
    unittest.main()
