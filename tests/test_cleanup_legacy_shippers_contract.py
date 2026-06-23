from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class CleanupLegacyShippersContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "src/fleet_node_observability/installers/cleanup_legacy_shippers.sh").read_text(
            encoding="utf-8"
        )
        cls.wrapper = (ROOT / "bin/cleanup-legacy-shippers").read_text(encoding="utf-8")

    def test_cleanup_command_preserves_dry_run_by_default(self) -> None:
        self.assertIn("APPLY=0", self.script)
        self.assertIn("DRY-RUN", self.script)
        self.assertIn('echo "mode=$([[ "$APPLY" -eq 1 ]] && echo apply || echo dry-run)"', self.script)

    def test_cleanup_command_allows_apply_override(self) -> None:
        self.assertIn("--apply", self.script)
        self.assertIn('if [[ "$APPLY" -eq 1 ]]', self.script)

    def test_cleanup_checks_known_labels_and_process_patterns(self) -> None:
        for label in [
            "com.unblocklabs.vector.openclaw",
            "dev.vector.agent",
            "ai.unblocklabs.promtail",
            "ai.openclaw.loki-log-shipper",
        ]:
            self.assertIn(label, self.script)

        for pattern in [
            "vector --config-yaml",
            "promtail",
            "loki-log-shipper",
        ]:
            self.assertIn(pattern, self.script)

    def test_cleanup_renames_launchagents_for_mutation(self) -> None:
        self.assertIn(".disabled-$timestamp", self.script)
        self.assertIn("mv \"$plist\" \"$disabled\"", self.script)

    def test_cleanup_wrapper_executes_impl(self) -> None:
        self.assertIn("src/fleet_node_observability/installers/cleanup_legacy_shippers.sh", self.wrapper)

    def test_cleanup_has_no_fleet_inventory_dependency(self) -> None:
        self.assertNotIn("fleet/nodes.json", self.script)
