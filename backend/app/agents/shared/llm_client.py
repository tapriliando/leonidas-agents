"""
llm_client.py — Thin async LLM caller shared by all agent nodes.

USAGE CONTRACT:
  Agents NEVER import an LLM library directly. They always use these four functions:

      from app.agents.shared.llm_client import load_prompt, render_prompt, call_llm, parse_json_response

      template = load_prompt("intent_node.txt")
      prompt   = render_prompt(template, user_query="...", available_workflows="...")
      text     = await call_llm(prompt)
      data     = parse_json_response(text, context="intent_node")

  This keeps all LLM configuration in one place — model, base URL, and API key.
  To swap models or point at a local proxy: change an env var, no code change needed.

CONFIGURATION (environment variables):
  OPENAI_API_KEY   — required; passed as Bearer token to the API
  OPENAI_BASE_URL  — API base URL (default: https://api.openai.com/v1)
                     Set to http://localhost:11434/v1 for a local Ollama proxy, etc.
  LLM_MODEL_NAME   — model name (default: gpt-4o-mini)

TEMPERATURE:
  Always 0 — classification and planning nodes must produce deterministic output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_MODEL_NAME: str = os.getenv("LLM_MODEL_NAME", "gpt-4o-mini")
_BASE_URL: str   = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
_API_KEY: str    = os.getenv("OPENAI_API_KEY", "")

# backend/app/agents/shared/ → parents[0..2] → backend/app/
_PROMPTS_DIR: Path = Path(__file__).resolve().parents[2] / "prompts"


# ---------------------------------------------------------------------------
# Prompt loading + rendering
# ---------------------------------------------------------------------------

def load_prompt(filename: str) -> str:
    """
    Loads a prompt template from backend/app/prompts/<filename>.

    Raises FileNotFoundError with a clear message if the file is missing.
    Prompt files are plain text with {{ variable }} placeholders.
    """
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Expected at: backend/app/prompts/{filename}"
        )
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, **kwargs: Any) -> str:
    """
    Fills {{ variable }} placeholders in a prompt template string.

    All values are coerced to strings. None values become empty strings.
    Placeholders with no matching kwarg are left as-is.

    Example:
        rendered = render_prompt(template, user_query="find leads", available_workflows="...")
    """
    for key, value in kwargs.items():
        placeholder = "{{ " + key + " }}"
        template = template.replace(placeholder, str(value) if value is not None else "")
    return template


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

async def call_llm(prompt: str) -> str:
    """
    Sends a single user message to the configured LLM and returns the text response.

    Uses langchain-openai's ChatOpenAI for native LangGraph compatibility.
    Temperature is always 0 — deterministic output for planning/classification.

    Raises on API or network errors. Callers should wrap in try/except
    and convert failures to state["errors"] entries.
    """
    from langchain_openai import ChatOpenAI  # imported here to keep as optional dep
    from langchain_core.messages import HumanMessage

    llm = ChatOpenAI(
        model=_MODEL_NAME,
        base_url=_BASE_URL or None,
        api_key=_API_KEY or None,
        temperature=0,
    )
    result = await llm.ainvoke([HumanMessage(content=prompt)])
    return str(result.content)


# ---------------------------------------------------------------------------
# JSON response parsing
# ---------------------------------------------------------------------------

def parse_json_response(raw: str, context: str = "") -> dict[str, Any]:
    """
    Parses a JSON object from an LLM response string.

    LLMs sometimes wrap JSON in markdown code fences (```json ... ``` or ``` ... ```).
    This function strips those before parsing.

    Raises ValueError with context info if JSON parsing fails, so callers can
    append a clean error message to state["errors"].
    """
    text = raw.strip()

    # Strip markdown code fence if present
    if text.startswith("```"):
        start = text.find("\n") + 1    # skip the opening ``` or ```json line
        end = text.rfind("```")
        if end > start:
            text = text[start:end].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        tag = f" ({context})" if context else ""
        raise ValueError(
            f"LLM returned invalid JSON{tag}: {exc}\n"
            f"Raw response (first 400 chars): {raw[:400]}"
        ) from exc
