-- RECONCILE with episode_schema.sql.
-- score_version and created_at are not in the first-slice illustrative table.
-- They are guesses for export and listing. episode_schema.sql wins if it differs.
-- Numbered 003 because migrations/002_gateway.sql already creates api_keys and runs.

alter table episodes add column if not exists score_version text not null default 'stub-v0';

alter table episodes add column if not exists created_at text not null default '';

create index if not exists episodes_source_quality_score_idx
  on episodes (source, quality_score);

create index if not exists episodes_source_created_at_idx
  on episodes (source, created_at);

create index if not exists episodes_created_at_run_id_idx
  on episodes (created_at, run_id);
