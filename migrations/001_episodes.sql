-- Postgres migration for the first-slice episodes table.
-- RECONCILE with episode_schema.sql. That file is the table of record.
-- Column set follows the illustrative DDL in docs/architecture/first-slice.md.

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
