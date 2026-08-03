from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

from fleet_node_observability.agent import (
    COLLECTOR_VERSION,
    HEARTBEAT_METRIC,
    AgentConfig,
    render_collector_config,
)


RUN_INTEGRATION = os.environ.get("FLEET_RUN_COLLECTOR_INTEGRATION") == "1"
COLLECTOR_IMAGE = (
    "otel/opentelemetry-collector-contrib@"
    "sha256:f2f01157055a9b2aab9df7118e1f1c9abf345e99b23bc7a2bc791db374a7d0f6"
)


class MetricsHandler(BaseHTTPRequestHandler):
    exposition = (
        "# TYPE node_cpu_seconds_total counter\n"
        'node_cpu_seconds_total{mode="idle"} 123\n'
        f"# TYPE {HEARTBEAT_METRIC} gauge\n"
        f'{HEARTBEAT_METRIC}{{node="mini_03"}} 1770000000\n'
    ).encode()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/metrics":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(self.exposition)))
        self.end_headers()
        self.wfile.write(self.exposition)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_command(args: list[str], *, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


@contextmanager
def cleanup_after_launch_attempt(docker: str, container_name: str) -> Iterator[None]:
    def remove_and_verify() -> None:
        removed: subprocess.CompletedProcess[str] | None = None
        removal_error: Exception | None = None
        try:
            removed = run_command(
                [docker, "container", "rm", "--force", container_name]
            )
        except Exception as error:
            removal_error = error

        try:
            residue = run_command([docker, "container", "inspect", container_name])
        except Exception as inspect_error:
            raise AssertionError(
                "could not verify integration container cleanup: "
                f"removal_error={removal_error!r} inspect_error={inspect_error!r}"
            ) from inspect_error

        absence_verified = (
            residue.returncode != 0
            and f"No such container: {container_name}" in residue.stderr
        )
        if not absence_verified:
            raise AssertionError(
                "could not verify integration container cleanup: "
                f"remove_stdout={None if removed is None else removed.stdout!r} "
                f"remove_stderr={None if removed is None else removed.stderr!r} "
                f"removal_error={removal_error!r} "
                f"inspect_stdout={residue.stdout!r} "
                f"inspect_stderr={residue.stderr!r}"
            )

    try:
        yield
    except BaseException as original_error:
        try:
            remove_and_verify()
        except Exception as cleanup_error:
            raise cleanup_error from original_error
        raise
    else:
        remove_and_verify()


def read_documents(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    documents: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        complete = line.endswith(("\n", "\r"))
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not complete:
                continue
            raise
        if isinstance(payload, dict):
            documents.append(payload)
    return documents


def attribute_value(attributes: list[dict[str, Any]], key: str) -> Any:
    for attribute in attributes:
        if attribute.get("key") != key:
            continue
        value = attribute.get("value", {})
        for value_key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if value_key in value:
                return value[value_key]
    return None


def metric_names_and_sources(path: Path) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    sources: set[str] = set()
    for document in read_documents(path):
        for resource_metrics in document.get("resourceMetrics", []):
            resource = resource_metrics.get("resource", {})
            source = attribute_value(resource.get("attributes", []), "fleet.signal.source")
            if source is not None:
                sources.add(str(source))
            for scope_metrics in resource_metrics.get("scopeMetrics", []):
                for metric in scope_metrics.get("metrics", []):
                    name = metric.get("name")
                    if isinstance(name, str):
                        names.add(name)
    return names, sources


def log_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for document in read_documents(path):
        for resource_logs in document.get("resourceLogs", []):
            for scope_logs in resource_logs.get("scopeLogs", []):
                records.extend(scope_logs.get("logRecords", []))
    return records


class ContainerCleanupTest(unittest.TestCase):
    @mock.patch(f"{__name__}.run_command")
    def test_removal_timeout_with_verified_absence_preserves_launch_error(
        self, mocked_run: mock.Mock
    ) -> None:
        container_name = "fleet-node-collector-test-timeout-absent"
        mocked_run.side_effect = [
            subprocess.TimeoutExpired(["docker", "container", "rm"], 20),
            subprocess.CompletedProcess(
                ["docker", "container", "inspect", container_name],
                1,
                stdout="[]\n",
                stderr=f"Error response from daemon: No such container: {container_name}\n",
            ),
        ]
        launch_error = RuntimeError("launch timed out")

        with self.assertRaises(RuntimeError) as caught:
            with cleanup_after_launch_attempt("docker", container_name):
                raise launch_error

        self.assertIs(caught.exception, launch_error)
        self.assertEqual(
            [call.args[0] for call in mocked_run.call_args_list],
            [
                ["docker", "container", "rm", "--force", container_name],
                ["docker", "container", "inspect", container_name],
            ],
        )

    @mock.patch(f"{__name__}.run_command")
    def test_removal_timeout_with_residue_chains_launch_error(
        self, mocked_run: mock.Mock
    ) -> None:
        container_name = "fleet-node-collector-test-timeout-residue"
        mocked_run.side_effect = [
            subprocess.TimeoutExpired(["docker", "container", "rm"], 20),
            subprocess.CompletedProcess(
                ["docker", "container", "inspect", container_name],
                0,
                stdout='[{"Name":"/fleet-node-collector-test-timeout-residue"}]\n',
                stderr="",
            ),
        ]
        launch_error = RuntimeError("launch timed out")

        with self.assertRaisesRegex(
            AssertionError, "could not verify integration container cleanup"
        ) as caught:
            with cleanup_after_launch_attempt("docker", container_name):
                raise launch_error

        self.assertIs(caught.exception.__cause__, launch_error)
        self.assertIn(container_name, str(caught.exception))
        self.assertEqual(
            [call.args[0] for call in mocked_run.call_args_list],
            [
                ["docker", "container", "rm", "--force", container_name],
                ["docker", "container", "inspect", container_name],
            ],
        )


@unittest.skipUnless(
    RUN_INTEGRATION,
    "set FLEET_RUN_COLLECTOR_INTEGRATION=1 to run the pinned Collector smoke test",
)
class CollectorRuntimeIntegrationTest(unittest.TestCase):
    def test_rendered_filters_and_shared_scrape_route_real_records(self) -> None:
        docker = shutil.which("docker")
        if docker is None:
            self.fail("BLOCKED: docker is required for the pinned Collector integration test")

        image_check = run_command([docker, "image", "inspect", COLLECTOR_IMAGE])
        if image_check.returncode != 0:
            self.fail(
                "BLOCKED: pinned Collector image is not available locally: "
                f"{COLLECTOR_IMAGE}"
            )
        version_container_name = f"fleet-node-collector-test-version-{uuid.uuid4().hex[:12]}"
        version_command = [
            docker,
            "run",
            "--name",
            version_container_name,
            COLLECTOR_IMAGE,
            "--version",
        ]
        self.assertNotIn("--rm", version_command)
        self.assertEqual(version_command[3], version_container_name)
        with cleanup_after_launch_attempt(docker, version_container_name):
            version = run_command(version_command)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn(f"version {COLLECTOR_VERSION}", version.stdout + version.stderr)

        server = ThreadingHTTPServer(("0.0.0.0", 0), MetricsHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        container_name = f"fleet-node-collector-test-{uuid.uuid4().hex[:12]}"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                output_dir = root / "output"
                output_dir.mkdir()
                os.chmod(output_dir, 0o777)
                config_path = root / "collector.json"
                config = AgentConfig(
                    node_label="mini_03",
                    node_user="fleet-mini-03",
                    node_home=Path("/Users/fleet-mini-03"),
                    telemetry_endpoint="https://unused.invalid",
                    codex_usage_enabled=False,
                    openclaw_config_path=Path("/tmp/openclaw.json"),
                    node_exporter_target=f"host.docker.internal:{server.server_port}",
                    node_exporter_textfile_dir=Path("/tmp/textfile"),
                    collector_config_path=Path("/tmp/collector.json"),
                    authorization_header_path=Path("/dev/null"),
                    queue_directory=Path("/output/queue"),
                    collector_binary_path=Path("/tmp/otelcol-contrib"),
                    local_otlp_endpoint="0.0.0.0:4318",
                    collector_metrics_endpoint="0.0.0.0:8888",
                    health_endpoint="0.0.0.0:13133",
                )
                rendered = render_collector_config(config)
                node_receiver = rendered["receivers"]["prometheus/node_exporter"]["config"]
                node_receiver["global"] = {
                    "scrape_interval": "200ms",
                    "scrape_timeout": "100ms",
                }
                file_names = {
                    "otlp_http/logs": "logs.json",
                    "otlp_http/traces": "traces.json",
                    "otlp_http/app_metrics": "app-metrics.json",
                    "otlp_http/agent": "agent.json",
                    "otlp_http/host": "host.json",
                    "otlp_http/heartbeat": "heartbeat.json",
                }
                rendered["exporters"] = {
                    f"file/{name.removesuffix('.json')}": {"path": f"/output/{name}"}
                    for name in file_names.values()
                }
                for pipeline in rendered["service"]["pipelines"].values():
                    pipeline["exporters"] = [
                        f"file/{file_names[exporter].removesuffix('.json')}"
                        for exporter in pipeline["exporters"]
                    ]
                config_path.write_text(json.dumps(rendered), encoding="utf-8")

                logs_path = output_dir / "logs.json"
                host_path = output_dir / "host.json"
                heartbeat_path = output_dir / "heartbeat.json"
                retained_cases = {
                    "ordinary",
                    "ordinary_agent_logger",
                    "sandbox_block_without_removed_count",
                    "tool_success_warn",
                    "tool_near_miss",
                    "qmd_logger",
                    "memory_logger",
                    "status_partial",
                    "status_blocked",
                    "task_output_old_body",
                    "security_success_warn",
                }
                dropped_cases = {"tool_success_info", "security_success_info"}
                # Enter cleanup before docker run so every launch attempt is covered.
                with cleanup_after_launch_attempt(docker, container_name):
                    run = run_command(
                        [
                            docker,
                            "run",
                            "--detach",
                            "--name",
                            container_name,
                            "--add-host",
                            "host.docker.internal:host-gateway",
                            "--publish",
                            "127.0.0.1::4318",
                            "--volume",
                            f"{config_path}:/etc/otelcol-contrib/config.yaml:ro",
                            "--volume",
                            f"{output_dir}:/output",
                            COLLECTOR_IMAGE,
                            "--config=file:/etc/otelcol-contrib/config.yaml",
                        ]
                    )
                    self.assertEqual(run.returncode, 0, run.stderr)
                    port_result = run_command([docker, "port", container_name, "4318/tcp"])
                    self.assertEqual(port_result.returncode, 0, port_result.stderr)
                    host_port = int(port_result.stdout.strip().rsplit(":", 1)[1])

                    now = str(time.time_ns())

                    def record(
                        case: str,
                        severity_number: int,
                        *,
                        body: str = "log",
                        attributes: dict[str, str | int] | None = None,
                    ) -> dict[str, Any]:
                        encoded_attributes = [
                            {
                                "key": "test.case",
                                "value": {"stringValue": case},
                            }
                        ]
                        for key, value in (attributes or {}).items():
                            encoded_attributes.append(
                                {
                                    "key": key,
                                    "value": (
                                        {"intValue": str(value)}
                                        if isinstance(value, int)
                                        else {"stringValue": value}
                                    ),
                                }
                            )
                        return {
                            "timeUnixNano": now,
                            "severityNumber": severity_number,
                            "severityText": "WARN" if severity_number >= 13 else "INFO",
                            "body": {"stringValue": body},
                            "attributes": encoded_attributes,
                        }

                    tool_success = {
                        "openclaw.logger": "agents/tool-policy",
                        "openclaw.removedToolCount": 2,
                        "openclaw.ruleKind": "allow",
                    }
                    security_success = {
                        "openclaw.security.action": "gateway.auth.succeeded",
                        "openclaw.security.outcome": "success",
                        "openclaw.security.policy.decision": "allow",
                    }
                    payload = {
                        "resourceLogs": [
                            {
                                "resource": {"attributes": []},
                                "scopeLogs": [
                                    {
                                        "scope": {"name": "fleet-integration-test"},
                                        "logRecords": [
                                            record("ordinary", 9),
                                            record(
                                                "ordinary_agent_logger",
                                                9,
                                                attributes={"openclaw.logger": "agent/embedded"},
                                            ),
                                            record(
                                                "sandbox_block_without_removed_count",
                                                9,
                                                attributes={
                                                    "openclaw.logger": "agents/tool-policy",
                                                    "openclaw.ruleKind": "deny",
                                                },
                                            ),
                                            record(
                                                "tool_success_info", 9, attributes=tool_success
                                            ),
                                            record(
                                                "tool_success_warn", 13, attributes=tool_success
                                            ),
                                            record(
                                                "tool_near_miss",
                                                9,
                                                attributes=tool_success
                                                | {"openclaw.removedToolCount": 0},
                                            ),
                                            record(
                                                "qmd_logger",
                                                9,
                                                attributes={"openclaw.logger": "qmd"},
                                            ),
                                            record(
                                                "memory_logger",
                                                9,
                                                attributes={"openclaw.logger": "memory"},
                                            ),
                                            record(
                                                "status_partial",
                                                9,
                                                attributes={
                                                    "event_type": "status",
                                                    "status": "partial",
                                                },
                                            ),
                                            record(
                                                "status_blocked",
                                                9,
                                                attributes={
                                                    "event_type": "status",
                                                    "status": "blocked",
                                                },
                                            ),
                                            record(
                                                "task_output_old_body",
                                                9,
                                                body="qmd sync completed for agent mini_03",
                                                attributes={
                                                    "event_type": "user_task_output"
                                                },
                                            ),
                                            record(
                                                "security_success_info",
                                                9,
                                                body="openclaw.security.event",
                                                attributes=security_success,
                                            ),
                                            record(
                                                "security_success_warn",
                                                13,
                                                body="openclaw.security.event",
                                                attributes=security_success,
                                            ),
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{host_port}/v1/logs",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            with urllib.request.urlopen(request, timeout=1) as response:
                                self.assertEqual(response.status, 200)
                            break
                        except (urllib.error.URLError, ConnectionError):
                            if time.monotonic() >= deadline:
                                logs = run_command([docker, "logs", container_name])
                                self.fail(
                                    "Collector OTLP receiver did not start:\n"
                                    f"{logs.stdout}{logs.stderr}"
                                )
                            time.sleep(0.2)

                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        records = log_records(logs_path)
                        observed_cases = {
                            attribute_value(item.get("attributes", []), "test.case")
                            for item in records
                        }
                        host_names, _ = metric_names_and_sources(host_path)
                        heartbeat_names, _ = metric_names_and_sources(heartbeat_path)
                        if (
                            retained_cases.issubset(observed_cases)
                            and "node_cpu_seconds_total" in host_names
                            and HEARTBEAT_METRIC in heartbeat_names
                        ):
                            break
                        time.sleep(0.2)

                final_records = log_records(logs_path)
                for dropped_case in dropped_cases:
                    with self.subTest(dropped_case=dropped_case):
                        self.assertFalse(
                            any(
                                attribute_value(item.get("attributes", []), "test.case")
                                == dropped_case
                                for item in final_records
                            )
                        )
                for retained_case in retained_cases:
                    with self.subTest(retained_case=retained_case):
                        retained = [
                            item
                            for item in final_records
                            if attribute_value(item.get("attributes", []), "test.case")
                            == retained_case
                        ]
                        self.assertEqual(len(retained), 1, retained)
                self.assertEqual(
                    next(
                        item
                        for item in final_records
                        if attribute_value(item.get("attributes", []), "test.case")
                        == "tool_success_warn"
                    ).get("severityNumber"),
                    13,
                )
                ordinary_agent = next(
                    item
                    for item in final_records
                    if attribute_value(item.get("attributes", []), "test.case")
                    == "ordinary_agent_logger"
                )
                self.assertEqual(ordinary_agent.get("severityNumber"), 9)
                self.assertEqual(
                    attribute_value(
                        ordinary_agent.get("attributes", []), "openclaw.logger"
                    ),
                    "agent/embedded",
                )
                sandbox_block = next(
                    item
                    for item in final_records
                    if attribute_value(item.get("attributes", []), "test.case")
                    == "sandbox_block_without_removed_count"
                )
                self.assertEqual(sandbox_block.get("severityNumber"), 9)
                self.assertIsNone(
                    attribute_value(
                        sandbox_block.get("attributes", []),
                        "openclaw.removedToolCount",
                    )
                )

                host_names, host_sources = metric_names_and_sources(host_path)
                heartbeat_names, heartbeat_sources = metric_names_and_sources(heartbeat_path)
                self.assertIn("node_cpu_seconds_total", host_names)
                self.assertNotIn(HEARTBEAT_METRIC, host_names)
                self.assertIn(HEARTBEAT_METRIC, heartbeat_names)
                self.assertNotIn("node_cpu_seconds_total", heartbeat_names)
                self.assertEqual(host_sources, {"node_agent_host"})
                self.assertEqual(heartbeat_sources, {"node_agent_heartbeat"})
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
