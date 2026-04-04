-- Workflow persistence for persist_node (WorkflowRunRecord + WorkflowArtifactRecord)
-- Run this in Supabase: SQL Editor → New query → paste → Run
--
-- Prerequisites:
--   - Project created at https://supabase.com
--   - Use service_role key in .env.mcp (backend MCP server) for inserts
--   - pip install supabase  (in the venv that runs MCP + agents)

-- ---------------------------------------------------------------------------
-- workflow_runs — one row per graph.invoke()
-- Columns match app.memory.schemas.WorkflowRunRecord.model_dump()
-- ---------------------------------------------------------------------------
create table if not exists public.workflow_runs (
  run_id          text primary key,
  workflow_type   text        not null,
  status          text        not null,
  item_count      integer,
  quality_score   double precision,
  iteration_count integer     not null default 0,
  errors          jsonb       not null default '[]'::jsonb,
  metadata        jsonb       not null default '{}'::jsonb,
  created_at      timestamptz not null default now()
);

create index if not exists idx_workflow_runs_workflow_type
  on public.workflow_runs (workflow_type);
create index if not exists idx_workflow_runs_created_at
  on public.workflow_runs (created_at desc);

comment on table public.workflow_runs is 'MAS persist_node: one row per agent run';

-- ---------------------------------------------------------------------------
-- workflow_artifacts — structured workflow_data + optional future embedding
-- Columns match app.memory.schemas.WorkflowArtifactRecord.model_dump()
-- ---------------------------------------------------------------------------
create table if not exists public.workflow_artifacts (
  id              uuid primary key default gen_random_uuid(),
  run_id          text        not null,
  artifact_type   text        not null,
  data            jsonb       not null default '{}'::jsonb,
  embedding       jsonb,     -- reserved; null until you add vector search
  created_at      timestamptz not null default now()
);

create index if not exists idx_workflow_artifacts_run_id
  on public.workflow_artifacts (run_id);

comment on table public.workflow_artifacts is 'MAS persist_node: workflow_data blob per run';

-- ---------------------------------------------------------------------------
-- Row Level Security (optional)
-- Service role bypasses RLS. If you ever use anon key, add policies here.
-- For server-only service_role: you can leave RLS disabled on these tables.
-- ---------------------------------------------------------------------------
-- alter table public.workflow_runs enable row level security;
-- alter table public.workflow_artifacts enable row level security;
