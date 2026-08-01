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
    def run_command(
        self,
        token_mode: int = 0o600,
        token_contents: bytes = b"secret-token\n",
    ) -> tuple[int, str, str, Path | None, tempfile.TemporaryDirectory]:
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
        token_path.write_bytes(token_contents)
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
        rc, out, err, secret_path, temp = self.run_command(0o640)
        self.addCleanup(temp.cleanup)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("group or other", err)
        assert secret_path is not None
        self.assertFalse(secret_path.exists())

    def test_accepts_exact_token_file_grammar(self) -> None:
        for name, token_contents in [
            ("no newline", b"secret-token"),
            ("one final LF", b"secret-token\n"),
        ]:
            with self.subTest(name=name):
                rc, _, err, secret_path, temp = self.run_command(token_contents=token_contents)
                self.addCleanup(temp.cleanup)
                self.assertEqual(rc, 0, err)
                assert secret_path is not None
                decoded = base64.b64decode(
                    secret_path.read_text(encoding="utf-8").removeprefix("Basic ")
                ).decode("utf-8")
                self.assertEqual(decoded, "mini_03:secret-token")

    def test_rejects_invalid_token_file_grammar_without_writing_secret(self) -> None:
        for name, token_contents in [
            ("CRLF", b"secret-token\r\n"),
            ("multiple trailing blank lines", b"secret-token\n\n"),
            ("embedded newline", b"secret\ntoken"),
            ("empty", b""),
            ("leading space", b" secret-token"),
            ("trailing space", b"secret-token "),
            ("leading tab", b"\tsecret-token"),
            ("trailing tab", b"secret-token\t"),
        ]:
            with self.subTest(name=name):
                rc, out, err, secret_path, temp = self.run_command(token_contents=token_contents)
                self.addCleanup(temp.cleanup)
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn("must contain one nonempty token", err)
                self.assertNotIn("secret-token", err)
                assert secret_path is not None
                self.assertFalse(secret_path.exists())


if __name__ == "__main__":
    unittest.main()
