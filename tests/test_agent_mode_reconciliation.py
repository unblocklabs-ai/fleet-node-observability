from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (
    ROOT / "src" / "fleet_node_observability" / "installers" / "fleet_node_agent.sh"
)


class AgentModeReconciliationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        cls.reconciler = source.split("# BEGIN MODE RECONCILIATION\n", 1)[1].split(
            "# END MODE RECONCILIATION", 1
        )[0]

    def make_harness(self, root: Path) -> Path:
        (root / "system").mkdir()
        (root / "user").mkdir()
        harness = root / "reconcile.sh"
        harness.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
TEST_ROOT="$1"
TELEMETRY_MODE="$2"
ACTION="${3:-reconcile}"
TMP_DIR="$TEST_ROOT/tmp"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NODE_UID="$(id -u)"
NODE_EXPORTER_LABEL="com.unblocklabs.node-exporter"
SYSTEM_NODE_EXPORTER_PLIST="$TEST_ROOT/system/$NODE_EXPORTER_LABEL.plist"
SYSTEM_NODE_EXPORTER_BACKUP="$SYSTEM_NODE_EXPORTER_PLIST.fleet-agent-rollback"
USER_NODE_EXPORTER_PLIST="$TEST_ROOT/user/$NODE_EXPORTER_LABEL.plist"
USER_NODE_EXPORTER_BACKUP="$TEST_ROOT/system/.user-node-exporter.fleet-agent-rollback"
ROLLBACK_OWNER_UID="$(id -u)"
LEGACY_PROXY_PLIST="$TEST_ROOT/system/com.unblocklabs.node-exporter-proxy.plist"
LEGACY_PROXY_RETIRED="$LEGACY_PROXY_PLIST.retired-by-fleet-agent"
LEGACY_RETIRE_MARKER="$TEST_ROOT/system/.legacy-pull-retired"
HEARTBEAT_PLIST="$TEST_ROOT/system/com.unblocklabs.fleet-node-agent-heartbeat.plist"
mkdir -p "$TMP_DIR" "$TEST_ROOT/system" "$TEST_ROOT/user"
run_as_node() { "$@"; }
launchctl() {
  printf '%s\n' "$*" >>"$TEST_ROOT/launchctl-events"
  if [[ -n "${FAIL_LAUNCHCTL_ONCE:-}" && "$*" == *"$FAIL_LAUNCHCTL_ONCE"* && \
    ! -e "$TEST_ROOT/launchctl-failure-injected" ]]; then
    : >"$TEST_ROOT/launchctl-failure-injected"
    return 1
  fi
}
"""
            + self.reconciler
            + """
case "$ACTION" in
  reconcile)
    reconcile_node_exporter_mode
    reconcile_heartbeat_mode
    ;;
  retire) retire_legacy_pull ;;
  *) exit 2 ;;
