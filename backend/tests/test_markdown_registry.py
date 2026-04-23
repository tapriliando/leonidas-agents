"""Markdown agent registry loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import AgentDefinition
from app.registry_markdown import load_agent_definition_from_markdown, validate_all_markdown_agents


def _agents_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "registry" / "agents"


def test_load_research_assistant_md():
    path = _agents_dir() / "research_assistant_md.md"
    if not path.exists():
        pytest.skip("sample markdown agent missing")
    d = load_agent_definition_from_markdown(path)
    assert isinstance(d, AgentDefinition)
    assert d.agent_id == "research_assistant_md"
    assert "mcp.web_search" in d.tools
    assert d.source == "markdown"
    assert "LangGraph" in d.instructions_markdown or len(d.instructions_markdown) > 10


def test_validate_all_markdown_agents_ok():
    errs = validate_all_markdown_agents(_agents_dir())
    assert errs == [], errs
