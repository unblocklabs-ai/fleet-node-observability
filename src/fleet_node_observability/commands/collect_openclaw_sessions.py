"""Count dated session windows without reading transcripts or exporting identities."""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fleet_node_observability.textfile import escape_label_value, write_textfile_atomic

ZONE = ZoneInfo("America/New_York")
MAX_AGENTS = 32
MAX_ROWS = 200_000


class CollectionError(RuntimeError):
    pass


def retention_start(today: date) -> date:
    """Current calendar month and twelve previous months, including leap years."""
    return date(today.year - 1, today.month, 1)


def calendar_counts(counts: Counter, today: date, since: float) -> dict[str, Counter]:
    """Sum unique daily observations, never repeated scrapes or rolling averages.

    Do not invent historical zero buckets before preservation began. Older buckets
    are present only when they contain at least one retained dated observation.
    """
    grouped = {grain: Counter() for grain in ("daily", "weekly", "monthly")}
    observed_day = datetime.fromtimestamp(since, ZONE).date()
    day = retention_start(today)
    while day <= today:
        starts = {"daily": day, "weekly": day - timedelta(days=day.weekday()),
                  "monthly": day.replace(day=1)}
        for grain, start in starts.items():
            bucket = start.isoformat()
            if day >= observed_day or counts[day.isoformat(), "all"] > 0:
                for kind in ("all", "cron"):
                    grouped[grain][bucket, kind] += counts[day.isoformat(), kind]
        day += timedelta(days=1)
    for values in grouped.values():
        for bucket, kind in list(values):
            if kind == "all":
                values[bucket, "noncron"] = values[bucket, "all"] - values[bucket, "cron"]
    return grouped


def collect(root: Path, state: Path, now: float) -> tuple[Counter, int, float]:
    """Fail the whole snapshot on source failure; never substitute missing agents with zero."""
    today = datetime.fromtimestamp(now, ZONE).date()
    first = retention_start(today)
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
            rows = db.execute(
                """SELECT w.session_id, w.session_key, w.started_at,
                       CASE WHEN json_extract(n.entry_json, '$.sessionId') = w.session_id
                            AND json_type(n.entry_json, '$.sessionStartedAt') IN ('integer', 'real')
                            THEN json_extract(n.entry_json, '$.sessionStartedAt') END
                   FROM session_windows w LEFT JOIN session_nodes n
                     ON n.session_key = w.session_key AND n.current_session_id = w.session_id
                   LIMIT ?""", (MAX_ROWS + 1,)
            ).fetchall()
        if len(rows) > MAX_ROWS:
            raise CollectionError("source_unbounded")
        for session_id, key, projected_start, session_start in rows:
            # Match OpenClaw's lifecycle priority, but NEVER its updatedAt fallback.
            # Join by window identity as well as key: a reset must not date an old window.
            started = next((value for value in (session_start, projected_start)
                            if isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) and value > 0), None)
            if started is None:
                undated += 1
                continue
            if started > now * 1000 + 30_000:
                raise CollectionError("invalid_start_time")
            day = datetime.fromtimestamp(started / 1000, ZONE).date()
            if day > today:
                continue
            # A cron-root reused for several turns is still one session. No job/run counting.
            cron = bool(re.match(r"^agent:[^:]+:cron:[^:]+(?:$|:)", key or ""))
            identity = hashlib.sha256((agent + "\0" + session_id).encode()).hexdigest()
            records.append((identity, day.isoformat(), int(cron)))
            if len(records) > MAX_ROWS:
                raise CollectionError("source_unbounded")

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
            # Reconcile previously misdated observations; retain vanished source rows.
            # Include old dates before pruning so corrections can move a row OUT of range.
            ledger.executemany(
                "INSERT INTO starts VALUES (?, ?, ?) ON CONFLICT(identity) DO UPDATE "
                "SET day=excluded.day, cron=excluded.cron", records
            )
            ledger.execute("DELETE FROM starts WHERE day < ?", (first.isoformat(),))
            if ledger.execute("SELECT count(*) FROM starts").fetchone()[0] > MAX_ROWS:
                raise CollectionError("ledger_unbounded")
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
    today = datetime.fromtimestamp(now, ZONE).date()
    metric("openclaw_sessions_retained_from_seconds", "Start of the retained thirteen-calendar-month window; not proof of complete coverage.",
           datetime.combine(retention_start(today), day_time(), ZONE).timestamp())
    for grain, values in calendar_counts(counts, today, since).items():
        name = "openclaw_sessions_" + grain
        lines.extend([f"# HELP {name} Unique observed starts per New York calendar period; current and pre-preservation periods are partial.", f"# TYPE {name} gauge"])
        day = retention_start(today)
        periods = set()
        while day <= today:
            periods.add(day if grain == "daily" else day - timedelta(days=day.weekday())
                        if grain == "weekly" else day.replace(day=1))
            day += timedelta(days=1)
        for day in sorted(periods):
            # Existing session_day label denotes period START; weeks begin Monday.
            bucket = datetime.combine(day, day_time(), ZONE).isoformat()
            for kind in ("all", "cron", "noncron"):
                # Explicit NaN prevents lines bridging unknown history; never fake zero.
                count = values.get((day.isoformat(), kind), "NaN")
                lines.append(f'{name}{labels(base | {"session_day": bucket, "session_kind": kind})} {count}')
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
