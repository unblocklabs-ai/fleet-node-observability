from __future__ import annotations

import os
import shutil
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
                "# HELP fleet_node_agent_queue_metrics_available Whether all six expected local "
                "Collector queue exporter samples are present and valid.",
                content,
            )
            self.assertIn(
                "# HELP fleet_node_agent_queue_oldest_age_seconds Seconds since a signal queue was "
                "first observed non-empty without a subsequently observed valid zero.",
                content,
            )
            self.assertIn(
                'fleet_node_agent_queue_oldest_age_seconds{node="mini_03",signal="heartbeat"} 0',
                content,
            )
            self.assertEqual(list(Path(tmpdir).glob(".fleet_node_agent_heartbeat.*")), [])

    def test_tracks_all_queue_ages_sums_duplicate_series_and_resets_when_drained(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mock_bin = root / "bin"
            mock_bin.mkdir()
            metrics = root / "metrics"
            curl = mock_bin / "curl"
            curl.write_text('#!/bin/sh\ncat "$MOCK_METRICS_FILE"\n', encoding="utf-8")
            curl.chmod(0o755)
            real_awk = shutil.which("awk")
            self.assertIsNotNone(real_awk)
            awk_capture = root / "awk-output"
            awk = mock_bin / "awk"
            awk.write_text(
                '#!/bin/sh\n"$REAL_AWK" "$@" | tee -a "$AWK_CAPTURE"\n',
                encoding="utf-8",
            )
            awk.chmod(0o755)
            env = os.environ | {
                "PATH": f"{mock_bin}:{os.environ['PATH']}",
                "MOCK_METRICS_FILE": str(metrics),
                "REAL_AWK": str(real_awk),
                "AWK_CAPTURE": str(awk_capture),
            }
            metrics.write_text(
                'otelcol_exporter_queue_size{data_type="logs",exporter="otlphttp/logs",partition="a"} 80\n'
                'otelcol_exporter_queue_size{data_type="logs",exporter="otlphttp/logs",partition="b"} 48\n'
                'otelcol_exporter_queue_size{data_type="traces",exporter="otlphttp/traces"} 6.4e1\n'
                'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/app_metrics"} 32\n'
                'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/agent"} 16\n'
                'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/host"} 8\n'
                'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/heartbeat"} 4\n',
                encoding="utf-8",
            )
            state_dir = root / "state"
            first = self.run_heartbeat(root, state_dir, env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("logs\t128", awk_capture.read_text(encoding="utf-8").splitlines())
            signals = (
                "logs",
                "traces",
                "openclaw_metrics",
                "agent_metrics",
                "host_metrics",
                "heartbeat",
            )
            state_files = [state_dir / f"queue-oldest-{signal}.timestamp" for signal in signals]
            for state_file in state_files:
                state_file.write_text(
                    f"{int(state_file.read_text()) - 42}\n", encoding="utf-8"
                )

            second = self.run_heartbeat(root, state_dir, env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            content = (root / "fleet_node_agent_heartbeat.prom").read_text(encoding="utf-8")
            self.assertIn('fleet_node_agent_queue_metrics_available{node="mini_03"} 1', content)
            for signal in signals:
                self.assertRegex(
                    content,
                    rf'fleet_node_agent_queue_oldest_age_seconds\{{node="mini_03",signal="{signal}"\}} 4[2-4]',
                )

            metrics.write_text(
                '\n'.join(
                    [
                        'otelcol_exporter_queue_size{data_type="logs",exporter="otlphttp/logs"} 0',
                        'otelcol_exporter_queue_size{data_type="traces",exporter="otlphttp/traces"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/app_metrics"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/agent"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/host"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/heartbeat"} 0',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            drained = self.run_heartbeat(root, state_dir, env=env)
            self.assertEqual(drained.returncode, 0, drained.stderr)
            drained_content = (root / "fleet_node_agent_heartbeat.prom").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'fleet_node_agent_queue_metrics_available{node="mini_03"} 1',
                drained_content,
            )
            self.assertTrue(all(not state_file.exists() for state_file in state_files))

    def test_empty_metrics_marks_unavailable_and_preserves_queue_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mock_bin = root / "bin"
            mock_bin.mkdir()
            metrics = root / "metrics"
            metrics.write_text("", encoding="utf-8")
            curl = mock_bin / "curl"
            curl.write_text('#!/bin/sh\ncat "$MOCK_METRICS_FILE"\n', encoding="utf-8")
            curl.chmod(0o755)
            env = os.environ | {
                "PATH": f"{mock_bin}:{os.environ['PATH']}",
                "MOCK_METRICS_FILE": str(metrics),
            }
            state_dir = root / "state"
            state_dir.mkdir()
            heartbeat_state = state_dir / "queue-oldest-heartbeat.timestamp"
            heartbeat_state.write_text("1\n", encoding="utf-8")

            result = self.run_heartbeat(root, state_dir, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            content = (root / "fleet_node_agent_heartbeat.prom").read_text(encoding="utf-8")
            self.assertIn(
                'fleet_node_agent_queue_metrics_available{node="mini_03"} 0', content
            )
            self.assertEqual(heartbeat_state.read_text(encoding="utf-8"), "1\n")

    def test_missing_exporter_marks_unavailable_and_only_resets_observed_zeroes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mock_bin = root / "bin"
            mock_bin.mkdir()
            metrics = root / "metrics"
            metrics.write_text(
                '\n'.join(
                    [
                        'otelcol_exporter_queue_size{data_type="logs",exporter="otlphttp/logs"} 0',
                        'otelcol_exporter_queue_size{data_type="traces",exporter="otlphttp/traces"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/app_metrics"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/agent"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/host"} 0',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            curl = mock_bin / "curl"
            curl.write_text('#!/bin/sh\ncat "$MOCK_METRICS_FILE"\n', encoding="utf-8")
            curl.chmod(0o755)
            env = os.environ | {
                "PATH": f"{mock_bin}:{os.environ['PATH']}",
                "MOCK_METRICS_FILE": str(metrics),
            }
            state_dir = root / "state"
            state_dir.mkdir()
            observed_signals = (
                "logs",
                "traces",
                "openclaw_metrics",
                "agent_metrics",
                "host_metrics",
            )
            for signal in (*observed_signals, "heartbeat"):
                (state_dir / f"queue-oldest-{signal}.timestamp").write_text(
                    "1\n", encoding="utf-8"
                )

            result = self.run_heartbeat(root, state_dir, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            content = (root / "fleet_node_agent_heartbeat.prom").read_text(encoding="utf-8")
            self.assertIn(
                'fleet_node_agent_queue_metrics_available{node="mini_03"} 0', content
            )
            self.assertTrue(
                all(
                    not (state_dir / f"queue-oldest-{signal}.timestamp").exists()
                    for signal in observed_signals
                )
            )
            self.assertTrue((state_dir / "queue-oldest-heartbeat.timestamp").exists())

    def test_invalid_duplicate_marks_exporter_unusable_and_preserves_its_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mock_bin = root / "bin"
            mock_bin.mkdir()
            metrics = root / "metrics"
            metrics.write_text(
                '\n'.join(
                    [
                        'otelcol_exporter_queue_size{data_type="logs",exporter="otlphttp/logs",sample="valid"} 0',
                        'otelcol_exporter_queue_size{data_type="logs",exporter="otlphttp/logs",sample="invalid"} NaN',
                        'otelcol_exporter_queue_size{data_type="traces",exporter="otlphttp/traces"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/app_metrics"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/agent"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/host"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/heartbeat"} 0',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            curl = mock_bin / "curl"
            curl.write_text('#!/bin/sh\ncat "$MOCK_METRICS_FILE"\n', encoding="utf-8")
            curl.chmod(0o755)
            env = os.environ | {
                "PATH": f"{mock_bin}:{os.environ['PATH']}",
                "MOCK_METRICS_FILE": str(metrics),
            }
            state_dir = root / "state"
            state_dir.mkdir()
            signals = (
                "logs",
                "traces",
                "openclaw_metrics",
                "agent_metrics",
                "host_metrics",
                "heartbeat",
            )
            for signal in signals:
                (state_dir / f"queue-oldest-{signal}.timestamp").write_text(
                    "1\n", encoding="utf-8"
                )

            result = self.run_heartbeat(root, state_dir, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            content = (root / "fleet_node_agent_heartbeat.prom").read_text(encoding="utf-8")
            self.assertIn(
                'fleet_node_agent_queue_metrics_available{node="mini_03"} 0', content
            )
            self.assertTrue((state_dir / "queue-oldest-logs.timestamp").exists())
            self.assertTrue(
                all(
                    not (state_dir / f"queue-oldest-{signal}.timestamp").exists()
                    for signal in signals[1:]
                )
            )

            metrics.write_text(
                '\n'.join(
                    [
                        'otelcol_exporter_queue_size{data_type="logs",exporter="otlphttp/logs"} 0.4',
                        'otelcol_exporter_queue_size{data_type="traces",exporter="otlphttp/traces"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/app_metrics"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/agent"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/host"} 0',
                        'otelcol_exporter_queue_size{data_type="metrics",exporter="otlphttp/heartbeat"} 0',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fractional = self.run_heartbeat(root, state_dir, env=env)
            self.assertEqual(fractional.returncode, 0, fractional.stderr)
            fractional_content = (root / "fleet_node_agent_heartbeat.prom").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'fleet_node_agent_queue_metrics_available{node="mini_03"} 0',
                fractional_content,
            )
            self.assertTrue((state_dir / "queue-oldest-logs.timestamp").exists())

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
