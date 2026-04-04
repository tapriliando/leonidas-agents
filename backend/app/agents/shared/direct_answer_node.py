"""
direct_answer_node.py — Terminal node for queries that don't need a workflow subgraph.

NODE CONTRACT:
  Reads:  user_query, goal, constraints, context
  Writes: artifacts.report  — LLM-generated markdown answer
          status             — "completed"
  Calls:  LLM via call_llm()

WHEN THIS RUNS:
  The planner sets workflow_plan = ["direct_answer"] when:
    - The query is conversational, informational, or doesn't match any registered workflow
    - A suggested workflow is not yet implemented (not in WORKFLOW_SUBGRAPHS)
    - The LLM judges the question simple enough to answer directly

  Examples:
    "what trending news is good for TikTok content?"
    "how does supply chain optimization work?"
    "explain the difference between B2B and B2C"

DESIGN:
  This node uses a generous system prompt to produce a well-structured markdown
  response. It injects user_query, goal, and any constraints so the LLM can
  tailor the depth and focus of its answer.

  It is the graceful fallback for the entire system — if intent and planning
  can't route to a domain workflow, this node ensures the user always gets
  a useful, well-formatted answer instead of a silent failure.

  On LLM failure: falls back to a plain-text error message, still sets
  status = "completed" so the graph always terminates cleanly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.shared.llm_client import call_llm

if TYPE_CHECKING:
    from app.state import AgentState


async def direct_answer_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: answers the user query directly via LLM, without invoking
    a workflow subgraph. Always terminates with status = "completed".
    """
    user_query: str = state.get("user_query") or ""
    goal: str = state.get("goal") or user_query
    constraints = state.get("constraints") or {}
    ctx = state.get("context") or {}
    past_summaries: list = ctx.get("past_run_summaries") or []
    domain_context: dict = ctx.get("domain_context") or {}

    # Build context string for the LLM
    context_parts: list[str] = []
    if domain_context:
        context_parts.append(f"Domain context:\n{json.dumps(domain_context, ensure_ascii=False)}")
    if past_summaries:
        context_parts.append(f"Recent history:\n" + "\n".join(str(s) for s in past_summaries[:3]))

    context_str = "\n\n".join(context_parts) if context_parts else "(none)"

    filters = constraints.get("filters") or {}
    filters_str = json.dumps(filters, ensure_ascii=False) if filters else "(none)"

    prompt = f"""You are a knowledgeable AI assistant. Answer the user's request below clearly
and helpfully. Structure your response in markdown (use headers, bullet points, and bold
text where appropriate to improve readability).

USER REQUEST: {user_query}
GOAL: {goal}
SCOPE FILTERS: {filters_str}

ADDITIONAL CONTEXT:
{context_str}

INSTRUCTIONS:
- Be comprehensive but focused on the user's goal
- Use markdown formatting for clarity
- If the request is about trends, research, or recommendations: provide concrete,
  actionable content — not generic advice
- Keep the response well-structured with a clear heading, body, and conclusion
- If you don't have live data (e.g. real-time search results), state that clearly
  and provide the best possible answer from your knowledge
"""

    answer: str = ""
    try:
        answer = await call_llm(prompt)
    except Exception as exc:
        answer = (
            f"# Response\n\n"
            f"*Could not generate a response due to an error: {exc}*\n\n"
            f"**Your query was:** {user_query}"
        )

    # Ensure the answer is in markdown (add a header if the LLM didn't)
    if answer and not answer.strip().startswith("#"):
        goal_title = goal.replace("_", " ").title()
        answer = f"# {goal_title}\n\n{answer.strip()}"

    artifacts = state.get("artifacts") or {}
    updated_artifacts = {**artifacts, "report": answer}

    return {
        "artifacts": updated_artifacts,
        "status": "completed",
    }
