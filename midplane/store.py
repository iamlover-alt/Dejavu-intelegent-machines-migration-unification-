"""Episode persistence.

Tests and the default CLI use SQLite. Postgres DDL lives in
migrations/001_episodes.sql.

RECONCILE with episode_schema.sql and episode_example.json. Names here follow
the illustrative table in docs/architecture/first-slice.md.
"""

from __future__ import annotations

import json
import os
import sqlite3
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

from midplane.ddl import (
    POSTGRES_EPISODE_INDEX_DDL,
    POSTGRES_EPISODES_DDL,
    SQLITE_EPISODE_INDEX_DDL,
    SQLITE_EPISODES_DDL,
)

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
    "score_version",
    "created_at",
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
    score_version: str = "stub-v0"
    created_at: str = ""


class InvalidEpisodeCursor(ValueError):
    """The listing cursor does not decode to a created_at and run_id."""


class EpisodeStore(Protocol):
    def upsert(self, episode: Episode) -> None:
        """Insert the episode or replace the row with the same run_id."""

    def get(self, run_id: str) -> Episode | None:
        """Return the episode or None."""

    def count(self, run_id: str) -> int:
        """Return how many rows share run_id. A correct upsert keeps this at 1."""

    def list_episodes(
        self,
        *,
        source: str | None = None,
        min_score: float | None = None,
        since: str | None = None,
        run_ids: Sequence[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Episode], str | None]:
        """Return a page ordered by created_at desc, run_id desc, and the next cursor."""


