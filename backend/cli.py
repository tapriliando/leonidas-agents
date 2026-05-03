#!/usr/bin/env python3
"""
cli.py — Interactive terminal runner for the multi-agent system.

USAGE (from project root):
  # Single query
  python backend/cli.py "analyze customer complaints about delivery this week"

  # Interactive REPL mode
  python backend/cli.py

  # Contributor / ops commands (no graph run)
  python backend/cli.py onboard
  python backend/cli.py doctor
  python backend/cli.py agents
  python backend/cli.py validate
  python backend/cli.py quickstart

PREREQUISITES:
  1. Fill in .env.backend  (OPENAI_API_KEY required; others optional)
  2. Start the MCP server in another terminal:
       cd <repo-root>/leonidas-agents
       uvicorn mcp_server.main:app --host 127.0.0.1 --port 8001

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

import argparse
import asyncio
import json
import os
import sys
import textwrap
import time
import uuid
from pathlib import Path
import urllib.error
import urllib.request


# ── Env loader ──────────────────────────────────────────────────────────────

def _load_env(path: str, *, quiet_if_missing: bool = False) -> None:
    """
    Minimal .env file parser — no external dependencies needed.
    Skips comments and blank lines. Handles quoted values.
    """
    env_path = Path(path)
    if not env_path.exists():
        if not quiet_if_missing:
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
    _load_env(str(project_root / ".env.mcp"), quiet_if_missing=True)

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


def _upsert_env_var(path: Path, key: str, value: str) -> None:
    """
    Insert or update a KEY=value entry in a .env-like file.

    Keeps existing comments/order where possible, appends missing keys.
    """
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    updated = False
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


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


def _repo_root() -> Path:
    """Directory containing pyproject.toml (parent of backend/)."""
    return Path(__file__).resolve().parent.parent


def _verify_repo_layout() -> bool:
    root = _repo_root()
    if not (root / "pyproject.toml").is_file():
        print(
            "\n❌  This repository layout looks incomplete.\n"
            f"   Expected pyproject.toml at: {root / 'pyproject.toml'}\n\n"
            "   Use the full project root (folder that contains backend/, "
            "mcp_server/, pyproject.toml), then retry.\n"
        )
        return False
    return True


def _ensure_langgraph_installed() -> bool:
    try:
        import langgraph  # noqa: F401

        return True
    except ImportError:
        root = _repo_root()
        exe = sys.executable
        print(
            "\n❌  Missing dependency: langgraph\n\n"
            "   Install project extras from the repo root:\n"
            f"      cd {root}\n"
            f'      "{exe}" -m pip install -e ".[api,dev]"'
            "\n\n"
            "   Confirm the same interpreter sees langgraph:\n"
            f'      "{exe}" -c "import langgraph; print(langgraph.__file__)"\n'
        )
        return False


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
    if not _verify_repo_layout():
        return
    if not _ensure_langgraph_installed():
        return
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


# ── Contributor commands ────────────────────────────────────────────────────


def _cmd_onboard(_args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parent.parent
    env = root / ".env.backend"
    example = root / ".env.backend.example"
    if not env.exists():
        text = (
            example.read_text(encoding="utf-8")
            if example.exists()
            else "OPENAI_API_KEY=\nMCP_SERVER_URL=http://localhost:8001\nREDIS_URL=\n"
        )
        env.write_text(text, encoding="utf-8")
        print(f"Created {env}")
    else:
        print(".env.backend already exists.")

    key_from_flag = (_args.openai_key or "").strip()
    if key_from_flag:
        _upsert_env_var(env, "OPENAI_API_KEY", key_from_flag)
        print("Saved OPENAI_API_KEY from --openai-key.")
    elif not _args.non_interactive:
        current = ""
        _load_env(str(env))
        current = os.environ.get("OPENAI_API_KEY", "")
        if not current:
            print("\nOPENAI_API_KEY is required for LLM calls.")
            entered = input("Paste OPENAI_API_KEY now (or press Enter to skip): ").strip()
            if entered:
                _upsert_env_var(env, "OPENAI_API_KEY", entered)
                print("Saved OPENAI_API_KEY.")

    # Keep sensible defaults for first-time runs.
    _upsert_env_var(env, "MCP_SERVER_URL", os.getenv("MCP_SERVER_URL", "http://localhost:8001"))

    mcp_env = root / ".env.mcp"
    mcp_example = root / ".env.mcp.example"
    if not mcp_env.exists() and mcp_example.exists():
        mcp_env.write_text(mcp_example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Created optional {mcp_env} from .env.mcp.example (you can leave keys blank).")

    print(
        "Next: start MCP in another terminal:\n"
        "     uvicorn mcp_server.main:app --host 127.0.0.1 --port 8001\n"
        "   (.env.mcp is optional — loaded automatically when present.)\n"
        "Then run: python backend/cli.py doctor"
    )


def _cmd_doctor(_args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parent.parent
    _load_env(str(root / ".env.backend"))
    _load_env(str(root / ".env.mcp"), quiet_if_missing=True)
    print("Leonidas doctor\n")
    print(
        "  Repo layout:",
        "OK (pyproject.toml found)" if (root / "pyproject.toml").is_file() else "MISSING pyproject.toml",
    )
    try:
        import langgraph  # noqa: F401

        print("  langgraph: OK")
    except ImportError:
        print('  langgraph: MISSING — pip install -e ".[api,dev]" from repo root')
    key = os.environ.get("OPENAI_API_KEY", "")
    print("  OPENAI_API_KEY:", "set" if key and not key.startswith("your-") else "MISSING")
    mcp = os.environ.get("MCP_SERVER_URL", "http://localhost:8001").rstrip("/")
    health = f"{mcp}/health"
    try:
        urllib.request.urlopen(health, timeout=3).read()
        print(f"  MCP health ({health}): OK")
    except (urllib.error.URLError, OSError) as exc:
        print(f"  MCP health ({health}): FAIL ({exc})")
    print("\nDone.")


def _cmd_quickstart(_args: argparse.Namespace) -> None:
    """
    Guided under-10-minute onboarding:
      1) ensure .env.backend exists (+ optional key)
      2) validate markdown agents
      3) run doctor checks
      4) print exact next command to run first query
    """
    print("Leonidas quickstart (target: under 10 minutes)\n")
    _cmd_onboard(_args)
    print()
    _cmd_validate(_args)
    print()
    _cmd_doctor(_args)
    print(
        "\nReady. Run from repo root:\n"
        '  python backend/cli.py "Explain LangGraph in 3 bullets."\n'
        "Or start API:\n"
        "  uvicorn app.api.main:app --app-dir backend --reload\n"
    )


def _cmd_agents(_args: argparse.Namespace) -> None:
    _ensure_import_paths()
    from app.registry import AGENT_REGISTRY, AGENT_DEFINITIONS

    print("Registered agents:\n")
    for aid in sorted(AGENT_REGISTRY.keys()):
        row = AGENT_REGISTRY[aid]
        tag = "markdown" if aid in AGENT_DEFINITIONS else "yaml"
        print(f"  [{tag}] {aid}: {row.get('purpose', '')[:120]}")
    print(f"\nTotal: {len(AGENT_REGISTRY)} ({len(AGENT_DEFINITIONS)} from Markdown)")


def _cmd_validate(_args: argparse.Namespace) -> None:
    _ensure_import_paths()
    from app.registry import agents_markdown_dir, refresh_registry
    from app.registry_markdown import validate_all_markdown_agents

    errs = validate_all_markdown_agents(agents_markdown_dir())
    if errs:
        print("Validation FAILED:\n")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    refresh_registry()
    print("All Markdown agent files are valid.")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Leonidas multi-agent CLI")
    sub = p.add_subparsers(dest="command")

    onboard = sub.add_parser("onboard", help="Create/prepare .env.backend for first run")
    onboard.add_argument(
        "--openai-key",
        default="",
        help="Set OPENAI_API_KEY in .env.backend non-interactively",
    )
    onboard.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt for input",
    )
    sub.add_parser("doctor", help="Check env and MCP reachability")
    sub.add_parser("agents", help="List registered agents (YAML + Markdown)")
    sub.add_parser("validate", help="Validate Markdown agent definitions")
    quickstart = sub.add_parser("quickstart", help="Run full first-time setup checks")
    quickstart.add_argument(
        "--openai-key",
        default="",
        help="Set OPENAI_API_KEY in .env.backend during quickstart",
    )
    quickstart.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt for input",
    )

    return p


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in ("onboard", "doctor", "agents", "validate", "quickstart"):
        parser = _build_arg_parser()
        args = parser.parse_args(argv)
        if args.command == "onboard":
            _cmd_onboard(args)
        elif args.command == "doctor":
            _cmd_doctor(args)
        elif args.command == "agents":
            _cmd_agents(args)
        elif args.command == "validate":
            _cmd_validate(args)
        elif args.command == "quickstart":
            _cmd_quickstart(args)
        return

    _setup_env()

    if argv:
        query = " ".join(argv)
        asyncio.run(_run_query(query))
    else:
        asyncio.run(_repl())


if __name__ == "__main__":
    main()
