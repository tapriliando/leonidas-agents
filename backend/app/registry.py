"""
registry.py — Dynamic agent, tool, and workflow registry.

HOW IT WORKS:
  This module reads all YAML files from docs/registry/ at import time.
  The planner node calls format_agents_for_planner() to get a plain-text
  list of available agents injected into its LLM prompt.

  When someone adds a new YAML file under docs/registry/agents/, the
  planner automatically knows about the new agent on the next run.
  No Python code changes are needed.

HOW TO ADD A NEW AGENT (non-technical):
  1. Copy docs/registry/agents/_template.yaml
  2. Fill in the fields (agent_id, purpose, workflow_types, tools)
  3. Save it as docs/registry/agents/<your_agent_id>.yaml
  That's it. The planner will include it automatically.

HOW TO ADD A NEW TOOL (non-technical):
  1. Copy docs/registry/tools/_template.yaml
  2. Fill in the fields
  3. Save it as docs/registry/tools/<your_tool_id>.yaml
  (A developer still needs to implement the handler in mcp-server/tools/)

HOW TO ADD A NEW WORKFLOW (non-technical):
  1. Copy docs/registry/workflows/_template.yaml
  2. Fill in the fields
  3. Save it as docs/registry/workflows/<workflow_type>.yaml
  (A developer still needs to wire the graph in graph/workflows/)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Registry root — resolves to docs/registry/ relative to this file
# ---------------------------------------------------------------------------

_REGISTRY_ROOT = Path(__file__).resolve().parents[2] / "docs" / "registry"


def _load_yaml_dir(subdir: str) -> dict[str, dict[str, Any]]:
    """
    Loads all non-template YAML files from docs/registry/<subdir>/.
    Returns a dict keyed by the id field in each file.

    Falls back to an empty dict if PyYAML is not installed or the folder
    doesn't exist yet — keeps the system runnable before Phase 2.
    """
    folder = _REGISTRY_ROOT / subdir
    if not folder.exists():
        return {}

    try:
        import yaml  # optional until Phase 2
    except ImportError:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for path in sorted(folder.glob("*.yaml")):
        if path.stem.startswith("_"):
            continue  # skip _template.yaml
        try:
            with open(path, encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f) or {}
            # use agent_id / tool_id / workflow_type / skill_id as key
            key = (
                data.get("agent_id")
                or data.get("tool_id")
                or data.get("workflow_type")
                or data.get("skill_id")
                or path.stem
            )
            result[key] = data
        except Exception:
            pass  # skip malformed files silently — don't break the system

    return result


# ---------------------------------------------------------------------------
# Public registries — loaded once at import time
# ---------------------------------------------------------------------------

AGENT_REGISTRY: dict[str, dict[str, Any]] = _load_yaml_dir("agents")
TOOL_REGISTRY: dict[str, dict[str, Any]] = _load_yaml_dir("tools")
WORKFLOW_REGISTRY: dict[str, dict[str, Any]] = _load_yaml_dir("workflows")
SKILL_REGISTRY: dict[str, dict[str, Any]] = _load_yaml_dir("skills")


# ---------------------------------------------------------------------------
# Query helpers used by planner_node and intent_node
# ---------------------------------------------------------------------------

def get_agents_for_workflow(workflow_type: str) -> list[dict[str, Any]]:
    """
    Returns all agents that support the given workflow type.

    An agent with workflow_types: ["*"] matches every workflow.
    Used by planner_node to know which agents are available.
    """
    return [
        agent
        for agent in AGENT_REGISTRY.values()
        if workflow_type in agent.get("workflow_types", [])
        or "*" in agent.get("workflow_types", [])
    ]


def get_all_workflow_types() -> list[str]:
    """
    Returns every registered workflow_type string.

    Used by intent_node to show the LLM the full set of possible workflows.
    Adding a new workflow YAML automatically expands this list.
    """
    return list(WORKFLOW_REGISTRY.keys())


def format_agents_for_planner(workflow_type: str) -> str:
    """
    Returns a plain-text description of available agents for the given workflow.
    This string is injected directly into the planner LLM prompt.

    Example output:
      - web_crawler_node: Crawls top web search results for the query context
      - social_media_scraper_node: Scrapes recent posts from Twitter/X and LinkedIn
      - summarizer_node: Condenses raw sources into a structured brief

    When you add a new agent YAML for this workflow_type, it appears here
    automatically on the next run — zero planner code changes needed.
    """
    agents = get_agents_for_workflow(workflow_type)
    if not agents:
        return "(no agents registered for this workflow yet)"
    lines = [
        f"  - {a.get('agent_id', '?')}: {a.get('purpose', 'no description')}"
        for a in agents
    ]
    return "\n".join(lines)


def format_workflows_for_intent() -> str:
    """
    Returns a plain-text description of all registered workflows.
    Injected into the intent_node LLM prompt so it can classify user queries.

    Example output:
      - research_intelligence: Gathers news and expert analysis on a topic
      - lead_gen: Finds and scores business leads from maps and directories
      - supply_chain: Assesses supplier risk using location and audit data

    When you add a new workflow YAML, it appears here automatically.
    """
    if not WORKFLOW_REGISTRY:
        return "(no workflows registered yet)"
    lines = [
        f"  - {wf_type}: {data.get('purpose', 'no description')}"
        for wf_type, data in WORKFLOW_REGISTRY.items()
    ]
    return "\n".join(lines)


def get_tools_for_agent(agent_id: str) -> list[str]:
    """
    Returns the list of MCP tool IDs that a given agent is allowed to call.
    Used by mcp_client to validate tool access per agent.
    """
    agent = AGENT_REGISTRY.get(agent_id, {})
    return agent.get("tools", [])


def describe_registry() -> str:
    """
    Returns a full human-readable summary of everything registered.
    Useful for debugging: python -c "from app.registry import describe_registry; print(describe_registry())"
    """
    lines = [
        f"Agents  ({len(AGENT_REGISTRY)}): {', '.join(AGENT_REGISTRY) or 'none'}",
        f"Tools   ({len(TOOL_REGISTRY)}): {', '.join(TOOL_REGISTRY) or 'none'}",
        f"Workflows ({len(WORKFLOW_REGISTRY)}): {', '.join(WORKFLOW_REGISTRY) or 'none'}",
        f"Skills  ({len(SKILL_REGISTRY)}): {', '.join(SKILL_REGISTRY) or 'none'}",
    ]
    return "\n".join(lines)
