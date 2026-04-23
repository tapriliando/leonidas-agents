"""
Generic executor for Markdown-defined (and YAML-defined) agents.

Runs an LLM loop with optional JSON tool calls, enforcing per-agent allowlists
via mcp_client.call_tool_guarded.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

from app.contracts import AgentDefinition, AgentResult, ExecutionContext, ToolPolicy
from app.registry import AGENT_REGISTRY, get_agent_definition

if TYPE_CHECKING:
    from app.state import AgentState

logger = logging.getLogger("mas.markdown_agent")


def resolve_agent_definition(agent_id: str) -> Optional[AgentDefinition]:
    """Build AgentDefinition from markdown registry or YAML-only agent entry."""
    md = get_agent_definition(agent_id)
    if md is not None:
        return md
    raw = AGENT_REGISTRY.get(agent_id)
    if not raw:
        return None
    try:
        return AgentDefinition(
            agent_id=str(raw.get("agent_id") or agent_id),
            purpose=str(raw.get("purpose") or ""),
            workflow_types=list(raw.get("workflow_types") or []),
            tools=[str(t) for t in (raw.get("tools") or []) if t],
            source="yaml",
            instructions_markdown=str(raw.get("instructions_markdown") or raw.get("instructions") or ""),
            tool_policy=ToolPolicy(**(raw.get("tool_policy") or {})),
        )
    except Exception:
        return None


def _ctx_from_state(state: AgentState, agent_id: str) -> ExecutionContext:
    artifacts = state.get("artifacts") or {}
    wd = artifacts.get("workflow_data") or {}
    ctx_mem = state.get("context") or {}
    mem_bits = []
    if ctx_mem.get("past_run_summaries"):
        mem_bits.append(str(ctx_mem["past_run_summaries"])[:2000])
    return ExecutionContext(
        run_id=state.get("run_id", ""),
        user_id=state.get("user_id"),
        user_query=state.get("user_query", ""),
        goal=state.get("goal"),
        workflow_type=state.get("workflow_type"),
        workflow_data=wd if isinstance(wd, dict) else {},
        memory_snippet="\n".join(mem_bits) if mem_bits else None,
    )


def _build_system_prompt(defn: AgentDefinition, ctx: ExecutionContext) -> str:
    parts = [
        f"You are agent `{defn.agent_id}`.",
        f"Purpose: {defn.purpose or 'Execute the user task.'}",
        "",
        "Instructions:",
        defn.instructions_markdown or "(no additional instructions)",
        "",
        "Respond with a single JSON object ONLY (no markdown fences), schema:",
        '{ "final_answer": string (optional if using tools),',
        '  "tool_calls": [ { "name": "<mcp tool id>", "params": { ... } } ] (optional) }',
        "",
        "Rules:",
        "- Only request tools from your allowed list (server will reject others).",
        "- If you have enough information, set final_answer and omit tool_calls or use empty list.",
        f"- User query: {ctx.user_query!s}",
    ]
    if ctx.memory_snippet:
        parts.extend(["", f"Memory context (truncated):\n{ctx.memory_snippet}"])
    wd_preview = json.dumps(ctx.workflow_data, default=str)[:6000]
    parts.extend(["", f"Current workflow_data (JSON, may be truncated):\n{wd_preview}"])
    return "\n".join(parts)


async def execute_markdown_agent(state: AgentState, agent_id: str) -> dict[str, Any]:
    """
    LangGraph node body: runs one agent step and merges into artifacts.workflow_data.markdown_agents.
    """
    defn = resolve_agent_definition(agent_id)
    if defn is None:
        return {"errors": [f"markdown_agent_executor: unknown agent_id {agent_id!r}"]}

    ctx = _ctx_from_state(state, agent_id)
    system = _build_system_prompt(defn, ctx)
    tool_budget: list[int] = [0]  # mutable counter in closure list
    tool_trace: list[dict[str, Any]] = []
    errors: list[str] = []

    from app.agents.shared.llm_client import call_llm, parse_json_response
    from app.mcp_client import call_tool_guarded

    max_rounds = 6
    last_text = ""
    for _ in range(max_rounds):
        prompt = system + "\n\nIf you already see tool results in the thread below, synthesize final_answer.\n"
        if tool_trace:
            prompt += "\nTool results so far:\n" + json.dumps(tool_trace, default=str)[:8000]
        try:
            raw = await call_llm(prompt)
            last_text = raw
            data = parse_json_response(raw, context=f"markdown_agent:{agent_id}")
        except Exception as exc:
            errors.append(f"{agent_id}: LLM/JSON error: {exc}")
            break
        if not isinstance(data, dict):
            errors.append(f"{agent_id}: model returned non-object JSON")
            break

        calls = data.get("tool_calls") or []
        final = data.get("final_answer")
        if calls and isinstance(calls, list):
            for c in calls:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                params = c.get("params") if isinstance(c.get("params"), dict) else {}
                if not name:
                    continue
                tr = await call_tool_guarded(
                    agent_id,
                    str(name),
                    params,
                    meta={
                        "run_id": ctx.run_id,
                        "user_id": ctx.user_id,
                        "_tool_budget": tool_budget,
                        "_max_tool_calls": int(defn.tool_policy.max_tool_calls),
                    },
                    timeout_seconds=float(defn.tool_policy.tool_timeout_seconds),
                )
                tool_trace.append(
                    {"name": name, "params": params, "success": tr.success, "result": tr.model_dump()}
                )
            continue

        if final is not None:
            last_text = str(final)
            break

        if final is None and not calls:
            last_text = raw
            break

    result = AgentResult(
        agent_id=agent_id,
        text=last_text,
        structured={"raw_last": last_text[:2000]},
        tool_trace=tool_trace,
        errors=errors,
    )
    artifacts = dict(state.get("artifacts") or {})
    wd = dict(artifacts.get("workflow_data") or {})
    ma = dict(wd.get("markdown_agents") or {})
    ma[agent_id] = result.to_workflow_patch()
    wd["markdown_agents"] = ma
    artifacts["workflow_data"] = wd
    out: dict[str, Any] = {"artifacts": artifacts}
    if errors:
        out["errors"] = errors
    return out


def make_markdown_agent_node(agent_id: str):
    """Returns an async LangGraph node callable bound to agent_id."""

    async def _node(state: AgentState) -> dict[str, Any]:
        return await execute_markdown_agent(state, agent_id)

    _node.__name__ = f"markdown_agent_{agent_id}"
    return _node