esac
""",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        (root / "tmp" / "node-exporter.plist").parent.mkdir(parents=True, exist_ok=True)
        (root / "tmp" / "node-exporter.plist").write_text(
            "<plist><dict><!-- fleet-node-agent-managed -->"
            "<string>--web.listen-address=127.0.0.1:9100</string></dict></plist>\n",
            encoding="utf-8",
        )
        (root / "tmp" / "heartbeat.plist").write_text("heartbeat\n", encoding="utf-8")
        return harness

    def run_mode(
        self,
        harness: Path,
        root: Path,
        mode: str,
        action: str = "reconcile",
        *,
        fail_launchctl_once: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if fail_launchctl_once is not None:
            env["FAIL_LAUNCHCTL_ONCE"] = fail_launchctl_once
        return subprocess.run(
            [str(harness), str(root), mode, action],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    @staticmethod
    def is_managed(path: Path) -> bool:
        return path.exists() and "fleet-node-agent-managed" in path.read_text(encoding="utf-8")

    def test_lan_transition_table_and_same_mode_installs_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            harness = self.make_harness(root)
            user = root / "user" / "com.unblocklabs.node-exporter.plist"
            user.write_text(
                "<plist><string>--web.listen-address=:9100</string></plist>\n",
                encoding="utf-8",
            )
            system = root / "system" / "com.unblocklabs.node-exporter.plist"
            heartbeat = root / "system" / "com.unblocklabs.fleet-node-agent-heartbeat.plist"

            for mode, expected_user, expected_managed, expected_heartbeat in [
                ("pull", True, False, False),
                ("pull", True, False, False),
                ("dual", True, False, True),
                ("dual", True, False, True),
                ("push", False, True, True),
                ("push", False, True, True),
                ("dual", True, False, True),
                ("pull", True, False, False),
                ("pull", True, False, False),
            ]:
                with self.subTest(mode=mode):
                    result = self.run_mode(harness, root, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(user.exists(), expected_user)
                    self.assertEqual(self.is_managed(system), expected_managed)
                    self.assertEqual(heartbeat.exists(), expected_heartbeat)

            backup = root / "system" / ".user-node-exporter.fleet-agent-rollback"
            self.assertTrue(backup.exists())
            self.assertEqual(user.read_text(encoding="utf-8"), backup.read_text(encoding="utf-8"))

    def test_restored_active_mutation_does_not_replace_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            harness = self.make_harness(root)
            user = root / "user" / "com.unblocklabs.node-exporter.plist"
            backup = root / "system" / ".user-node-exporter.fleet-agent-rollback"
            original = "<plist><string>ORIGINAL --web.listen-address=:9100</string></plist>\n"
            user.write_text(original, encoding="utf-8")
            for mode in ["push", "dual"]:
                result = self.run_mode(harness, root, mode)
                self.assertEqual(result.returncode, 0, result.stderr)
            user.write_text(
                "<plist><string>MUTATED --web.listen-address=:9100</string></plist>\n",
                encoding="utf-8",
            )
            pushed = self.run_mode(harness, root, "push")
            self.assertEqual(pushed.returncode, 0, pushed.stderr)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            restored = self.run_mode(harness, root, "dual")
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual(user.read_text(encoding="utf-8"), original)

    def test_launchctl_failure_then_retry_converges_to_original_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            harness = self.make_harness(root)
            user = root / "user" / "com.unblocklabs.node-exporter.plist"
            original = "<plist><string>ORIGINAL --web.listen-address=:9100</string></plist>\n"
            user.write_text(original, encoding="utf-8")
            failed = self.run_mode(
                harness,
                root,
                "push",
                fail_launchctl_once="bootstrap system",
            )
            self.assertNotEqual(failed.returncode, 0)
            retried = self.run_mode(harness, root, "push")
            self.assertEqual(retried.returncode, 0, retried.stderr)
            rolled_back = self.run_mode(harness, root, "dual")
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertEqual(user.read_text(encoding="utf-8"), original)

    def test_unsafe_existing_rollback_paths_fail_closed(self) -> None:
        for unsafe_kind in ["symlink", "directory", "writable"]:
            with self.subTest(unsafe_kind=unsafe_kind), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                harness = self.make_harness(root)
                user = root / "user" / "com.unblocklabs.node-exporter.plist"
                user.write_text(
                    "<plist><string>--web.listen-address=:9100</string></plist>\n",
                    encoding="utf-8",
                )
                backup = root / "system" / ".user-node-exporter.fleet-agent-rollback"
                if unsafe_kind == "symlink":
                    target = root / "unsafe-target"
                    target.write_text("unsafe\n", encoding="utf-8")
                    backup.symlink_to(target)
                elif unsafe_kind == "directory":
                    backup.mkdir()
                else:
                    backup.write_text("unsafe\n", encoding="utf-8")
                    backup.chmod(0o622)
                result = self.run_mode(harness, root, "push")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("legacy rollback backup", result.stderr)

    def test_user_plist_symlink_cannot_be_backed_up_or_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            harness = self.make_harness(root)
            outside = root / "outside-root-only-equivalent.plist"
            secret = (
                "<plist><string>ROOT_ONLY_SECRET "
                "--web.listen-address=:9100</string></plist>\n"
            )
            outside.write_text(secret, encoding="utf-8")
            outside.chmod(0o000)
            user = root / "user" / "com.unblocklabs.node-exporter.plist"
            user.symlink_to(outside)
            backup = root / "system" / ".user-node-exporter.fleet-agent-rollback"
            staged_source = root / "tmp" / "user-node-exporter-source.plist"
            staged_active = root / "tmp" / "user-node-exporter-active.plist"
            managed = root / "system" / "com.unblocklabs.node-exporter.plist"

            try:
                pushed = self.run_mode(harness, root, "push")
                self.assertNotEqual(pushed.returncode, 0)
                self.assertIn("without following links", pushed.stderr)
                self.assertFalse(backup.exists())
                self.assertFalse(staged_source.exists())
                self.assertFalse(managed.exists())

                restored = self.run_mode(harness, root, "dual")
                self.assertNotEqual(restored.returncode, 0)
                self.assertIn("without following links", restored.stderr)
                self.assertFalse(backup.exists())
                self.assertFalse(staged_active.exists())
                self.assertTrue(user.is_symlink())
                self.assertFalse(managed.exists())
            finally:
                outside.chmod(0o600)

            self.assertEqual(outside.read_text(encoding="utf-8"), secret)

    def test_loopback_legacy_path_requires_and_reactivates_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            harness = self.make_harness(root)
            system = root / "system" / "com.unblocklabs.node-exporter.plist"
            original = (
                "<plist><string>--web.listen-address=127.0.0.1:9100</string></plist>\n"
            )
            system.write_text(original, encoding="utf-8")
            missing = self.run_mode(harness, root, "dual")
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("requires its preserved scrape proxy", missing.stderr)

            proxy = root / "system" / "com.unblocklabs.node-exporter-proxy.plist"
            proxy.write_text("proxy\n", encoding="utf-8")
            for mode in ["dual", "push", "push", "dual"]:
                result = self.run_mode(harness, root, mode)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(system.read_text(encoding="utf-8"), original)
            self.assertTrue(proxy.exists())
            events = (root / "launchctl-events").read_text(encoding="utf-8")
            self.assertIn("system/com.unblocklabs.node-exporter-proxy", events)

    def test_explicit_retirement_is_idempotent_and_blocks_automatic_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            harness = self.make_harness(root)
            user = root / "user" / "com.unblocklabs.node-exporter.plist"
            user.write_text(
                "<plist><string>--web.listen-address=:9100</string></plist>\n",
                encoding="utf-8",
            )
            proxy = root / "system" / "com.unblocklabs.node-exporter-proxy.plist"
            proxy.write_text("proxy\n", encoding="utf-8")
            self.assertEqual(self.run_mode(harness, root, "push").returncode, 0)
            first = self.run_mode(harness, root, "push", "retire")
            second = self.run_mode(harness, root, "push", "retire")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already explicitly retired", second.stdout)
            self.assertFalse(proxy.exists())
            self.assertTrue(Path(f"{proxy}.retired-by-fleet-agent").exists())
            rollback = self.run_mode(harness, root, "dual")
            self.assertNotEqual(rollback.returncode, 0)
            self.assertIn("legacy pull was explicitly retired", rollback.stderr)

    def test_pull_and_dual_fail_closed_without_a_legacy_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            harness = self.make_harness(root)
            for mode in ["pull", "dual"]:
                with self.subTest(mode=mode):
                    result = self.run_mode(harness, root, mode)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("no preserved legacy node_exporter", result.stderr)


if __name__ == "__main__":
    unittest.main()
