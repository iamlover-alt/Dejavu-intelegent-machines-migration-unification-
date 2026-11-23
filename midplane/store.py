"""Episode persistence.

Tests and the default CLI use SQLite. Postgres DDL lives in
migrations/001_episodes.sql.

RECONCILE with episode_schema.sql and episode_example.json. Names here follow
the illustrative table in docs/architecture/first-slice.md.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from midplane.ddl import POSTGRES_EPISODES_DDL, SQLITE_EPISODES_DDL

EPISODE_COLUMNS = (
    "run_id",
    "source",
    "inputs",
    "plan",
    "tool_calls",
    "model_choices",
    "outputs",
    "cost",
    "latency_ms",
    "outcome",
    "quality_score",
    "trace",
)

JSON_COLUMNS = (
    "inputs",
    "plan",
    "tool_calls",
    "model_choices",
    "outputs",
    "cost",
    "trace",
)


@dataclass(frozen=True)
class Episode:
    run_id: str
    source: str
    inputs: dict
    plan: dict
    tool_calls: list
    model_choices: list
    outputs: dict
    cost: dict
    latency_ms: int
    outcome: str
    quality_score: float
    trace: list


class EpisodeStore(Protocol):
    def upsert(self, episode: Episode) -> None:
        """Insert the episode or replace the row with the same run_id."""

    def get(self, run_id: str) -> Episode | None:
        """Return the episode or None."""

    def count(self, run_id: str) -> int:
        """Return how many rows share run_id. A correct upsert keeps this at 1."""

    def list_all(self) -> list[Episode]:
        """Return every episode ordered by run_id."""


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _loads(value: str) -> object:
    return json.loads(value)


def _row_to_episode(row: sqlite3.Row | dict) -> Episode:
    data = {column: row[column] for column in EPISODE_COLUMNS}
    for column in JSON_COLUMNS:
        raw = data[column]
        data[column] = _loads(raw) if isinstance(raw, str) else raw
    data["latency_ms"] = int(data["latency_ms"])
    data["quality_score"] = float(data["quality_score"])
    return Episode(**data)


class SqliteEpisodeStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SQLITE_EPISODES_DDL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(self, episode: Episode) -> None:
        payload = {
            "run_id": episode.run_id,
            "source": episode.source,
            "inputs": _dumps(episode.inputs),
            "plan": _dumps(episode.plan),
            "tool_calls": _dumps(episode.tool_calls),
            "model_choices": _dumps(episode.model_choices),
            "outputs": _dumps(episode.outputs),
            "cost": _dumps(episode.cost),
            "latency_ms": episode.latency_ms,
            "outcome": episode.outcome,
            "quality_score": episode.quality_score,
            "trace": _dumps(episode.trace),
        }
        assignments = ", ".join(f"{column} = excluded.{column}" for column in EPISODE_COLUMNS if column != "run_id")
        columns = ", ".join(EPISODE_COLUMNS)
        placeholders = ", ".join(f":{column}" for column in EPISODE_COLUMNS)
        self._conn.execute(
            f"""
            insert into episodes ({columns})
            values ({placeholders})
            on conflict(run_id) do update set {assignments}
            """,
            payload,
        )
        self._conn.commit()

    def get(self, run_id: str) -> Episode | None:
        row = self._conn.execute("select * from episodes where run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return _row_to_episode(row)

    def count(self, run_id: str) -> int:
        row = self._conn.execute("select count(*) as n from episodes where run_id = ?", (run_id,)).fetchone()
        return int(row["n"])

    def list_all(self) -> list[Episode]:
        selected = ", ".join(EPISODE_COLUMNS)
        rows = self._conn.execute(f"select {selected} from episodes order by run_id").fetchall()
        return [_row_to_episode(row) for row in rows]


def _postgres_row_to_episode(row: tuple) -> Episode:
    data = dict(zip(EPISODE_COLUMNS, row))
    for column in JSON_COLUMNS:
        raw = data[column]
        if isinstance(raw, str):
            data[column] = json.loads(raw)
    data["latency_ms"] = int(data["latency_ms"])
    data["quality_score"] = float(data["quality_score"])
    return Episode(**data)


class PostgresEpisodeStore:
    """Postgres store. Requires the optional `postgres` extra (`psycopg`).

    Applies migrations/001_episodes.sql semantics via the packaged DDL.
    """

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Postgres episodes require the postgres extra: pip install 'midplane[postgres]'"
            ) from exc
        self._conn = psycopg.connect(dsn)
        self._conn.execute(POSTGRES_EPISODES_DDL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(self, episode: Episode) -> None:
        columns = ", ".join(EPISODE_COLUMNS)
        assignments = ", ".join(
            f"{column} = excluded.{column}" for column in EPISODE_COLUMNS if column != "run_id"
        )
        values = []
        placeholders = []
        for column in EPISODE_COLUMNS:
            value = getattr(episode, column)
            if column in JSON_COLUMNS:
                values.append(json.dumps(value))
                placeholders.append("%s::jsonb")
            else:
                values.append(value)
                placeholders.append("%s")
        self._conn.execute(
            f"""
            insert into episodes ({columns})
            values ({", ".join(placeholders)})
            on conflict (run_id) do update set {assignments}
            """,
            values,
        )
        self._conn.commit()

    def get(self, run_id: str) -> Episode | None:
        selected = ", ".join(EPISODE_COLUMNS)
        row = self._conn.execute(
            f"select {selected} from episodes where run_id = %s",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return _postgres_row_to_episode(row)

    def count(self, run_id: str) -> int:
        row = self._conn.execute(
            "select count(*) from episodes where run_id = %s",
            (run_id,),
        ).fetchone()
        return int(row[0])

    def list_all(self) -> list[Episode]:
        selected = ", ".join(EPISODE_COLUMNS)
        rows = self._conn.execute(f"select {selected} from episodes order by run_id").fetchall()
        return [_postgres_row_to_episode(row) for row in rows]


def migration_sql_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "migrations" / "001_episodes.sql"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("migrations/001_episodes.sql")


def open_store(url: str | None = None) -> EpisodeStore:
    import os

    resolved = url if url is not None else os.environ.get(
        "MIDPLANE_DATABASE_URL",
        "sqlite:///midplane_episodes.sqlite",
    )
    if resolved in {":memory:", "sqlite:///:memory:"}:
        return SqliteEpisodeStore(":memory:")
    if resolved.startswith("sqlite:///"):
        return SqliteEpisodeStore(resolved[len("sqlite:///") :])
    if resolved.startswith("postgres://") or resolved.startswith("postgresql://"):
        return PostgresEpisodeStore(resolved)
    raise ValueError(
        "MIDPLANE_DATABASE_URL must be sqlite:///..., sqlite:///:memory:, or a postgres URL"
    )
