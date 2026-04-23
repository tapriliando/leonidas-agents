"""
contracts.py — Stable runtime contracts for Markdown-first agents.

These Pydantic models validate agent definitions loaded from YAML and Markdown
registry files. They complement the workflow-agnostic AgentState TypedDict.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ToolPolicy(BaseModel):
    """Per-agent limits for MCP tool usage."""

    max_tool_calls: int = Field(default=8, ge=0, le=100)
    tool_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_retries_per_tool: int = Field(default=1, ge=0, le=5)


class AgentDefinition(BaseModel):
    """
    Canonical agent contract (YAML or Markdown frontmatter).

    Markdown body (instructions) is stored in `instructions_markdown`.
    Legacy YAML-only agents may omit it.
    """

    agent_id: str = Field(..., min_length=1, max_length=128)
    purpose: str = Field(default="", max_length=4000)
    workflow_types: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    source: Literal["yaml", "markdown"] = "yaml"
    instructions_markdown: str = Field(default="")
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)

    @field_validator("agent_id")
    @classmethod
    def agent_id_slug(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_.-]+$", v):
            raise ValueError(
                "agent_id must contain only letters, digits, underscore, dot, or hyphen"
            )
        return v

    @field_validator("tools")
    @classmethod
    def tools_non_empty_strings(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for t in v:
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
        return out

    def to_planner_dict(self) -> dict[str, Any]:
        """Shape compatible with legacy AGENT_REGISTRY dict consumers."""
        return {
            "agent_id": self.agent_id,
            "purpose": self.purpose,
            "workflow_types": self.workflow_types,
            "tools": self.tools,
            "source": self.source,
            "tool_policy": self.tool_policy.model_dump(),
        }


class ExecutionContext(BaseModel):
    """Narrow context passed into the generic Markdown agent executor."""

    run_id: str
    user_id: Optional[str] = None
    user_query: str = ""
    goal: Optional[str] = None
    workflow_type: Optional[str] = None
    workflow_data: dict[str, Any] = Field(default_factory=dict)
    memory_snippet: Optional[str] = None


class AgentResult(BaseModel):
    """Normalized output from a single agent step."""

    agent_id: str
    text: str = ""
    structured: dict[str, Any] = Field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_workflow_patch(self) -> dict[str, Any]:
        """Merge into artifacts.workflow_data under markdown_agents.<agent_id>."""
        return {
            "text": self.text,
            "structured": self.structured,
            "tool_trace": self.tool_trace,
            "errors": self.errors,
        }


__all__ = [
    "ToolPolicy",
    "AgentDefinition",
    "ExecutionContext",
    "AgentResult",
]
