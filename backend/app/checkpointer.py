"""
checkpointer.py — LangGraph persistence for human-in-the-loop (interrupt/resume).

Development: MemorySaver when no Postgres DSN is available.
Production / durable: AsyncPostgresSaver using a Postgres connection string.

Postgres DSN resolution (first match wins):
  1. DATABASE_URL
  2. SUPABASE_DATABASE_URL (paste from Supabase Dashboard → Database → URI)
  3. SUPABASE_DB_PASSWORD + SUPABASE_URL — builds
     postgresql://postgres:PASSWORD@db.<project_ref>.supabase.co:5432/postgres

Note: SUPABASE_DB_PASSWORD is the database password from Project Settings → Database,
not the anon or service_role API keys.
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus, urlparse
from typing import Any


def resolve_postgres_dsn() -> str | None:
    """
    Return a libpq connection string for LangGraph Postgres checkpointers, or None.

    Supabase projects are Postgres; use the direct connection string from the dashboard
    or password + project URL from your existing Supabase env.
    """
    direct = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DATABASE_URL")
    if direct and direct.strip():
        return direct.strip()

    password = os.getenv("SUPABASE_DB_PASSWORD")
    supabase_url = os.getenv("SUPABASE_URL")
    if not password or not supabase_url:
        return None

    ref = _supabase_project_ref(supabase_url.strip())
    if not ref:
        return None

    # Direct connection (port 5432). Use pooler URI in SUPABASE_DATABASE_URL if you prefer.
    user = quote_plus("postgres")
    pwd = quote_plus(password.strip())
    return f"postgresql://{user}:{pwd}@db.{ref}.supabase.co:5432/postgres"


def _supabase_project_ref(supabase_url: str) -> str | None:
    """Extract project ref from https://<ref>.supabase.co."""
    host = (urlparse(supabase_url).hostname or "").lower()
    if not host.endswith(".supabase.co"):
        return None
    ref = host[: -len(".supabase.co")]
    if not ref or "." in ref:
        return None
    return ref


def get_memory_checkpointer() -> Any:
    """Return a fresh in-memory checkpointer."""
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


def get_checkpointer(env: str | None = None) -> Any:
    """
    Default checkpointer for CLI and tests: always MemorySaver.

    The ``env`` argument is reserved for future routing; production API servers
    should compile the meta graph inside lifespan with AsyncPostgresSaver instead.
    """
    _ = env  # noqa: F841 — reserved for future policy hooks
    return get_memory_checkpointer()


def postgres_checkpointer_sync():
    """
    Sync Postgres checkpointer context manager (CLI / one-off scripts).

    Usage::

        with postgres_checkpointer_sync() as cp:
            g = build_meta_graph(cp)
            g.invoke(...)
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    url = resolve_postgres_dsn()
    if not url:
        raise RuntimeError(
            "No Postgres DSN: set DATABASE_URL, SUPABASE_DATABASE_URL, or "
            "SUPABASE_DB_PASSWORD + SUPABASE_URL"
        )
    return PostgresSaver.from_conn_string(url)
