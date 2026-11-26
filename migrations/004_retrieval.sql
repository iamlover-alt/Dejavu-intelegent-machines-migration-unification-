-- Postgres retrieval migration: decision traces and the pgvector index.
-- SQLite keeps vectors in the in-memory numpy index and does not use this file.
-- HNSW needs pgvector >= 0.5. The docker compose database image is pgvector/pgvector:pg16.
--
-- IVFFlat alternative when HNSW is unavailable (pgvector older than 0.5).
-- Do not create both indexes. Comment kept so the fallback is in the migration:
-- create index if not exists retrieval_vectors_embedding_ivfflat
--   on retrieval_vectors using ivfflat (embedding vector_cosine_ops) with (lists = 100);
--
-- RECONCILE: no artifact in this repo defines decision_traces. If a later
-- episode_schema.sql or integration_guide.md names this log, that file wins.

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
  embedding vector(256) not null,
  metadata jsonb not null,
  primary key (store_name, doc_id)
);

create index if not exists retrieval_vectors_embedding_hnsw
  on retrieval_vectors
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
