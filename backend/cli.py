#!/usr/bin/env python3
"""
cli.py — Interactive terminal runner for the multi-agent system.

USAGE (from project root):
  # Single query
  python backend/cli.py "analyze customer complaints about delivery this week"

  # Interactive REPL mode
  python backend/cli.py

PREREQUISITES:
  1. Fill in .env.backend  (OPENAI_API_KEY required; others optional)
  2. Start the MCP server in another terminal:
       cd d:/Inway/mas/project
       uvicorn mcp_server.main:app --port 8001 --env-file .env.mcp

WHAT RUNS WITHOUT SUPABASE:
  intent_node    ✅ LLM only
  planner_node   ✅ LLM only
  scraper_agent  ✅ web_search via DuckDuckGo (no key needed) or Tavily
  enrichment     ✅ web_search
  assigner       ✅ LLM only
  analytics      ✅ pure computation
  report_node    ✅ pure computation
  persist_node   ⚠️  writes to Supabase — fails gracefully, logs error
  summarize_node ✅ LLM only (for complaint_analysis)
  suggest_node   ✅ LLM only
  fetch_node     ⚠️  reads from Supabase — fails gracefully (triggers retry→fail)

RECOMMENDED FIRST TEST WORKFLOW:
  "find 10 coffee shops in Jakarta and rank them for our distribution"
  → routes to lead_gen → scraper (DuckDuckGo) → enrich → score → report

HUMAN APPROVAL (Phase 6):
  If intent/planner sets constraints.require_approval=true (e.g. content_generation),
  the graph will pause at interrupt() until you POST approve/reject via the API
  (see app.api.workflows). CLI invoke stops at the interrupt payload in the result.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
import time
import uuid
from pathlib import Path


# ── Env loader ──────────────────────────────────────────────────────────────

def _load_env(path: str) -> None:
    """
    Minimal .env file parser — no external dependencies needed.
    Skips comments and blank lines. Handles quoted values.
    """
    env_path = Path(path)
    if not env_path.exists():
        print(f"  [warn] .env file not found: {path}")
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and value not in ("...", "your-key-here", "your-supabase-key"):
                os.environ.setdefault(key, value)


def _setup_env() -> None:
    """Load env files from the project root (backend reads both)."""
    project_root = Path(__file__).resolve().parent.parent
    _load_env(str(project_root / ".env.backend"))
    _load_env(str(project_root / ".env.mcp"))

    # Validate required vars
    missing = []
    if not os.environ.get("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if missing:
        print("\n❌  Missing required environment variables:")
        for m in missing:
            print(f"   • {m}")
        print("\n   Edit .env.backend and fill in the missing values.\n")
        sys.exit(1)


# ── Pretty printer ──────────────────────────────────────────────────────────

def _separator(title: str = "", width: int = 70) -> None:
    if title:
        pad = (width - len(title) - 2) // 2
        print("─" * pad + f" {title} " + "─" * (width - pad - len(title) - 2))
    else:
        print("─" * width)


def _print_state_summary(result: dict) -> None:
    """Pretty-print the most useful fields from the final AgentState."""
    _separator("INTENT + PLAN")
    print(f"  Goal        : {result.get('goal') or '—'}")
    print(f"  Workflow    : {result.get('workflow_type') or '—'}")
    print(f"  Department  : {result.get('department') or '—'}")
    plan = result.get("workflow_plan") or []
    if plan:
        print(f"  Plan        : {' → '.join(plan)}")
    cst = result.get("constraints") or {}
    if cst:
        print(f"  Constraints : {json.dumps(cst, ensure_ascii=False)}")

    _separator("METRICS")
    metrics = result.get("metrics") or {}
    item_count     = metrics.get("item_count")
    quality_score  = metrics.get("quality_score")
    confidence     = metrics.get("confidence")
    custom         = metrics.get("custom") or {}
    if item_count is not None:
        print(f"  Items processed : {item_count}")
    if quality_score is not None:
        print(f"  Quality score   : {quality_score:.3f}")
    if confidence is not None:
        print(f"  Confidence      : {confidence:.3f}")
    for k, v in custom.items():
        print(f"  {k.replace('_', ' ').title():<16}: {v}")

    errors = result.get("errors") or []
    if errors:
        _separator("ERRORS")
        for e in errors:
            print(f"  ⚠  {e}")

    _separator("REPORT")
    artifacts = result.get("artifacts") or {}
    report = artifacts.get("report")
    workflow_data = artifacts.get("workflow_data") or {}

    if report:
        # Indent the report for readability
        for line in report.splitlines():
            print("  " + line)
    elif workflow_data:
        # No report yet — show raw keys available
        print(f"  workflow_data keys: {list(workflow_data.keys())}")
        # Show a summary preview
        summary = workflow_data.get("summary") or {}
        if summary:
            overview = summary.get("overview") or ""
            if overview:
                for line in textwrap.wrap(overview, width=66):
                    print(f"  {line}")
        items = workflow_data.get("items") or []
        if items and not summary:
            print(f"  {len(items)} items fetched (first 3 shown):")
            for item in items[:3]:
                name = item.get("name") or item.get("id") or str(item)[:60]
                print(f"    • {name}")
    else:
        status = result.get("status") or "unknown"
        print(f"  No output produced (status: {status}).")

    _separator()
    final_status = result.get("status") or "unknown"
    icon = "✅" if final_status == "completed" else "⚠️ " if errors else "❌"
    print(f"  {icon}  Final status: {final_status.upper()}")
    _separator()


# ── Graph runner ────────────────────────────────────────────────────────────

def _ensure_import_paths() -> None:
    """
    Ensure both `app` (under backend/) and `mcp_server` (repo root) are importable.

    `mcp_client` imports `mcp_server.contracts`; setuptools only packages `backend/`,
    so running `python backend/cli.py` requires the project root on sys.path.
    """
    backend_dir = Path(__file__).resolve().parent
    project_root = backend_dir.parent
    for p in (backend_dir, project_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


async def _run_query(query: str) -> None:
    """Build the meta graph and invoke it with the user query."""
    # Late import so env is already loaded before importing LangChain / app modules
    _ensure_import_paths()

    from app.graph.base_graph import build_meta_graph
    from app.state import make_initial_state

    run_id = str(uuid.uuid4())

    print(f"\n  Run ID : {run_id}")
    print(f"  Query  : {query!r}")
    _separator()

    print("  Building graph …")
    try:
        graph = build_meta_graph()
    except Exception as exc:
        print(f"  ❌  Graph build failed: {exc}")
        return

    initial_state = make_initial_state(user_query=query, run_id=run_id)
    # Required whenever the graph is compiled with a checkpointer (Phase 6+).
    lg_config = {"configurable": {"thread_id": run_id}}

    print("  Invoking agents …\n")
    t0 = time.perf_counter()
    try:
        result = await graph.ainvoke(initial_state, lg_config)
    except Exception as exc:
        print(f"  ❌  Graph invocation failed: {exc}")
        import traceback
        traceback.print_exc()
        return

    elapsed = time.perf_counter() - t0
    print(f"\n  Completed in {elapsed:.1f}s")

    _print_state_summary(result)


# ── REPL ────────────────────────────────────────────────────────────────────

async def _repl() -> None:
    print("\n" + "═" * 70)
    print("  Multi-Agent System — Terminal Interface")
    print("  Type your query and press Enter. Ctrl+C or 'quit' to exit.")
    print("═" * 70)

    while True:
        try:
            print()
            query = input("  You > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Goodbye.\n")
            break

        if not query:
            continue
        if query.lower() in {"quit", "exit", "q"}:
            print("  Goodbye.\n")
            break

        await _run_query(query)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    _setup_env()

    if len(sys.argv) > 1:
        # Single query from command line args
        query = " ".join(sys.argv[1:])
        asyncio.run(_run_query(query))
    else:
        # Interactive REPL
        asyncio.run(_repl())


if __name__ == "__main__":
    main()
