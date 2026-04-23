"""
registry.py — Dynamic agent, tool, and workflow registry.

Loads YAML from docs/registry/*/ and Markdown agent specs from
docs/registry/agents/*.md. Markdown definitions override YAML entries
with the same agent_id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.contracts import AgentDefinition

# ---------------------------------------------------------------------------
# Registry root — resolves to docs/registry/ relative to this file
# ---------------------------------------------------------------------------

_REGISTRY_ROOT = Path(__file__).resolve().parents[2] / "docs" / "registry"


def _load_yaml_dir(subdir: str) -> dict[str, dict[str, Any]]:
    folder = _REGISTRY_ROOT / subdir
    if not folder.exists():
        return {}

    try:
        import yaml
    except ImportError:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for path in sorted(folder.glob("*.yaml")):
        if path.stem.startswith("_"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f) or {}
            key = (
                data.get("agent_id")
                or data.get("tool_id")
                or data.get("workflow_type")
                or data.get("skill_id")
                or path.stem
            )
            result[key] = data
        except Exception:
            pass
    return result


def _load_merged_agent_registry() -> tuple[dict[str, dict[str, Any]], dict[str, AgentDefinition]]:
    yaml_agents = _load_yaml_dir("agents")
    md_folder = _REGISTRY_ROOT / "agents"
    definitions: dict[str, AgentDefinition] = {}
    try:
        from app.registry_markdown import load_markdown_agents_dir

        definitions = load_markdown_agents_dir(md_folder)
    except ImportError:
        definitions = {}
    merged: dict[str, dict[str, Any]] = {**yaml_agents}
    for agent_id, defn in definitions.items():
        d = defn.to_planner_dict()
        d["instructions_markdown"] = defn.instructions_markdown
        merged[agent_id] = d
    return merged, definitions


AGENT_REGISTRY: dict[str, dict[str, Any]]
AGENT_DEFINITIONS: dict[str, AgentDefinition]
TOOL_REGISTRY: dict[str, dict[str, Any]]
WORKFLOW_REGISTRY: dict[str, dict[str, Any]]
SKILL_REGISTRY: dict[str, dict[str, Any]]

AGENT_REGISTRY, AGENT_DEFINITIONS = _load_merged_agent_registry()
TOOL_REGISTRY = _load_yaml_dir("tools")
WORKFLOW_REGISTRY = _load_yaml_dir("workflows")
SKILL_REGISTRY = _load_yaml_dir("skills")


def refresh_registry() -> None:
    """Reload all registries (used by tests and CLI validate)."""
    global AGENT_REGISTRY, AGENT_DEFINITIONS, TOOL_REGISTRY, WORKFLOW_REGISTRY, SKILL_REGISTRY
    AGENT_REGISTRY, AGENT_DEFINITIONS = _load_merged_agent_registry()
    TOOL_REGISTRY = _load_yaml_dir("tools")
    WORKFLOW_REGISTRY = _load_yaml_dir("workflows")
    SKILL_REGISTRY = _load_yaml_dir("skills")


def get_agent_definition(agent_id: str) -> Optional[AgentDefinition]:
    return AGENT_DEFINITIONS.get(agent_id)


def get_agents_for_workflow(workflow_type: str) -> list[dict[str, Any]]:
    return [
        agent
        for agent in AGENT_REGISTRY.values()
        if workflow_type in agent.get("workflow_types", [])
        or "*" in agent.get("workflow_types", [])
    ]


def get_all_workflow_types() -> list[str]:
    return list(WORKFLOW_REGISTRY.keys())


def format_agents_for_planner(workflow_type: str) -> str:
    agents = get_agents_for_workflow(workflow_type)
    if not agents:
        return "(no agents registered for this workflow yet)"
    lines = [
        f"  - {a.get('agent_id', '?')}: {a.get('purpose', 'no description')}"
        for a in agents
    ]
    return "\n".join(lines)


def format_workflows_for_intent() -> str:
    if not WORKFLOW_REGISTRY:
        return "(no workflows registered yet)"
    lines = [
        f"  - {wf_type}: {data.get('purpose', 'no description')}"
        for wf_type, data in WORKFLOW_REGISTRY.items()
    ]
    return "\n".join(lines)


def get_tools_for_agent(agent_id: str) -> list[str]:
    agent = AGENT_REGISTRY.get(agent_id, {})
    return list(agent.get("tools", []))


def get_tool_policy_for_agent(agent_id: str) -> dict[str, Any]:
    agent = AGENT_REGISTRY.get(agent_id, {})
    return dict(agent.get("tool_policy") or {})


def describe_registry() -> str:
    lines = [
        f"Agents  ({len(AGENT_REGISTRY)}): {', '.join(AGENT_REGISTRY) or 'none'}",
        f"  (markdown-defined: {len(AGENT_DEFINITIONS)})",
        f"Tools   ({len(TOOL_REGISTRY)}): {', '.join(TOOL_REGISTRY) or 'none'}",
        f"Workflows ({len(WORKFLOW_REGISTRY)}): {', '.join(WORKFLOW_REGISTRY) or 'none'}",
        f"Skills  ({len(SKILL_REGISTRY)}): {', '.join(SKILL_REGISTRY) or 'none'}",
    ]
    return "\n".join(lines)


def list_markdown_agent_ids() -> list[str]:
    return sorted(AGENT_DEFINITIONS.keys())


def agents_markdown_dir() -> Path:
    """Directory containing `*.md` agent definitions."""
    return _REGISTRY_ROOT / "agents"
