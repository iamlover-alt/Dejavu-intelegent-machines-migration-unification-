"""Decision traces: what an agent chose, and whether it worked.

The log is a table on the same database URL as episodes. Vectors follow that
URL: numpy for SQLite, pgvector for Postgres. ``outcome_stats`` groups
successes and failures per decision point.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from typing import Sequence

from midplane.retrieval.ddl import SQLITE_DECISION_TRACES_DDL
from midplane.retrieval.embedder import Embedder, HashedNgramEmbedder
from midplane.retrieval.index import VectorIndex, apply_postgres_retrieval_ddl, is_postgres_url, open_index
from midplane.retrieval.rerank import LexicalReranker
from midplane.retrieval.retriever import Retriever
from midplane.retrieval.types import Document, RetrievalResult
from midplane.retrieval.weights import trace_prior
from midplane.store import episode_timestamp

_COLUMNS = (
    "trace_id",
    "agent",
    "run_id",
    "episode_id",
    "decision_point",
    "options",
    "choice",
    "rationale",
    "outcome",
    "outcome_score",
    "created_at",
    "updated_at",
)
_OUTCOMES = {"success", "fail", "unknown"}


@dataclass(frozen=True)
class DecisionTrace:
    trace_id: str
    agent: str
    run_id: str | None
    episode_id: str | None
    decision_point: str
    options: list
    choice: str
    rationale: str
    outcome: str
    outcome_score: float | None
    created_at: str
    updated_at: str


def trace_document(trace: DecisionTrace) -> Document:
    options = " ".join(str(option) for option in trace.options)
    text = (
        f"agent {trace.agent} decision {trace.decision_point} choice {trace.choice} "
        f"options {options} rationale {trace.rationale} outcome {trace.outcome}"
    )
    return Document(
        doc_id=f"trace:{trace.trace_id}",
        store="traces",
        text=" ".join(text.split()),
        metadata={
            "store": "traces",
            "trace_id": trace.trace_id,
            "agent": trace.agent,
            "run_id": trace.run_id or "",
            "episode_id": trace.episode_id or "",
            "decision_point": trace.decision_point,
            "choice": trace.choice,
            "outcome": trace.outcome,
            "outcome_score": trace.outcome_score,
            "created_at": trace.created_at,
        },
        fields={
            "agent": trace.agent,
            "decision_point": trace.decision_point,
            "choice": trace.choice,
            "outcome": trace.outcome,
        },
    )


class DecisionTraceStore:
    def __init__(
        self,
        url: str = "sqlite:///:memory:",
        *,
        embedder: Embedder | None = None,
        index: VectorIndex | None = None,
    ) -> None:
        self.url = url
        self._lock = threading.RLock()
        self._conn, self.dialect = _connect(url)
        self.embedder = embedder or HashedNgramEmbedder()
        self.index = index or open_index(url, store_name="traces", dimension=self.embedder.dimension)
        self.retriever = Retriever(
            name="traces",
            embedder=self.embedder,
            index=self.index,
            reranker=LexicalReranker(trace_prior),
        )
        self._reindex_locked()

    def close(self) -> None:
        self.index.close()
        self._conn.close()

    def record(
        self,
        *,
        agent: str,
        run_id: str | None,
        decision_point: str,
        options: Sequence[object],
        choice: str,
        rationale: str,
        outcome: str = "unknown",
        outcome_score: float | None = None,
        episode_id: str | None = None,
        trace_id: str | None = None,
    ) -> DecisionTrace:
        if outcome not in _OUTCOMES:
            raise ValueError("outcome must be success, fail, or unknown")
        if not decision_point or not agent:
            raise ValueError("agent and decision_point are required")
        now = episode_timestamp()
        trace = DecisionTrace(
            trace_id=trace_id or f"trace_{uuid.uuid4().hex}",
            agent=agent,
            run_id=run_id,
            episode_id=episode_id if episode_id is not None else run_id,
            decision_point=decision_point,
            options=list(options),
            choice=choice,
            rationale=rationale,
            outcome=outcome,
            outcome_score=None if outcome_score is None else float(outcome_score),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._insert_locked(trace)
            self._conn.commit()
            self._reindex_locked()
        return trace

    def complete_run(
        self,
        *,
        run_id: str,
        outcome: str,
        outcome_score: float | None,
        choice: str,
        rationale: str,
        agent: str = "episode_writer",
        episode_id: str | None = None,
    ) -> DecisionTrace:
        """Set model-routing rows for this run, then append an episode-completion trace."""

        if outcome not in {"success", "fail"}:
            raise ValueError("a completed run is success or fail")
        now = episode_timestamp()
        with self._lock:
            self._execute(
                """
                update decision_traces
                set outcome = ?, outcome_score = ?, updated_at = ?
                where run_id = ? and decision_point = ?
                """,
                (outcome, outcome_score, now, run_id, "model_routing"),
            )
            trace = DecisionTrace(
                trace_id=f"trace_{uuid.uuid4().hex}",
                agent=agent,
                run_id=run_id,
                episode_id=episode_id or run_id,
                decision_point="episode_completion",
                options=["success", "fail"],
                choice=choice,
                rationale=rationale,
                outcome=outcome,
                outcome_score=None if outcome_score is None else float(outcome_score),
                created_at=now,
                updated_at=now,
            )
            self._insert_locked(trace)
            self._conn.commit()
            self._reindex_locked()
        return trace

    def outcome_stats(self, decision_point: str | None = None) -> list[dict]:
        """What worked and what did not, per decision point.

        A choice is in ``worked`` when it has more successes than failures.
        It is in ``failed`` when it has at least one failure. A mixed choice
        can appear in both.
        """

        sql = "select decision_point, choice, outcome, outcome_score from decision_traces"
        params: tuple = ()
        if decision_point is not None:
            sql += " where decision_point = ?"
            params = (decision_point,)
        with self._lock:
            rows = self._execute(sql, params).fetchall()
        grouped: dict[str, dict[str, dict]] = {}
        for row in rows:
            point, choice, outcome, score = _stats_row(row, self.dialect)
            choices = grouped.setdefault(point, {})
            item = choices.setdefault(
                choice,
                {"success": 0, "fail": 0, "unknown": 0, "scores": []},
            )
            if outcome not in item:
                continue
            item[outcome] += 1
            if score is not None:
                item["scores"].append(float(score))
        results: list[dict] = []
        for point, choices in sorted(grouped.items()):
            counts = {"success": 0, "fail": 0, "unknown": 0}
            worked: list[dict] = []
            failed: list[dict] = []
            scores: list[float] = []
            for choice, item in sorted(choices.items()):
                for key in counts:
                    counts[key] += item[key]
                scores.extend(item["scores"])
                summary = {
                    "choice": choice,
                    "success": item["success"],
                    "fail": item["fail"],
                    "unknown": item["unknown"],
                }
                if item["success"] > item["fail"]:
                    worked.append(summary)
                if item["fail"] > 0:
                    failed.append(summary)
            mean = round(sum(scores) / len(scores), 6) if scores else None
            results.append(
                {
                    "decision_point": point,
                    "counts": counts,
                    "mean_outcome_score": mean,
                    "worked": worked,
                    "failed": failed,
                }
            )
        return results

    def list_traces(self, *, run_id: str | None = None) -> list[DecisionTrace]:
        sql = f"select {', '.join(_COLUMNS)} from decision_traces"
        params: tuple = ()
        if run_id is not None:
            sql += " where run_id = ?"
            params = (run_id,)
        sql += " order by created_at asc, trace_id asc"
        with self._lock:
            rows = self._execute(sql, params).fetchall()
        return [_trace_from_row(row, self.dialect) for row in rows]

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict | None = None,
        candidate_k: int | None = None,
    ) -> list[RetrievalResult]:
        with self._lock:
            self._reindex_locked()
            return self.retriever.retrieve(query, top_k=top_k, filters=filters, candidate_k=candidate_k)

    def _insert_locked(self, trace: DecisionTrace) -> None:
        placeholder = "?::jsonb" if self.dialect == "postgres" else "?"
        columns = ", ".join(_COLUMNS)
        values = ", ".join(placeholder if column == "options" else "?" for column in _COLUMNS)
        self._execute(
            f"insert into decision_traces ({columns}) values ({values})",
            (
                trace.trace_id,
                trace.agent,
                trace.run_id,
                trace.episode_id,
                trace.decision_point,
                json.dumps(trace.options),
                trace.choice,
                trace.rationale,
                trace.outcome,
                trace.outcome_score,
                trace.created_at,
                trace.updated_at,
            ),
        )

    def _reindex_locked(self) -> None:
        rows = self._execute(
            f"select {', '.join(_COLUMNS)} from decision_traces order by created_at asc",
            (),
        ).fetchall()
        documents = [trace_document(_trace_from_row(row, self.dialect)) for row in rows]
        self.retriever.replace(documents)

    def _execute(self, sql: str, params: tuple):
        statement = sql if self.dialect == "sqlite" else sql.replace("?", "%s")
        return self._conn.execute(statement, params)


def _connect(url: str):
    if url in {":memory:", "sqlite:///:memory:"}:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.executescript(SQLITE_DECISION_TRACES_DDL)
        connection.commit()
        return connection, "sqlite"
    if url.startswith("sqlite:///"):
        connection = sqlite3.connect(url[len("sqlite:///") :], check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(SQLITE_DECISION_TRACES_DDL)
        connection.commit()
        return connection, "sqlite"
    if is_postgres_url(url):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Postgres retrieval requires the postgres extra: pip install 'midplane[postgres]'"
            ) from exc
        connection = psycopg.connect(url)
        try:
            apply_postgres_retrieval_ddl(connection)
        except Exception as exc:
            message = str(exc).lower()
            if "vector" in message or "extension" in message:
                raise RuntimeError(
                    "Postgres retrieval needs the pgvector extension. "
                    "Use an image such as pgvector/pgvector:pg16, or install the extension."
                ) from exc
            raise
        return connection, "postgres"
    raise ValueError("decision trace URL must be sqlite:///... or a postgres URL")


def _trace_from_row(row, dialect: str) -> DecisionTrace:
    if dialect == "sqlite":
        data = {column: row[column] for column in _COLUMNS}
    else:
        data = dict(zip(_COLUMNS, row))
    options = data["options"]
    if isinstance(options, str):
        options = json.loads(options)
    score = data["outcome_score"]
    return DecisionTrace(
        trace_id=data["trace_id"],
        agent=data["agent"],
        run_id=data["run_id"],
        episode_id=data["episode_id"],
        decision_point=data["decision_point"],
        options=list(options or []),
        choice=data["choice"],
        rationale=data["rationale"],
        outcome=data["outcome"],
        outcome_score=None if score is None else float(score),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _stats_row(row, dialect: str) -> tuple[str, str, str, float | None]:
    if dialect == "sqlite":
        score = row["outcome_score"]
        return row["decision_point"], row["choice"], row["outcome"], None if score is None else float(score)
    score = row[3]
    return row[0], row[1], row[2], None if score is None else float(score)


