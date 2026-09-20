"""Count dated session windows without reading transcripts or exporting identities."""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, time as day_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fleet_node_observability.textfile import escape_label_value, write_textfile_atomic

ZONE = ZoneInfo("America/New_York")
DAYS = 30
MAX_AGENTS = 32
MAX_ROWS = 200_000


class CollectionError(RuntimeError):
    pass


def collect(root: Path, state: Path, now: float) -> tuple[Counter, int, float]:
    """Fail the whole snapshot on source failure; never substitute missing agents with zero."""
    today = datetime.fromtimestamp(now, ZONE).date()
    first = today - timedelta(days=DAYS - 1)
    cutoff_ms = int(datetime.combine(first, day_time(), ZONE).timestamp() * 1000)
    paths = sorted((root / "agents").glob("*/agent/openclaw-agent.sqlite"))
    if not paths or len(paths) > MAX_AGENTS:
        raise CollectionError("source_missing_or_unbounded")
    records = []
    undated = 0
    for path in paths:
        agent = path.parent.parent.name
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", agent):
            raise CollectionError("invalid_agent")
        # SQLite URI mode=ro respects WAL and cannot create or modify the source.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            deadline = time.monotonic() + 10
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            undated += db.execute("SELECT count(*) FROM session_windows WHERE started_at IS NULL OR started_at <= 0").fetchone()[0]
            rows = db.execute(
                "SELECT session_id, session_key, started_at FROM session_windows "
                "WHERE started_at >= ? LIMIT ?", (cutoff_ms, MAX_ROWS + 1)
            ).fetchall()
        if len(rows) > MAX_ROWS:
            raise CollectionError("source_unbounded")
        for session_id, key, started in rows:
            if not isinstance(started, (int, float)) or started > now * 1000 + 30_000:
                raise CollectionError("invalid_start_time")
            day = datetime.fromtimestamp(started / 1000, ZONE).date()
            if day > today:
                continue
            # A cron-root reused for several turns is still one session. No job/run counting.
            cron = bool(re.match(r"^agent:[^:]+:cron:[^:]+(?:$|:)", key or ""))
            identity = hashlib.sha256((agent + "\0" + session_id).encode()).hexdigest()
            records.append((identity, day.isoformat(), int(cron)))

    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Only a private hashed-ID ledger is persisted; source DBs and bodies are never copied.
    with closing(sqlite3.connect(state)) as ledger:
        state.chmod(0o600)
        ledger.executescript(
            "CREATE TABLE IF NOT EXISTS starts (identity TEXT PRIMARY KEY, day TEXT NOT NULL, cron INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL NOT NULL);"
        )
        with ledger:
            ledger.execute("INSERT OR IGNORE INTO metadata VALUES ('observed_since', ?)", (now,))
            ledger.executemany("INSERT OR IGNORE INTO starts VALUES (?, ?, ?)", records)
            ledger.execute("DELETE FROM starts WHERE day < ?", (first.isoformat(),))
        counts = Counter()
        for day, cron, count in ledger.execute("SELECT day, cron, count(*) FROM starts GROUP BY day, cron"):
            counts[day, "all"] += count
            counts[day, "cron"] += count if cron else 0
        since = ledger.execute("SELECT value FROM metadata WHERE key='observed_since'").fetchone()[0]
    return counts, undated, since


def labels(values: dict[str, str]) -> str:
    return "{" + ",".join(f'{k}="{escape_label_value(v)}"' for k, v in values.items()) + "}"


def render(node: str, root: Path, state: Path, now: float) -> tuple[str, int]:
    base = {"node": node, "node_label": node}
    lines = []

    def metric(name: str, help_text: str, value: float, extra: dict[str, str] | None = None) -> None:
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} gauge",
                      f"{name}{labels(base | (extra or {}))} {value:.15g}"])

    metric("openclaw_sessions_collected_at_seconds", "Latest collection attempt timestamp.", now)
    try:
        counts, undated, since = collect(root, state, now)
    except (CollectionError, sqlite3.Error, OSError, ValueError, OverflowError):
        metric("openclaw_sessions_collector_success", "Whether all local session metadata sources were read successfully.", 0)
        return "\n".join(lines) + "\n", 1
    metric("openclaw_sessions_collector_success", "Whether all local session metadata sources were read successfully.", 1)
    metric("openclaw_sessions_undated", "Retained session windows excluded because they have no valid start timestamp.", undated)
    metric("openclaw_sessions_observed_since_seconds", "When the local collector first began preserving dated starts; older counts use retained metadata only.", since)
    name = "openclaw_sessions_daily"
    lines.extend([f"# HELP {name} Unique dated session windows retained or observed per New York calendar day; today is partial.", f"# TYPE {name} gauge"])
    today = datetime.fromtimestamp(now, ZONE).date()
    for offset in reversed(range(DAYS)):
        day = today - timedelta(days=offset)
        # Explicit offset makes the bucket unambiguous across DST in Grafana.
        bucket = datetime.combine(day, day_time(), ZONE).isoformat()
        for kind in ("all", "cron"):
            lines.append(f'{name}{labels(base | {"session_day": bucket, "session_kind": kind})} {counts[day.isoformat(), kind]}')
    return "\n".join(lines) + "\n", 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--openclaw-root", type=Path, default=Path.home() / ".openclaw")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    content, status = render(args.node, args.openclaw_root, args.state, time.time())
    write_textfile_atomic(args.output, content)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
