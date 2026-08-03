from __future__ import annotations

import unittest

from fleet_node_observability.commands.ensure_openclaw_diagnostics_otel import (
    diagnostics_plugin,
    matching_plugin_version,
)
from fleet_node_observability.config import ConfigError


class EnsureOpenClawDiagnosticsOtelTest(unittest.TestCase):
    def test_packaging_revision_uses_base_extension_release(self) -> None:
        self.assertEqual(matching_plugin_version("OpenClaw 2026.7.1-2 (abc)"), "2026.7.1")

    def test_named_prerelease_is_preserved(self) -> None:
        self.assertEqual(matching_plugin_version("2026.7.2-beta.7"), "2026.7.2-beta.7")

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "determine"):
            matching_plugin_version("OpenClaw development build")

    def test_plugin_inventory_selects_exact_id(self) -> None:
        plugin = {"id": "diagnostics-otel", "enabled": True, "status": "loaded"}
        self.assertEqual(
            diagnostics_plugin({"plugins": [{"id": "other"}, plugin]}),
            plugin,
        )
        self.assertIsNone(diagnostics_plugin({"plugins": []}))

    def test_malformed_or_duplicate_inventory_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "plugins list"):
            diagnostics_plugin({"plugins": {}})
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            diagnostics_plugin(
                {"plugins": [{"id": "diagnostics-otel"}, {"id": "diagnostics-otel"}]}
            )


if __name__ == "__main__":
    unittest.main()
