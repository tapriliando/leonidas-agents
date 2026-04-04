-- Per-user workflow history for memory loader + API identity
-- Run after 001_workflow_tables.sql

alter table if exists public.workflow_runs
  add column if not exists user_id text;

create index if not exists idx_workflow_runs_user_id_created
  on public.workflow_runs (user_id, created_at desc);

comment on column public.workflow_runs.user_id is 'Optional API user id for memory loader filters';
