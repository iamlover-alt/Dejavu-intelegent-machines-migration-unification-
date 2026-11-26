"""API key and run-record store. SQLite for tests, Postgres in docker compose."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from midplane.gateway.ddl import POSTGRES_GATEWAY_DDL, SQLITE_GATEWAY_DDL


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    key_prefix: str
    client_id: str
    created_at: str
    revoked_at: str | None


@dataclass
class RunRecord:
    run_id: str
    client_id: str
    client_ref: str | None
    task_type: str
    input: dict
    status: str
    result: dict | None
    episode_id: str | None
    error_message: str | None
    events: list
    created_at: str
    updated_at: str


class GatewayStore:
    def __init__(self, url: str) -> None:
        self.url = url
        self._lock = threading.Lock()
        if url in {":memory:", "sqlite:///:memory:"}:
            raise ValueError("the gateway store needs a file or Postgres URL so worker threads share it")
        if url.startswith("sqlite:///"):
            self.dialect = "sqlite"
            path = url[len("sqlite:///") :]
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.executescript(SQLITE_GATEWAY_DDL)
            self._conn.commit()
        elif url.startswith("postgres://") or url.startswith("postgresql://"):
            self.dialect = "postgres"
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError(
                    "Postgres requires the postgres extra: pip install 'midplane[postgres]'"
                ) from exc
            self._conn = psycopg.connect(url)
            for statement in POSTGRES_GATEWAY_DDL.split(";"):
                sql = statement.strip()
                if sql:
                    self._conn.execute(sql)
            self._conn.commit()
        else:
            raise ValueError("database URL must be sqlite:///... or a postgres URL")

    def close(self) -> None:
        self._conn.close()

    def _json_placeholder(self) -> str:
        return "?::jsonb" if self.dialect == "postgres" else "?"

    def _execute(self, sql: str, params: tuple = ()) -> Any:
        statement = sql if self.dialect == "sqlite" else sql.replace("?", "%s")
        return self._conn.execute(statement, params)

    def insert_key(self, *, key_id: str, key_hash: str, key_prefix: str, client_id: str) -> KeyRecord:
        created_at = utc_now()
        with self._lock:
            self._execute(
                """
                insert into api_keys (key_id, key_hash, key_prefix, client_id, created_at, revoked_at)
                values (?, ?, ?, ?, ?, null)
                """,
                (key_id, key_hash, key_prefix, client_id, created_at),
            )
            self._conn.commit()
        return KeyRecord(key_id, key_prefix, client_id, created_at, None)

    def find_active_key(self, key_hash: str) -> KeyRecord | None:
        with self._lock:
            row = self._execute(
                """
                select key_id, key_prefix, client_id, created_at, revoked_at
                from api_keys
                where key_hash = ? and revoked_at is null
                """,
                (key_hash,),
            ).fetchone()
        if row is None:
            return None
        return self._key_from_row(row)

    def revoke_key(self, key_id: str) -> bool:
        with self._lock:
            row = self._execute(
                "select revoked_at from api_keys where key_id = ?",
                (key_id,),
            ).fetchone()
            if row is None:
                return False
            revoked = row["revoked_at"] if self.dialect == "sqlite" else row[0]
            if revoked:
                return True
            self._execute(
                "update api_keys set revoked_at = ? where key_id = ?",
                (utc_now(), key_id),
            )
            self._conn.commit()
        return True

    def insert_run(
        self,
        *,
        run_id: str,
        client_id: str,
        client_ref: str | None,
        task_type: str,
        payload: dict,
    ) -> RunRecord:
        now = utc_now()
        record = RunRecord(
            run_id=run_id,
            client_id=client_id,
            client_ref=client_ref,
            task_type=task_type,
            input=payload,
            status="queued",
            result=None,
            episode_id=None,
            error_message=None,
            events=[],
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            payload_sql = self._json_placeholder()
            events_sql = self._json_placeholder()
            self._execute(
                f"""
                insert into runs (
                  run_id, client_id, client_ref, task_type, input_json, status,
                  result_json, episode_id, error_message, events_json, created_at, updated_at
                ) values (?, ?, ?, ?, {payload_sql}, 'queued', null, null, null, {events_sql}, ?, ?)
                """,
                (
                    run_id,
                    client_id,
                    client_ref,
                    task_type,
                    json.dumps(payload),
                    json.dumps([]),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            row = self._execute("select * from runs where run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return self._run_from_row(row)

    def find_run_for_episode(self, episode_id: str, client_id: str) -> RunRecord | None:
        with self._lock:
            row = self._execute(
                "select * from runs where episode_id = ? and client_id = ?",
                (episode_id, client_id),
            ).fetchone()
        if row is None:
            return None
        return self._run_from_row(row)

    def mark_running(self, run_id: str) -> None:
        self._set_status(run_id, "running")

    def append_event(self, run_id: str, event: dict) -> None:
        with self._lock:
            row = self._execute(
                "select events_json from runs where run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return
            raw = row["events_json"] if self.dialect == "sqlite" else row[0]
            events = json.loads(raw) if isinstance(raw, str) else list(raw)
            events.append(event)
            self._execute(
                f"update runs set events_json = {self._json_placeholder()}, updated_at = ? where run_id = ?",
                (json.dumps(events), utc_now(), run_id),
            )
            self._conn.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        result: dict | None,
        episode_id: str | None,
        error_message: str | None,
    ) -> None:
        with self._lock:
            result_sql = "null" if result is None else self._json_placeholder()
            params: list = [status]
            if result is not None:
                params.append(json.dumps(result))
            params.extend([episode_id, error_message, utc_now(), run_id])
            self._execute(
                f"""
                update runs
                set status = ?, result_json = {result_sql}, episode_id = ?, error_message = ?, updated_at = ?
                where run_id = ?
                """,
                tuple(params),
            )
            self._conn.commit()

    def _set_status(self, run_id: str, status: str) -> None:
        with self._lock:
            self._execute(
                "update runs set status = ?, updated_at = ? where run_id = ?",
                (status, utc_now(), run_id),
            )
            self._conn.commit()

    def _key_from_row(self, row) -> KeyRecord:
        if self.dialect == "sqlite":
            return KeyRecord(
                row["key_id"],
                row["key_prefix"],
                row["client_id"],
                row["created_at"],
                row["revoked_at"],
            )
        return KeyRecord(row[0], row[1], row[2], row[3], row[4])

    def _run_from_row(self, row) -> RunRecord:
        if self.dialect == "sqlite":
            data = dict(row)
        else:
            columns = (
                "run_id",
                "client_id",
                "client_ref",
                "task_type",
                "input_json",
                "status",
                "result_json",
                "episode_id",
                "error_message",
                "events_json",
                "created_at",
                "updated_at",
            )
            data = dict(zip(columns, row))
        return RunRecord(
            run_id=data["run_id"],
            client_id=data["client_id"],
            client_ref=data["client_ref"],
            task_type=data["task_type"],
            input=_json_value(data["input_json"]),
            status=data["status"],
            result=_json_value(data["result_json"]) if data["result_json"] is not None else None,
            episode_id=data["episode_id"],
            error_message=data["error_message"],
            events=_json_value(data["events_json"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


def _json_value(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return raw
