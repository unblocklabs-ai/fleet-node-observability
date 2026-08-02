from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fleet_node_observability.agent import LocalNodeContext
from fleet_node_observability.commands.configure_openclaw_local_otel import (
    main as configure_main,
)
from fleet_node_observability.commands.render_agent_config import main as render_main
from fleet_node_observability.commands.write_agent_secret import main as secret_main


class V2HelperCommandTest(unittest.TestCase):
    def test_all_shipped_helpers_accept_canonical_v2_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            node_home = root / "node-home"
            node_home.mkdir()
            config_path = root / "node.json"
            config_path.write_text(
                json.dumps(
                    {
                        "config_schema_version": 2,
                        "node_label": "mini-03",
                        "telemetry_mode": "push",
                        "telemetry_endpoint": "https://telemetry.example.com",
                    }
                ),
                encoding="utf-8",
            )
            token_path = root / "token"
            token_path.write_text("secret\n", encoding="utf-8")
            token_path.chmod(0o600)
            rendered_path = root / "collector.json"
            local = LocalNodeContext(
                node_user="local-node",
                node_home=node_home,
                architecture="arm64",
                homebrew_prefix=Path("/opt/homebrew"),
            )
            with patch(
                "fleet_node_observability.agent.resolve_current_node_context",
                return_value=local,
            ):
                self.assertEqual(
                    render_main(
                        ["--config", str(config_path), "--output", str(rendered_path)]
                    ),
                    0,
                )
                self.assertEqual(
                    secret_main(
                        ["--config", str(config_path), "--token-file", str(token_path)]
                    ),
                    0,
                )
                self.assertEqual(
                    configure_main(
                        ["--config", str(config_path), "--no-backup"]
                    ),
                    0,
                )

            runtime = node_home / ".openclaw" / "fleet-node-observability"
            self.assertTrue(rendered_path.is_file())
            self.assertTrue((runtime / "secrets" / "authorization-header").is_file())
            self.assertTrue((node_home / ".openclaw" / "openclaw.json").is_file())

    def test_all_shipped_helpers_refuse_implicit_root_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "node.json"
            config_path.write_text(
                json.dumps(
                    {
                        "config_schema_version": 2,
                        "node_label": "mini-03",
                        "telemetry_mode": "push",
                        "telemetry_endpoint": "https://telemetry.example.com",
                    }
                ),
                encoding="utf-8",
            )
            token_path = root / "token"
            token_path.write_text("secret\n", encoding="utf-8")
            token_path.chmod(0o600)
            with patch(
                "fleet_node_observability.agent.os.geteuid", return_value=0
            ):
                self.assertEqual(
                    render_main(
                        [
                            "--config",
                            str(config_path),
                            "--output",
                            str(root / "collector.json"),
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    secret_main(
                        ["--config", str(config_path), "--token-file", str(token_path)]
                    ),
                    1,
                )
                self.assertEqual(
                    configure_main(
                        ["--config", str(config_path), "--no-backup"]
                    ),
                    1,
                )
            self.assertFalse((root / "collector.json").exists())


if __name__ == "__main__":
    unittest.main()
