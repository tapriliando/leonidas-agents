"""
FastAPI entrypoint: middleware, routers, lifespan (graph + Redis + optional Postgres checkpointer).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.cache.redis_cache import close_redis, init_redis
from app.checkpointer import resolve_postgres_dsn
from app.graph.base_graph import build_meta_graph
from app.middleware import RequestContextMiddleware

logger = logging.getLogger("mas.api")


def _load_project_env() -> None:
    root = Path(__file__).resolve().parents[3]
    load_dotenv(root / ".env.backend")
    load_dotenv(root / ".env.mcp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_project_env()
    await init_redis(os.getenv("REDIS_URL"))

    try:
        db_url = resolve_postgres_dsn()
        force_memory = os.getenv("USE_MEMORY_CHECKPOINTER", "").lower() in (
            "1",
            "true",
            "yes",
        )
        # Postgres when we have a DSN: DATABASE_URL, SUPABASE_DATABASE_URL, or
        # SUPABASE_DB_PASSWORD + SUPABASE_URL (see app.checkpointer.resolve_postgres_dsn).
        use_postgres = bool(db_url) and not force_memory

        if use_postgres:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            logger.info("Using AsyncPostgresSaver checkpointer (Postgres / Supabase)")
            async with AsyncPostgresSaver.from_conn_string(db_url) as cp:
                await cp.setup()
                app.state.graph = build_meta_graph(cp)
                yield
        else:
            from app.checkpointer import get_memory_checkpointer

            logger.info("Using MemorySaver checkpointer (no Postgres DSN or forced memory)")
            cp = get_memory_checkpointer()
            app.state.graph = build_meta_graph(cp)
            yield
    finally:
        await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI I-Bridge MAS API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.api import routes, workflows

    app.include_router(routes.router)
    app.include_router(workflows.router)
    return app


app = create_app()
