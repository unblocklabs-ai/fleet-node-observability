from __future__ import annotations

import os
import subprocess
import tempfile
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

    def run_path_rejection(self, path: Path) -> subprocess.CompletedProcess[str]:
        function_start = self.installer.index("reject_symlink_components() {")
        function_end = self.installer.index("\n}\n", function_start) + len("\n}\n")
        function = self.installer[function_start:function_end]
        return subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail\n"
                + function
                + 'reject_symlink_components "managed node path" "$1"\n',
                "fleet-node-agent-path-test",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

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
        self.assertIn('run_as_node env PYTHONPATH="$REPO_DIR/src"', self.installer)
        self.assertNotIn('chown "$NODE_USER" "$AUTH_HEADER_FILE"', self.installer)

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

    def test_rejects_replaced_final_file_component(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            target.write_text("unchanged\n", encoding="utf-8")
            replaced = root / "collector.json"
            replaced.symlink_to(target)
            result = self.run_path_rejection(replaced)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not contain symlink components", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged\n")

    def test_rejects_replaced_parent_directory_component(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            target.mkdir()
            replaced_parent = root / "runtime"
            replaced_parent.symlink_to(target, target_is_directory=True)
            result = self.run_path_rejection(replaced_parent / "collector.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not contain symlink components", result.stderr)
            self.assertEqual(list(target.iterdir()), [])

    def test_source_config_is_normalized_once_and_frozen(self) -> None:
        self.assertEqual(self.installer.count("config = load_agent_config("), 1)
        self.assertIn('SOURCE_CONFIG="$TMP_DIR/source-node-config.json"', self.installer)
        self.assertIn("os.O_RDONLY | os.O_NOFOLLOW", self.installer)
        self.assertIn('"$SOURCE_CONFIG" "$FROZEN_CONFIG"', self.installer)
        self.assertIn('FROZEN_CONFIG="$TMP_DIR/node-config.json"', self.installer)
        self.assertNotIn("agent_value()", self.installer)
        self.assertIn('--config "$FROZEN_CONFIG" --token-file "$TOKEN_FILE"', self.installer)
        self.assertIn('--config "$FROZEN_CONFIG" --output "$COLLECTOR_CONFIG"', self.installer)
        self.assertIn('--config "$FROZEN_CONFIG"', self.installer)
        self.assertIn('chmod 0444 "$FROZEN_CONFIG"', self.installer)
        self.assertNotIn('chown "$NODE_USER" "$FROZEN_CONFIG"', self.installer)
        self.assertIn("verify_frozen_config", self.installer)
        self.assertIn("normalized node config changed during installation", self.installer)

    def test_v2_identity_and_paths_are_resolved_locally_before_freeze(self) -> None:
        for required in [
            "--node-user",
            "--node-exporter-textfile-dir",
            'dscl . -read "/Users/$NODE_USER" NFSHomeDirectory',
            'NODE_HOME="$(cd "$SYSTEM_HOME" && pwd -P)"',
            'arm64) ARCHITECTURE="arm64"; PLATFORM="darwin_arm64"',
            'x86_64) ARCHITECTURE="x86_64"; PLATFORM="darwin_amd64"',
            'node_user=sys.argv[3]',
            'node_home=sys.argv[4]',
            'architecture=sys.argv[5]',
            'homebrew_prefix=sys.argv[6]',
            'node_exporter_textfile_dir=sys.argv[7] or None',
        ]:
            self.assertIn(required, self.installer)
        freeze_index = self.installer.index('FROZEN_CONFIG="$TMP_DIR/node-config.json"')
        download_index = self.installer.index("[fleet-node-agent] downloading pinned Collector")
        runtime_index = self.installer.index('RUNTIME_DIR="$NODE_HOME/.openclaw/fleet-node-observability"')
        self.assertLess(freeze_index, download_index)
        self.assertLess(freeze_index, runtime_index)

    def test_node_user_is_explicit_for_v2_and_legacy_installs(self) -> None:
        self.assertIn(
            '--config, --node-user, and --ingest-token-file are required',
            self.installer,
        )
        self.assertNotIn("CONFIG_NODE_USER", self.installer)
        required_check = self.installer.index(
            'if [[ -z "$CONFIG_PATH" || -z "$NODE_USER" || -z "$TOKEN_FILE" ]]'
        )
        source_snapshot = self.installer.index('SOURCE_CONFIG="$TMP_DIR/source-node-config.json"')
        self.assertLess(required_check, source_snapshot)

    def test_one_selected_homebrew_owns_node_exporter_and_textfile_default(self) -> None:
        for required in [
            "select_homebrew()",
            'HOMEBREW_PREFIX="$(select_homebrew)"',
            'BREW_BIN="$HOMEBREW_PREFIX/bin/brew"',
            '"$BREW_BIN" --prefix',
            'local candidate="$HOMEBREW_PREFIX/bin/node_exporter"',
            'sudo -u "$NODE_USER" "$BREW_BIN" install node_exporter',
        ]:
            self.assertIn(required, self.installer)

    @unittest.skipIf(os.geteuid() == 0, "requires a genuinely unprivileged test UID")
    def test_root_owned_readonly_snapshot_permission_model(self) -> None:
        # /private/etc/hosts has the same relevant ownership/mode/parent model as the
        # install snapshot: root-owned, world-readable, and in a root-controlled directory.
        snapshot_model = Path("/private/etc/hosts")
        info = snapshot_model.stat()
        self.assertEqual(info.st_uid, 0)
        self.assertNotEqual(info.st_uid, os.geteuid())
        self.assertTrue(snapshot_model.read_bytes())
        with self.assertRaises(PermissionError):
            snapshot_model.chmod(0o600)
        with self.assertRaises(PermissionError):
            os.open(snapshot_model, os.O_WRONLY)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp_dir:
            replacement = Path(temp_dir) / "replacement"
            replacement.write_text("replacement\n", encoding="utf-8")
            with self.assertRaises(PermissionError):
                os.replace(replacement, "/private/etc/.fleet-node-config-replacement-test")
            self.assertTrue(replacement.exists())

    def test_frozen_config_digest_check_rejects_changes(self) -> None:
        function_start = self.installer.index("verify_frozen_config() {")
        function_end = self.installer.index("\n}\n", function_start) + len("\n}\n")
        function = self.installer[function_start:function_end]
        with tempfile.TemporaryDirectory() as temp_dir:
            frozen = Path(temp_dir) / "node-config.json"
            frozen.write_text('{"node_label":"mini_03"}\n', encoding="utf-8")
            digest = subprocess.run(
                ["shasum", "-a", "256", str(frozen)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()[0]
            harness = (
                "set -euo pipefail\n"
                + f'FROZEN_CONFIG={str(frozen)!r}\n'
                + f'FROZEN_CONFIG_SHA256={digest!r}\n'
                + function
                + "verify_frozen_config\n"
                + f"printf '%s\\n' changed >{str(frozen)!r}\n"
                + "verify_frozen_config\n"
            )
            result = subprocess.run(
                ["bash", "-c", harness], capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("normalized node config changed during installation", result.stderr)

    def test_node_owned_paths_are_never_mutated_with_root_authority(self) -> None:
        self.assertIn('if [[ "$NODE_UID" -eq 0 ]]', self.installer)
        self.assertIn("node_user must be an unprivileged local account", self.installer)
        runtime_start = self.installer.index('RUNTIME_DIR="$NODE_HOME/.openclaw/fleet-node-observability"')
        runtime_end = self.installer.index("escape_xml()", runtime_start)
        runtime_install = self.installer[runtime_start:runtime_end]
        for required in [
            'run_as_node mkdir -p "$RUNTIME_BIN"',
            'run_as_node chmod 0700 "$RUNTIME_DIR"',
            'run_as_node install -m 0755 "$STAGED_COLLECTOR_BIN" "$COLLECTOR_BIN"',
            "run_as_node env PYTHONPATH=",
        ]:
            self.assertIn(required, runtime_install)
        for prohibited in [
            'chown -R "$NODE_USER" "$RUNTIME_DIR"',
            'chown "$NODE_USER" "$TEXTFILE_DIR"',
            'install -m 0755 "$EXTRACTED_BIN" "$COLLECTOR_BIN"',
            'PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" -m fleet_node_observability.commands.write_agent_secret',
        ]:
            self.assertNotIn(prohibited, runtime_install)
        self.assertIn(
            'stage_user_plist "$USER_NODE_EXPORTER_PLIST" '
            '"$TMP_DIR/user-node-exporter-source.plist"',
            self.installer,
        )
        self.assertIn(
            'preserve_rollback_backup_once "$TMP_DIR/user-node-exporter-source.plist" '
            '\\\n      "$USER_NODE_EXPORTER_BACKUP"',
            self.installer,
        )
        for required in ["os.O_NOFOLLOW", "os.fstat", "stat.S_ISREG"]:
            self.assertIn(required, self.installer)
        self.assertNotIn(
            'install -m 0400 "$USER_NODE_EXPORTER_PLIST"', self.installer
        )
        self.assertNotIn(
            'run_as_node install -m 0644 "$source_plist" "$active_plist"',
            self.installer,
        )
        self.assertIn(
            'USER_NODE_EXPORTER_BACKUP="/Library/LaunchDaemons/', self.installer
        )
        self.assertIn('run_as_node rm -f "$USER_NODE_EXPORTER_PLIST"', self.installer)
        self.assertIn('run_as_node tail -n 40 "$RUNTIME_LOGS/collector.err.log"', self.installer)

    def test_agent_is_single_network_owner(self) -> None:
        self.assertIn("com.unblocklabs.fleet-node-agent", self.installer)
        self.assertIn("configure_openclaw_local_otel", self.installer)
        self.assertIn("fleet-node-agent-heartbeat", self.installer)
        self.assertNotIn("configure-openclaw-otel", self.installer)

    def test_cutover_retirement_is_explicit_and_push_only(self) -> None:
        self.assertIn("--retire-legacy-pull", self.installer)
        self.assertIn('"$TELEMETRY_MODE" != "push"', self.installer)
        self.assertIn("legacy rollback artifacts were not retired", self.installer)
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

    def test_large_node_exporter_response_is_fetched_once_before_checks(self) -> None:
        function_start = self.installer.index("verify_node_exporter_metrics() {")
        function_end = self.installer.index("\n}\n", function_start) + len("\n}\n")
        function = self.installer[function_start:function_end]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            response = root / "large-metrics-response"
            response.write_text(
                "node_cpu_seconds_total 1\n"
                "fleet_node_agent_heartbeat_timestamp_seconds 2\n"
                + "".join(f"unrelated_metric_{index} {index}\n" for index in range(20000)),
                encoding="utf-8",
            )
            call_count = root / "curl-call-count"
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
count=0
if [[ -f "$MOCK_CURL_CALL_COUNT" ]]; then count="$(<"$MOCK_CURL_CALL_COUNT")"; fi
printf '%s\n' "$((count + 1))" >"$MOCK_CURL_CALL_COUNT"
cp "$MOCK_CURL_RESPONSE" "$output"
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            harness = root / "verify.sh"
            harness.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\nTMP_DIR=\"$1\"\n" + function
                + "verify_node_exporter_metrics http://127.0.0.1:9100/metrics\n"
                + "mode=$(stat -f %Lp \"$TMP_DIR/node-exporter.metrics\" 2>/dev/null || "
                + "stat -c %a \"$TMP_DIR/node-exporter.metrics\")\n"
                + "[[ \"$mode\" == 600 ]]\n",
                encoding="utf-8",
            )
            harness.chmod(0o755)
            result = subprocess.run(
                [str(harness), str(root)],
                capture_output=True,
                text=True,
                check=False,
                env=os.environ
                | {
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "MOCK_CURL_RESPONSE": str(response),
                    "MOCK_CURL_CALL_COUNT": str(call_count),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(call_count.read_text(encoding="utf-8"), "1\n")
            self.assertGreater((root / "node-exporter.metrics").stat().st_size, 500_000)


if __name__ == "__main__":
    unittest.main()
