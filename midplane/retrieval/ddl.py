"""DDL for decision traces and the pgvector index.

SQLite stores the trace log and keeps vectors in the numpy index. Postgres
stores both. The executable Postgres script is migrations/004_retrieval.sql.

RECONCILE: no artifact in this repo defines decision_traces. If a later
episode_schema.sql or integration_guide.md names this log, that file wins.
"""

from __future__ import annotations

from midplane.retrieval.embedder import EMBEDDING_DIM

POSTGRES_RETRIEVAL_DDL = f"""
create extension if not exists vector;

create table if not exists decision_traces (
  trace_id text primary key,
  agent text not null,
  run_id text,
  episode_id text,
  decision_point text not null,
  options jsonb not null,
  choice text not null,
  rationale text not null,
  outcome text not null check (outcome in ('success', 'fail', 'unknown')),
  outcome_score double precision,
  created_at text not null,
  updated_at text not null
);

create index if not exists decision_traces_point_outcome_idx
  on decision_traces (decision_point, outcome);

create index if not exists decision_traces_run_id_idx
  on decision_traces (run_id);

create table if not exists retrieval_vectors (
  store_name text not null,
  doc_id text not null,
  embedding vector({EMBEDDING_DIM}) not null,
  metadata jsonb not null,
  primary key (store_name, doc_id)
);

create index if not exists retrieval_vectors_embedding_hnsw
  on retrieval_vectors
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
""".strip() + "\n"

SQLITE_DECISION_TRACES_DDL = """
create table if not exists decision_traces (
  trace_id text primary key,
  agent text not null,
  run_id text,
  episode_id text,
  decision_point text not null,
  options text not null,
  choice text not null,
  rationale text not null,
  outcome text not null check (outcome in ('success', 'fail', 'unknown')),
  outcome_score real,
  created_at text not null,
  updated_at text not null
);

create index if not exists decision_traces_point_outcome_idx
  on decision_traces (decision_point, outcome);

create index if not exists decision_traces_run_id_idx
  on decision_traces (run_id);
"""