def episode_timestamp() -> str:
    """UTC timestamp that sorts lexicographically. Export --since uses the same form."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def encode_episode_cursor(created_at: str, run_id: str) -> str:
    raw = json.dumps({"created_at": created_at, "run_id": run_id}, separators=(",", ":")).encode()
    return urlsafe_b64encode(raw).decode().rstrip("=")


def decode_episode_cursor(cursor: str) -> tuple[str, str]:
    padding = "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(urlsafe_b64decode((cursor + padding).encode()).decode())
        created_at = payload["created_at"]
        run_id = payload["run_id"]
    except Exception as exc:
        raise InvalidEpisodeCursor("The cursor is not valid.") from exc
    if not isinstance(created_at, str) or not isinstance(run_id, str):
        raise InvalidEpisodeCursor("The cursor is not valid.")
    return created_at, run_id


def build_episode_list_query(
    *,
    source: str | None,
    min_score: float | None,
    since: str | None,
    run_ids: Sequence[str] | None,
    cursor: str | None,
    limit: int,
) -> tuple[str, list] | None:
    """SQL using `?` placeholders. None means the caller already knows the page is empty."""

    if run_ids is not None and len(run_ids) == 0:
        return None
    clauses: list[str] = []
    params: list = []
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if min_score is not None:
        clauses.append("quality_score >= ?")
        params.append(min_score)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    if run_ids is not None:
        marks = ", ".join("?" for _ in run_ids)
        clauses.append(f"run_id in ({marks})")
        params.extend(list(run_ids))
    if cursor is not None:
        created_at, run_id = decode_episode_cursor(cursor)
        clauses.append("(created_at < ? or (created_at = ? and run_id < ?))")
        params.extend([created_at, created_at, run_id])
    selected = ", ".join(EPISODE_COLUMNS)
    sql = f"select {selected} from episodes"
    if clauses:
        sql += " where " + " and ".join(clauses)
    sql += " order by created_at desc, run_id desc limit ?"
    params.append(limit + 1)
    return sql, params


def trim_episode_page(episodes: list[Episode], limit: int) -> tuple[list[Episode], str | None]:
    if len(episodes) <= limit:
        return episodes, None
    page = episodes[:limit]
    last = page[-1]
    return page, encode_episode_cursor(last.created_at, last.run_id)


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _loads(value: str) -> object:
    return json.loads(value)


def _episode_from_mapping(data: dict) -> Episode:
    for column in JSON_COLUMNS:
        raw = data.get(column)
        if isinstance(raw, str):
            data[column] = _loads(raw)
    data["latency_ms"] = int(data["latency_ms"])
    data["quality_score"] = float(data["quality_score"])
    data["score_version"] = str(data.get("score_version") or "stub-v0")
    data["created_at"] = str(data.get("created_at") or "")
    return Episode(
        run_id=data["run_id"],
        source=data["source"],
        inputs=data["inputs"],
        plan=data["plan"],
        tool_calls=data["tool_calls"],
        model_choices=data["model_choices"],
        outputs=data["outputs"],
        cost=data["cost"],
        latency_ms=data["latency_ms"],
        outcome=data["outcome"],
        quality_score=data["quality_score"],
        trace=data["trace"],
        score_version=data["score_version"],
        created_at=data["created_at"],
    )


def _row_to_episode(row: sqlite3.Row | dict) -> Episode:
    data = {column: row[column] for column in EPISODE_COLUMNS}
    return _episode_from_mapping(data)


class SqliteEpisodeStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SQLITE_EPISODES_DDL)
        self._ensure_export_columns()
        self._conn.executescript(SQLITE_EPISODE_INDEX_DDL)
        self._conn.commit()

    def _ensure_export_columns(self) -> None:
        present = {row[1] for row in self._conn.execute("pragma table_info(episodes)")}
        if "score_version" not in present:
            self._conn.execute(
                "alter table episodes add column score_version text not null default 'stub-v0'"
            )
        if "created_at" not in present:
            self._conn.execute("alter table episodes add column created_at text not null default ''")

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
            "score_version": episode.score_version,
            "created_at": episode.created_at,
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

    def list_episodes(
        self,
        *,
        source: str | None = None,
        min_score: float | None = None,
        since: str | None = None,
        run_ids: Sequence[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Episode], str | None]:
        built = build_episode_list_query(
            source=source,
            min_score=min_score,
            since=since,
            run_ids=run_ids,
            cursor=cursor,
            limit=limit,
        )
        if built is None:
            return [], None
        sql, params = built
        rows = self._conn.execute(sql, params).fetchall()
        return trim_episode_page([_row_to_episode(row) for row in rows], limit)


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
        self.dsn = dsn
        self._conn = psycopg.connect(dsn)
        for script in (POSTGRES_EPISODES_DDL, POSTGRES_EPISODE_INDEX_DDL):
            for statement in script.split(";"):
                sql = statement.strip()
                if sql:
                    self._conn.execute(sql)
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
        return _episode_from_mapping(dict(zip(EPISODE_COLUMNS, row)))

    def count(self, run_id: str) -> int:
        row = self._conn.execute(
            "select count(*) from episodes where run_id = %s",
            (run_id,),
        ).fetchone()
        return int(row[0])

    def list_episodes(
        self,
        *,
        source: str | None = None,
        min_score: float | None = None,
        since: str | None = None,
        run_ids: Sequence[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Episode], str | None]:
        built = build_episode_list_query(
            source=source,
            min_score=min_score,
            since=since,
            run_ids=run_ids,
            cursor=cursor,
            limit=limit,
        )
        if built is None:
            return [], None
        sql, params = built
        rows = self._conn.execute(sql.replace("?", "%s"), params).fetchall()
        episodes = [_episode_from_mapping(dict(zip(EPISODE_COLUMNS, row))) for row in rows]
        return trim_episode_page(episodes, limit)


def migration_sql_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "migrations" / "001_episodes.sql"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("migrations/001_episodes.sql")


def resolve_database_url(explicit: str | None = None) -> str:
    """Prefer an explicit URL, then DATABASE_URL, then MIDPLANE_DATABASE_URL."""

    if explicit is not None:
        return explicit
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("MIDPLANE_DATABASE_URL")
        or "sqlite:///midplane_episodes.sqlite"
    )


def open_store(url: str | None = None) -> EpisodeStore:
    resolved = resolve_database_url(url)
    if resolved in {":memory:", "sqlite:///:memory:"}:
        return SqliteEpisodeStore(":memory:")
    if resolved.startswith("sqlite:///"):
        return SqliteEpisodeStore(resolved[len("sqlite:///") :])
    if resolved.startswith("postgres://") or resolved.startswith("postgresql://"):
        return PostgresEpisodeStore(resolved)
    raise ValueError(
        "DATABASE_URL must be sqlite:///..., sqlite:///:memory:, or a postgres URL"
    )
