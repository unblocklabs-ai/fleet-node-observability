"""Configuration and rendering for the unified fleet node telemetry agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import ConfigError, normalize_label


COLLECTOR_VERSION = "0.157.0"
COLLECTOR_RELEASES: dict[str, dict[str, str]] = {
    "darwin_amd64": {
        "url": (
            "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/"
            "download/v0.157.0/otelcol-contrib_0.157.0_darwin_amd64.tar.gz"
        ),
        "sha256": "e11e7482144c3ac1eb1f612d3d175589435cad968a791d6ef5c73be43e1b8c34",
    },
    "darwin_arm64": {
        "url": (
            "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/"
            "download/v0.157.0/otelcol-contrib_0.157.0_darwin_arm64.tar.gz"
        ),
        "sha256": "6c03308935573712a795b4229f756bc4288bbbb13850604f3c7287868af84d4b",
    },
}

TELEMETRY_MODES = frozenset({"pull", "dual", "push"})
HEARTBEAT_METRIC = "fleet_node_agent_heartbeat_timestamp_seconds"


@dataclass(frozen=True)
class AgentConfig:
    node_label: str
    node_user: str
    node_home: Path
    telemetry_mode: str
    telemetry_endpoint: str
    openclaw_config_path: Path
    node_exporter_target: str
    node_exporter_textfile_dir: Path
    collector_config_path: Path
    authorization_header_path: Path
    queue_directory: Path
    collector_binary_path: Path
    local_otlp_endpoint: str = "127.0.0.1:4318"
    collector_metrics_endpoint: str = "127.0.0.1:8888"
    health_endpoint: str = "127.0.0.1:13133"


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"node config {path} not found") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"node config {path} contains invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"unable to read node config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"node config {path} must contain a JSON object")
    return payload


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} is required and must be a non-empty string")
    return value.strip()


def _string(payload: dict[str, Any], key: str, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value.strip()


def _absolute_path(value: str, *, key: str, node_home: Path | None = None) -> Path:
    if value.startswith("~/"):
        if node_home is None:
            raise ConfigError(f"{key} cannot use ~ before node_home is resolved")
        path = node_home / value[2:]
    else:
        path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ConfigError(f"{key} must be an absolute path without parent-directory segments")
    return path


def _validate_host_port(value: str, *, key: str, require_loopback: bool) -> str:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise ConfigError(f"{key} must use host:port syntax")
    port = int(port_text)
    if port < 1 or port > 65535:
        raise ConfigError(f"{key} port must be between 1 and 65535")
    if require_loopback and host not in {"127.0.0.1", "localhost", "::1", "[::1]"}:
        raise ConfigError(f"{key} must bind to loopback")
    return value


def _validate_endpoint(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.hostname is None:
        raise ConfigError("telemetry_endpoint must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("telemetry_endpoint must not include userinfo")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("telemetry_endpoint port must be numeric and between 1 and 65535") from exc
    if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
        raise ConfigError("telemetry_endpoint port must be numeric and between 1 and 65535")
    if parsed.query or parsed.fragment:
        raise ConfigError("telemetry_endpoint must not include a query or fragment")
    if parsed.path not in {"", "/"}:
        raise ConfigError("telemetry_endpoint must be the OTLP/HTTP base URL without /v1/*")
    return value.rstrip("/")


def load_agent_config(path: Path | str) -> AgentConfig:
    payload = _load_object(Path(path))
    node_label = normalize_label(_required_string(payload, "node_label"))
    node_user = _required_string(payload, "node_user")
    if any(char.isspace() for char in node_user) or "/" in node_user:
        raise ConfigError("node_user must be a local account name")

    node_home = _absolute_path(_required_string(payload, "node_home"), key="node_home")
    mode = _string(payload, "telemetry_mode", "push").lower()
    if mode not in TELEMETRY_MODES:
        raise ConfigError("telemetry_mode must be one of: pull, dual, push")
    raw_telemetry_endpoint = payload.get("telemetry_endpoint")
    if (
        isinstance(raw_telemetry_endpoint, str)
        and raw_telemetry_endpoint.strip() != raw_telemetry_endpoint
    ):
        raise ConfigError("telemetry_endpoint must not include surrounding whitespace")

    base_dir = node_home / ".openclaw" / "fleet-node-observability"
    config = AgentConfig(
        node_label=node_label,
        node_user=node_user,
        node_home=node_home,
        telemetry_mode=mode,
        telemetry_endpoint=_validate_endpoint(_required_string(payload, "telemetry_endpoint")),
        openclaw_config_path=_absolute_path(
            _string(payload, "openclaw_config_path", "~/.openclaw/openclaw.json"),
            key="openclaw_config_path",
            node_home=node_home,
        ),
        node_exporter_target=_validate_host_port(
            _string(payload, "node_exporter_target", "127.0.0.1:9100"),
            key="node_exporter_target",
            require_loopback=True,
        ),
        node_exporter_textfile_dir=_absolute_path(
            _string(
                payload,
                "node_exporter_textfile_dir",
                "/opt/homebrew/var/lib/node_exporter/textfile_collector",
            ),
            key="node_exporter_textfile_dir",
        ),
        collector_config_path=_absolute_path(
            _string(payload, "collector_config_path", str(base_dir / "config" / "collector.json")),
            key="collector_config_path",
        ),
        authorization_header_path=_absolute_path(
            _string(
                payload,
                "authorization_header_path",
                str(base_dir / "secrets" / "authorization-header"),
            ),
            key="authorization_header_path",
        ),
        queue_directory=_absolute_path(
            _string(payload, "queue_directory", str(base_dir / "queue")),
            key="queue_directory",
        ),
        collector_binary_path=_absolute_path(
            _string(payload, "collector_binary_path", str(base_dir / "bin" / "otelcol-contrib")),
            key="collector_binary_path",
        ),
        local_otlp_endpoint=_validate_host_port(
            _string(payload, "local_otlp_endpoint", "127.0.0.1:4318"),
            key="local_otlp_endpoint",
            require_loopback=True,
        ),
        collector_metrics_endpoint=_validate_host_port(
            _string(payload, "collector_metrics_endpoint", "127.0.0.1:8888"),
            key="collector_metrics_endpoint",
            require_loopback=True,
        ),
        health_endpoint=_validate_host_port(
            _string(payload, "health_endpoint", "127.0.0.1:13133"),
            key="health_endpoint",
            require_loopback=True,
        ),
    )
    for key, managed_path in {
        "collector_config_path": config.collector_config_path,
        "authorization_header_path": config.authorization_header_path,
        "queue_directory": config.queue_directory,
        "collector_binary_path": config.collector_binary_path,
    }.items():
        try:
            managed_path.relative_to(base_dir)
        except ValueError as exc:
            raise ConfigError(f"{key} must be inside {base_dir}") from exc
    _validate_managed_path_relationships(config, state_directory=base_dir / "state")
    allowed_textfile_dirs = {
        Path("/opt/homebrew/var/lib/node_exporter/textfile_collector"),
        Path("/usr/local/var/lib/node_exporter/textfile_collector"),
    }
    if config.node_exporter_textfile_dir not in allowed_textfile_dirs:
        try:
            config.node_exporter_textfile_dir.relative_to(node_home)
        except ValueError as exc:
            raise ConfigError(
                "node_exporter_textfile_dir must use a supported Homebrew path or live under node_home"
            ) from exc
    return config


def _validate_managed_path_relationships(
    config: AgentConfig, *, state_directory: Path
) -> None:
    file_targets = {
        "collector_config_path": config.collector_config_path,
        "authorization_header_path": config.authorization_header_path,
        "collector_binary_path": config.collector_binary_path,
    }
    directory_targets = {
        "queue_directory": config.queue_directory,
        "state_directory": state_directory,
    }

    file_items = list(file_targets.items())
    for index, (left_name, left_path) in enumerate(file_items):
        for right_name, right_path in file_items[index + 1 :]:
            if (
                left_path == right_path
                or left_path in right_path.parents
                or right_path in left_path.parents
            ):
                raise ConfigError(f"{left_name} and {right_name} must not overlap")

    if (
        config.queue_directory == state_directory
        or config.queue_directory in state_directory.parents
        or state_directory in config.queue_directory.parents
    ):
        raise ConfigError("queue_directory and state_directory must not overlap")

    for directory_name, directory_path in directory_targets.items():
        for file_name, file_path in file_targets.items():
            if (
                directory_path == file_path
                or directory_path in file_path.parents
                or file_path in directory_path.parents
            ):
                raise ConfigError(f"{directory_name} and {file_name} must not overlap")


def _resource_processor(config: AgentConfig, source: str) -> dict[str, Any]:
    return {
        "attributes": [
            {"key": "fleet.transport", "value": config.telemetry_mode, "action": "upsert"},
            {"key": "fleet.signal.source", "value": source, "action": "upsert"},
            {"key": "fleet.claimed_node", "value": config.node_label, "action": "upsert"},
        ]
    }


def _exporter(
    config: AgentConfig,
    *,
    queue_bytes: int,
    request_bytes: int,
    retry_max_elapsed: str,
) -> dict[str, Any]:
    return {
        "endpoint": config.telemetry_endpoint,
        "headers": {"Authorization": f"${{file:{config.authorization_header_path}}}"},
        "compression": "gzip",
        "timeout": "10s",
        "retry_on_failure": {
            "enabled": True,
            "initial_interval": "1s",
            "max_interval": "30s",
            "max_elapsed_time": retry_max_elapsed,
        },
        "sending_queue": {
            "enabled": True,
            "sizer": "bytes",
            "queue_size": queue_bytes,
            "num_consumers": 1,
            "block_on_overflow": False,
            "storage": "file_storage/fleet",
            "batch": {
                "flush_timeout": "1s",
                "min_size": min(request_bytes // 4, 256 * 1024),
                "max_size": request_bytes,
                "sizer": "bytes",
            },
        },
    }


def render_collector_config(config: AgentConfig) -> dict[str, Any]:
    """Render the complete, secret-free Collector configuration."""

    receivers: dict[str, Any] = {
        "otlp/openclaw": {
            "protocols": {"http": {"endpoint": config.local_otlp_endpoint}}
        },
        "prometheus/agent": {
            "config": {
                "global": {"scrape_interval": "15s", "scrape_timeout": "5s"},
                "scrape_configs": [
                    {
                        "job_name": "fleet_node_agent",
                        "static_configs": [{"targets": [config.collector_metrics_endpoint]}],
                    }
                ],
            }
        },
    }

    processors: dict[str, Any] = {
        "memory_limiter": {
            "check_interval": "1s",
            "limit_mib": 96,
            "spike_limit_mib": 24,
        },
        "resource/openclaw": _resource_processor(config, "openclaw"),
        "resource/agent": _resource_processor(config, "node_agent_internal"),
        "batch/logs": {"timeout": "1s", "send_batch_size": 512, "send_batch_max_size": 1024},
        "batch/traces": {"timeout": "1s", "send_batch_size": 512, "send_batch_max_size": 1024},
        "batch/app_metrics": {"timeout": "2s", "send_batch_size": 1000, "send_batch_max_size": 2000},
        "batch/agent": {"timeout": "5s", "send_batch_size": 500, "send_batch_max_size": 1000},
    }

    exporters: dict[str, Any] = {
        "otlp_http/logs": _exporter(
            config, queue_bytes=96 * 1024 * 1024, request_bytes=1024 * 1024, retry_max_elapsed="24h"
        ),
        "otlp_http/traces": _exporter(
            config, queue_bytes=48 * 1024 * 1024, request_bytes=1024 * 1024, retry_max_elapsed="12h"
        ),
        "otlp_http/app_metrics": _exporter(
            config, queue_bytes=32 * 1024 * 1024, request_bytes=512 * 1024, retry_max_elapsed="6h"
        ),
        "otlp_http/agent": _exporter(
            config, queue_bytes=16 * 1024 * 1024, request_bytes=256 * 1024, retry_max_elapsed="30m"
        ),
    }

    pipelines: dict[str, Any] = {
        "logs/openclaw": {
            "receivers": ["otlp/openclaw"],
            "processors": ["memory_limiter", "resource/openclaw", "batch/logs"],
            "exporters": ["otlp_http/logs"],
        },
        "traces/openclaw": {
            "receivers": ["otlp/openclaw"],
            "processors": ["memory_limiter", "resource/openclaw", "batch/traces"],
            "exporters": ["otlp_http/traces"],
        },
        "metrics/openclaw": {
            "receivers": ["otlp/openclaw"],
            "processors": ["memory_limiter", "resource/openclaw", "batch/app_metrics"],
            "exporters": ["otlp_http/app_metrics"],
        },
        "metrics/agent": {
            "receivers": ["prometheus/agent"],
            "processors": ["memory_limiter", "resource/agent", "batch/agent"],
            "exporters": ["otlp_http/agent"],
        },
    }

    if config.telemetry_mode in {"dual", "push"}:
        receivers.update(
            {
                "prometheus/host": {
                    "config": {
                        "global": {"scrape_interval": "15s", "scrape_timeout": "5s"},
                        "scrape_configs": [
                            {
                                "job_name": "node_exporter_fleet",
                                "static_configs": [{"targets": [config.node_exporter_target]}],
                                "metric_relabel_configs": [
                                    {
                                        "source_labels": ["__name__"],
                                        "regex": HEARTBEAT_METRIC,
                                        "action": "drop",
                                    }
                                ],
                            }
                        ],
                    }
                },
                "prometheus/heartbeat": {
                    "config": {
                        "global": {"scrape_interval": "15s", "scrape_timeout": "5s"},
                        "scrape_configs": [
                            {
                                "job_name": "fleet_node_agent_heartbeat",
                                "static_configs": [{"targets": [config.node_exporter_target]}],
                                "metric_relabel_configs": [
                                    {
                                        "source_labels": ["__name__"],
                                        "regex": HEARTBEAT_METRIC,
                                        "action": "keep",
                                    }
                                ],
                            }
                        ],
                    }
                },
            }
        )
        processors.update(
            {
                "resource/host": _resource_processor(config, "node_agent_host"),
                "resource/heartbeat": _resource_processor(config, "node_agent_heartbeat"),
                "batch/host": {"timeout": "3s", "send_batch_size": 1000, "send_batch_max_size": 2000},
                "batch/heartbeat": {"timeout": "1s", "send_batch_size": 10, "send_batch_max_size": 20},
            }
        )
        exporters.update(
            {
                "otlp_http/host": _exporter(
                    config, queue_bytes=48 * 1024 * 1024, request_bytes=512 * 1024, retry_max_elapsed="6h"
                ),
                "otlp_http/heartbeat": _exporter(
                    config, queue_bytes=8 * 1024 * 1024, request_bytes=64 * 1024, retry_max_elapsed="5m"
                ),
            }
        )
        pipelines.update(
            {
                "metrics/host": {
                    "receivers": ["prometheus/host"],
                    "processors": ["memory_limiter", "resource/host", "batch/host"],
                    "exporters": ["otlp_http/host"],
                },
                "metrics/heartbeat": {
                    "receivers": ["prometheus/heartbeat"],
                    "processors": ["memory_limiter", "resource/heartbeat", "batch/heartbeat"],
                    "exporters": ["otlp_http/heartbeat"],
                },
            }
        )

    return {
        "extensions": {
            "file_storage/fleet": {
                "directory": str(config.queue_directory),
                "create_directory": True,
                "fsync": True,
                "compaction": {
                    "directory": str(config.queue_directory / "compaction"),
                    "on_start": True,
                },
            },
            "health_check": {"endpoint": config.health_endpoint},
        },
        "receivers": receivers,
        "processors": processors,
        "exporters": exporters,
        "service": {
            "extensions": ["file_storage/fleet", "health_check"],
            "telemetry": {
                "metrics": {
                    "level": "normal",
                    "readers": [
                        {
                            "pull": {
                                "exporter": {
                                    "prometheus": {
                                        "host": config.collector_metrics_endpoint.rpartition(":")[0],
                                        "port": int(config.collector_metrics_endpoint.rpartition(":")[2]),
                                    }
                                }
                            }
                        }
                    ],
                }
            },
            "pipelines": pipelines,
        },
    }


def render_openclaw_local_settings(config: AgentConfig) -> dict[str, Any]:
    return {
        "endpoint": f"http://{config.local_otlp_endpoint}",
        "service_name": "openclaw_gateway",
        "headers": {},
    }


def render_authorization_header(node_label: str, token: str) -> str:
    import base64

    if not token or "\n" in token or "\r" in token:
        raise ConfigError("ingest token must be non-empty and contain no newlines")
    normalized = normalize_label(node_label)
    encoded = base64.b64encode(f"{normalized}:{token}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"
