"""Vector indexes. Numpy in memory, pgvector when the URL is Postgres.

Backend selection matches EpisodeStore: sqlite and tests use the numpy index.
A postgres or postgresql URL uses pgvector. The HNSW index is created with the
table. Search orders by cosine distance. On a small table Postgres usually
scans it exactly, which is what the parity test compares.
"""

from __future__ import annotations

import json
from typing import Mapping, Protocol, Sequence

import numpy as np

from midplane.retrieval.ddl import POSTGRES_RETRIEVAL_DDL
from midplane.retrieval.embedder import EMBEDDING_DIM
from midplane.retrieval.types import Neighbor, metadata_matches, validate_filters


class VectorIndex(Protocol):
    dimension: int

    def replace_all(self, rows: Sequence[tuple[str, Sequence[float], Mapping]]) -> None:
        """Replace this store's vectors."""

    def search(
        self,
        vector: Sequence[float],
        k: int,
        filters: Mapping | None = None,
    ) -> list[Neighbor]:
        """Return up to ``k`` neighbors, highest cosine first, after filters."""

    def close(self) -> None:
        """Release a database connection. The numpy index has nothing to close."""


def is_postgres_url(url: str | None) -> bool:
    return bool(url) and (url.startswith("postgres://") or url.startswith("postgresql://"))


def open_index(url: str | None, *, store_name: str, dimension: int) -> VectorIndex:
    """Pick the index the same way EpisodeStore picks its database."""

    if is_postgres_url(url):
        return PgVectorIndex(url or "", store_name=store_name, dimension=dimension)
    return InMemoryVectorIndex(dimension)


def _as_vector(values: Sequence[float], dimension: int) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != dimension:
        raise ValueError(f"expected {dimension} dimensions, got {len(vector)}")
    return vector


class InMemoryVectorIndex:
    """Cosine search over one process-local matrix."""

    def __init__(self, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self.dimension = dimension
        self._ids: list[str] = []
        self._metadata: list[dict] = []
        self._matrix: np.ndarray | None = None

    def replace_all(self, rows: Sequence[tuple[str, Sequence[float], Mapping]]) -> None:
        ids: list[str] = []
        metadata: list[dict] = []
        vectors: list[list[float]] = []
        for doc_id, vector, meta in rows:
            ids.append(doc_id)
            metadata.append(dict(meta))
            vectors.append(_as_vector(vector, self.dimension))
        self._ids = ids
        self._metadata = metadata
        self._matrix = np.asarray(vectors, dtype=np.float64) if vectors else None

    def search(
        self,
        vector: Sequence[float],
        k: int,
        filters: Mapping | None = None,
    ) -> list[Neighbor]:
        if k < 1 or self._matrix is None or not self._ids:
            return []
        active = validate_filters(filters)
        query = np.asarray(_as_vector(vector, self.dimension), dtype=np.float64)
        scores = self._matrix @ query
        order = np.argsort(-scores)
        hits: list[Neighbor] = []
        for index in order:
            position = int(index)
            meta = self._metadata[position]
            if not metadata_matches(meta, active):
                continue
            hits.append(
                Neighbor(
                    doc_id=self._ids[position],
                    score=float(scores[position]),
                    metadata=meta,
                )
            )
            if len(hits) >= k:
                break
        return hits

    def close(self) -> None:
        return None


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def apply_postgres_retrieval_ddl(connection) -> None:
    for statement in POSTGRES_RETRIEVAL_DDL.split(";"):
        sql = statement.strip()
        if sql:
            connection.execute(sql)
    connection.commit()


class PgVectorIndex:
    """One ``store_name`` slice of ``retrieval_vectors``.

    The column is ``vector(EMBEDDING_DIM)``. Other dimensions are rejected so
    a mismatched model cannot write a silent cast error later.
    """

    def __init__(self, dsn: str, *, store_name: str, dimension: int = EMBEDDING_DIM) -> None:
        if dimension != EMBEDDING_DIM:
            raise ValueError(
                f"pgvector column is vector({EMBEDDING_DIM}); got dimension {dimension}. "
                "A new model size needs a new migration."
            )
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Postgres retrieval requires the postgres extra: pip install 'midplane[postgres]'"
            ) from exc
        self.dimension = dimension
        self.store_name = store_name
        self._dsn = dsn
        try:
            self._conn = psycopg.connect(dsn)
            apply_postgres_retrieval_ddl(self._conn)
        except Exception as exc:
            message = str(exc).lower()
            if "vector" in message or "extension" in message:
                raise RuntimeError(
                    "Postgres retrieval needs the pgvector extension. "
                    "Use an image such as pgvector/pgvector:pg16, or install the extension."
                ) from exc
            raise

    def close(self) -> None:
        self._conn.close()

    def replace_all(self, rows: Sequence[tuple[str, Sequence[float], Mapping]]) -> None:
        self._conn.execute(
            "delete from retrieval_vectors where store_name = %s",
            (self.store_name,),
        )
        for doc_id, vector, meta in rows:
            literal = _vector_literal(_as_vector(vector, self.dimension))
            self._conn.execute(
                """
                insert into retrieval_vectors (store_name, doc_id, embedding, metadata)
                values (%s, %s, %s::vector, %s::jsonb)
                """,
                (self.store_name, doc_id, literal, json.dumps(dict(meta))),
            )
        self._conn.commit()

    def search(
        self,
        vector: Sequence[float],
        k: int,
        filters: Mapping | None = None,
    ) -> list[Neighbor]:
        if k < 1:
            return []
        active = validate_filters(filters)
        literal = _vector_literal(_as_vector(vector, self.dimension))
        clauses = ["store_name = %s"]
        params: list = [self.store_name]
        clauses.extend(_filter_clauses(active, params))
        where = " and ".join(clauses)
        params.append(literal)
        params.append(k)
        # Cosine distance is 1 - cosine_similarity for normalized vectors.
        rows = self._conn.execute(
            f"""
            select doc_id, metadata, 1 - (embedding <=> %s::vector) as score
            from retrieval_vectors
            where {where}
            order by embedding <=> %s::vector
            limit %s
            """,
            [literal, *params],
        ).fetchall()
        hits: list[Neighbor] = []
        for doc_id, metadata, score in rows:
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            hits.append(Neighbor(doc_id=str(doc_id), score=float(score), metadata=dict(metadata)))
        return hits


def _filter_clauses(filters: Mapping, params: list) -> list[str]:
    clauses: list[str] = []
    if "run_ids" in filters:
        run_ids = list(filters["run_ids"])
        if not run_ids:
            clauses.append("false")
        else:
            marks = ", ".join(["%s"] * len(run_ids))
            clauses.append(f"metadata->>'run_id' in ({marks})")
            params.extend(run_ids)
    if "min_score" in filters:
        clauses.append("(metadata->>'quality_score')::double precision >= %s")
        params.append(float(filters["min_score"]))
    if "since" in filters:
        clauses.append("coalesce(metadata->>'created_at', '') >= %s")
        params.append(filters["since"])
    if "until" in filters:
        clauses.append("coalesce(metadata->>'created_at', '') <= %s")
        params.append(filters["until"])
    for key in (
        "source",
        "outcome",
        "status",
        "kind",
        "decision_point",
        "agent",
        "name",
        "table",
        "method",
        "path",
        "chunk",
    ):
        if key in filters:
            clauses.append(f"metadata->>'{key}' = %s")
            params.append(filters[key])
    return clauses
