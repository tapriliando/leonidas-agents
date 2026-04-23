"""
registry_markdown.py — Load agent definitions from Markdown + YAML frontmatter.

Expected file shape:

---
agent_id: example_agent
purpose: One-line description
workflow_types: ["markdown_chain"]
tools: ["mcp.web_search"]
source: markdown
max_tool_calls: 5
tool_timeout_seconds: 30
---

# Role

Free-form instructions for the LLM (system-style content).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.contracts import AgentDefinition, ToolPolicy

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL | re.MULTILINE,
)


class MarkdownAgentValidationError(Exception):
    """Raised when a Markdown agent file fails validation."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    m = _FRONTMATTER_RE.match(raw.strip())
    if not m:
        raise ValueError("missing YAML frontmatter (must start with --- and include closing ---)")
    yaml_blob, body = m.group(1), m.group(2)
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load Markdown agent registries") from exc
    meta = yaml.safe_load(yaml_blob) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must parse to a YAML mapping")
    return meta, body.strip()


def load_agent_definition_from_markdown(path: Path) -> AgentDefinition:
    raw = path.read_text(encoding="utf-8")
    try:
        meta, body = _parse_frontmatter(raw)
    except ValueError as exc:
        raise MarkdownAgentValidationError(path, str(exc)) from exc

    agent_id = meta.get("agent_id") or path.stem
    purpose = str(meta.get("purpose") or "")
    workflow_types = meta.get("workflow_types") or []
    if isinstance(workflow_types, str):
        workflow_types = [workflow_types]
    if not isinstance(workflow_types, list):
        raise MarkdownAgentValidationError(path, "workflow_types must be a list or string")

    tools = meta.get("tools") or []
    if isinstance(tools, str):
        tools = [tools]
    if not isinstance(tools, list):
        raise MarkdownAgentValidationError(path, "tools must be a list or string")

    max_tc = meta.get("max_tool_calls", 8)
    timeout = meta.get("tool_timeout_seconds", 30.0)
    retries = meta.get("max_retries_per_tool", 1)
    try:
        policy = ToolPolicy(
            max_tool_calls=int(max_tc),
            tool_timeout_seconds=float(timeout),
            max_retries_per_tool=int(retries),
        )
    except Exception as exc:
        raise MarkdownAgentValidationError(path, f"invalid tool policy: {exc}") from exc

    try:
        return AgentDefinition(
            agent_id=str(agent_id),
            purpose=purpose,
            workflow_types=[str(x) for x in workflow_types],
            tools=[str(x) for x in tools],
            source="markdown",
            instructions_markdown=body,
            tool_policy=policy,
        )
    except Exception as exc:
        raise MarkdownAgentValidationError(path, str(exc)) from exc


def load_markdown_agents_dir(folder: Path) -> dict[str, AgentDefinition]:
    if not folder.exists():
        return {}
    out: dict[str, AgentDefinition] = {}
    for path in sorted(folder.glob("*.md")):
        if path.stem.startswith("_"):
            continue
        try:
            definition = load_agent_definition_from_markdown(path)
        except MarkdownAgentValidationError:
            raise
        except Exception as exc:
            raise MarkdownAgentValidationError(path, str(exc)) from exc
        out[definition.agent_id] = definition
    return out


def validate_all_markdown_agents(folder: Path) -> list[str]:
    """Returns a list of error strings; empty means all valid."""
    errors: list[str] = []
    if not folder.exists():
        return [f"agents markdown folder missing: {folder}"]
    for path in sorted(folder.glob("*.md")):
        if path.stem.startswith("_"):
            continue
        try:
            load_agent_definition_from_markdown(path)
        except MarkdownAgentValidationError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return errors
