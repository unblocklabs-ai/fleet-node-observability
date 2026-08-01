from __future__ import annotations

import base64
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from fleet_node_observability.commands.write_agent_secret import main


class AgentSecretTest(unittest.TestCase):
    def run_command(self, token_mode: int = 0o600) -> tuple[int, str, str, Path | None, tempfile.TemporaryDirectory]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        node_home = root / "node"
        secret_path = node_home / ".openclaw" / "fleet-node-observability" / "secrets" / "authorization-header"
        config_path = root / "agent.json"
        config_path.write_text(
            json.dumps(
                {
                    "node_label": "Mini-03",
                    "node_user": "fleet-mini-03",
                    "node_home": str(node_home),
                    "telemetry_mode": "push",
                    "telemetry_endpoint": "https://telemetry.example.com",
                    "authorization_header_path": str(secret_path),
                }
            ),
            encoding="utf-8",
        )
        token_path = root / "token"
        token_path.write_text("secret-token\n", encoding="utf-8")
        token_path.chmod(token_mode)
        out = StringIO()
        err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--config", str(config_path), "--token-file", str(token_path)])
        return rc, out.getvalue(), err.getvalue(), secret_path, temp

    def test_writes_only_full_basic_header_with_mode_0600(self) -> None:
        rc, _, err, secret_path, temp = self.run_command()
        self.addCleanup(temp.cleanup)
        self.assertEqual(rc, 0, err)
        assert secret_path is not None
        content = secret_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("Basic "))
        decoded = base64.b64decode(content.removeprefix("Basic ")).decode("utf-8")
        self.assertEqual(decoded, "mini_03:secret-token")
        self.assertEqual(stat.S_IMODE(secret_path.stat().st_mode), 0o600)

    def test_rejects_group_readable_token_source(self) -> None:
        rc, out, err, _, temp = self.run_command(0o640)
        self.addCleanup(temp.cleanup)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("group or other", err)


if __name__ == "__main__":
    unittest.main()
