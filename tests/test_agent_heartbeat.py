from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "fleet_node_observability" / "collectors" / "fleet_node_agent_heartbeat.sh"


class AgentHeartbeatTest(unittest.TestCase):
    def run_heartbeat(self, textfile_dir: Path, state_dir: Path, *, env: dict[str, str] | None = None):
        return subprocess.run(
            [str(SCRIPT), str(textfile_dir), "mini_03", "127.0.0.1:9", str(state_dir)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_writes_timestamp_gauge_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = self.run_heartbeat(root, root / "state")
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (root / "fleet_node_agent_heartbeat.prom").read_text(encoding="utf-8")
            self.assertRegex(
                content,
                r'fleet_node_agent_heartbeat_timestamp_seconds\{node="mini_03"\} [0-9]+',
            )
            self.assertIn('fleet_node_agent_queue_metrics_available{node="mini_03"} 0', content)
            self.assertIn(
                'fleet_node_agent_queue_oldest_age_seconds{node="mini_03",signal="heartbeat"} 0',
                content,
            )
            self.assertEqual(list(Path(tmpdir).glob(".fleet_node_agent_heartbeat.*")), [])

    def test_tracks_oldest_nonempty_queue_age_and_resets_when_drained(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mock_bin = root / "bin"
            mock_bin.mkdir()
            metrics = root / "metrics"
            curl = mock_bin / "curl"
            curl.write_text('#!/bin/sh\ncat "$MOCK_METRICS_FILE"\n', encoding="utf-8")
            curl.chmod(0o755)
            env = os.environ | {
                "PATH": f"{mock_bin}:{os.environ['PATH']}",
                "MOCK_METRICS_FILE": str(metrics),
            }
            metrics.write_text(
                'otelcol_exporter_queue_size{data_type="logs",exporter="otlp_http/logs"} 128\n',
                encoding="utf-8",
            )
            state_dir = root / "state"
            first = self.run_heartbeat(root, state_dir, env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            state_file = state_dir / "queue-oldest-logs.timestamp"
            state_file.write_text(f"{int(state_file.read_text()) - 42}\n", encoding="utf-8")

            second = self.run_heartbeat(root, state_dir, env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            content = (root / "fleet_node_agent_heartbeat.prom").read_text(encoding="utf-8")
            self.assertIn('fleet_node_agent_queue_metrics_available{node="mini_03"} 1', content)
            self.assertRegex(
                content,
                r'fleet_node_agent_queue_oldest_age_seconds\{node="mini_03",signal="logs"\} 4[2-4]',
            )

            metrics.write_text(
                'otelcol_exporter_queue_size{data_type="logs",exporter="otlp_http/logs"} 0\n',
                encoding="utf-8",
            )
            drained = self.run_heartbeat(root, state_dir, env=env)
            self.assertEqual(drained.returncode, 0, drained.stderr)
            self.assertFalse(state_file.exists())

    def test_rejects_unnormalized_node_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [str(SCRIPT), tmpdir, "Mini-03", "127.0.0.1:9", str(Path(tmpdir) / "state")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("normalized", result.stderr)


if __name__ == "__main__":
    unittest.main()
