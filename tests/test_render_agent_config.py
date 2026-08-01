from __future__ import annotations

import json
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from fleet_node_observability.commands.render_agent_config import main


class RenderAgentConfigTest(unittest.TestCase):
    def test_output_is_mode_0600_and_no_temp_file_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            node_home = root / "node"
            config = root / "agent.json"
            output = node_home / ".openclaw" / "fleet-node-observability" / "config" / "collector.json"
            config.write_text(
                json.dumps(
                    {
                        "node_label": "mini_03",
                        "node_user": "fleet-mini-03",
                        "node_home": str(node_home),
                        "telemetry_mode": "pull",
                        "telemetry_endpoint": "https://telemetry.example.com",
                    }
                ),
                encoding="utf-8",
            )
            out = StringIO()
            err = StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(["--config", str(config), "--output", str(output)])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(list(output.parent.glob(".collector.json.*")), [])

    def test_refuses_symlink_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            node_home = root / "node"
            managed = node_home / ".openclaw" / "fleet-node-observability"
            output = managed / "config" / "collector.json"
            output.parent.mkdir(parents=True)
            target = root / "outside"
            target.write_text("preserve", encoding="utf-8")
            output.symlink_to(target)
            config = root / "agent.json"
            config.write_text(
                json.dumps(
                    {
                        "node_label": "mini_03",
                        "node_user": "fleet-mini-03",
                        "node_home": str(node_home),
                        "telemetry_mode": "pull",
                        "telemetry_endpoint": "https://telemetry.example.com",
                    }
                ),
                encoding="utf-8",
            )
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                rc = main(["--config", str(config), "--output", str(output)])
            self.assertEqual(rc, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
