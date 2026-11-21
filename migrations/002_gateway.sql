-- Postgres tables for gateway API keys and run records.
-- RECONCILE with integration_guide.md if that document already defines this store.

create table if not exists api_keys (
  key_id text primary key,
  key_hash text not null unique,
  key_prefix text not null,
  client_id text not null,
  created_at text not null,
  revoked_at text
);

create table if not exists runs (
  run_id text primary key,
  client_id text not null,
  client_ref text,
  task_type text not null,
  input_json jsonb not null,
  status text not null check (status in ('queued', 'running', 'succeeded', 'failed')),
  result_json jsonb,
  episode_id text,
  error_message text,
  events_json jsonb not null,
  created_at text not null,
  updated_at text not null
);
