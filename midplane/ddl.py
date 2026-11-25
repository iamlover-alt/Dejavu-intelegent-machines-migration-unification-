"""DDL for the episodes table.

The Postgres statement is the migration body. SQLite stores JSON as text
because it has no jsonb type. Column names match in both dialects.

RECONCILE with episode_schema.sql.
"""

from __future__ import annotations

POSTGRES_EPISODES_DDL = """
create table if not exists episodes (
  run_id         text primary key,
  source         text not null check (source in ('dev_guild', 'trading', 'app')),
  inputs         jsonb not null,
  plan           jsonb not null,
  tool_calls     jsonb not null,
  model_choices  jsonb not null,
  outputs        jsonb not null,
  cost           jsonb not null,
  latency_ms     integer not null,
  outcome        text not null check (outcome in ('success', 'error')),
  quality_score  double precision not null,
  trace          jsonb not null
);
""".strip() + "\n"

SQLITE_EPISODES_DDL = """
create table if not exists episodes (
  run_id         text primary key,
  source         text not null check (source in ('dev_guild', 'trading', 'app')),
  inputs         text not null,
  plan           text not null,
  tool_calls     text not null,
  model_choices  text not null,
  outputs        text not null,
  cost           text not null,
  latency_ms     integer not null,
  outcome        text not null check (outcome in ('success', 'error')),
  quality_score  real not null,
  trace          text not null,
  score_version  text not null default 'stub-v0',
  created_at     text not null default ''
);
"""

# RECONCILE with episode_schema.sql.
# score_version and created_at are guesses. 002_gateway.sql already exists,
# so these indexes live in migrations/003_episode_indexes.sql.
POSTGRES_EPISODE_INDEX_DDL = """
alter table episodes add column if not exists score_version text not null default 'stub-v0';

alter table episodes add column if not exists created_at text not null default '';

create index if not exists episodes_source_quality_score_idx
  on episodes (source, quality_score);

create index if not exists episodes_source_created_at_idx
  on episodes (source, created_at);

create index if not exists episodes_created_at_run_id_idx
  on episodes (created_at, run_id);
""".strip() + "\n"

SQLITE_EPISODE_INDEX_DDL = """
create index if not exists episodes_source_quality_score_idx
  on episodes (source, quality_score);

create index if not exists episodes_source_created_at_idx
  on episodes (source, created_at);

create index if not exists episodes_created_at_run_id_idx
  on episodes (created_at, run_id);
"""
