"""
message_bus.py — Helper functions for A2A message passing.

USAGE CONTRACT:
  Agents NEVER manipulate state["messages"] directly.
  They always call these four functions and return the result dict.

  send_message   → enqueue a new message to another agent
  get_pending    → read messages addressed to you
  mark_done      → mark your message as completed with a result
  request_spawn  → ask the graph to spawn a sub-agent branch

WHY THIS LAYER EXISTS:
  Without these helpers, every agent would need to know the Message schema,
  handle ID generation, timestamps, and list manipulation. That's boilerplate
  that creates inconsistencies when agents are written by different developers.
  With these helpers, the contract is simple:
    - call send_message() to send
    - call get_pending() to receive
    - call mark_done() to complete
    - call request_spawn() to delegate background work

  Adding a new agent that participates in A2A requires zero changes here.
  Just call these functions from the new agent node.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.state import AgentState


# ---------------------------------------------------------------------------
# send_message — enqueue a new message to another agent
# ---------------------------------------------------------------------------

def send_message(
    state: AgentState,
    from_agent: str,
    to_agent: str,
    task: str,
    payload: dict[str, Any],
    provenance: str = "inter_session",
) -> dict:
    """
    Creates a new pending message and returns a state update dict.

    The returned dict uses the append_messages reducer — it contains ONLY
    the new message in a list. LangGraph appends it to state["messages"].

    Example usage inside a node:
        def content_agent_node(state: AgentState) -> dict:
            video_url = state["artifacts"]["workflow_data"]["video_url"]
            return send_message(
                state,
                from_agent="content_agent",
                to_agent="analytics_agent",
                task="analyze_video",
                payload={"video_url": video_url},
            )

    provenance values:
      "inter_session" — A2A message (default)
      "user"          — forwarded from the end user
      "scheduler"     — triggered by a cron job
    """
    new_message = {
        "id": str(uuid4()),
        "from_agent": from_agent,
        "to_agent": to_agent,
        "task": task,
        "payload": payload,
        "status": "pending",
        "result": None,
        "provenance": provenance,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Return ONLY the new message in a list.
    # append_messages reducer merges this into existing state["messages"].
    return {"messages": [new_message]}


# ---------------------------------------------------------------------------
# get_pending — read pending messages addressed to you
# ---------------------------------------------------------------------------

def get_pending(state: AgentState, for_agent: str) -> list[dict]:
    """
    Returns all pending messages addressed to for_agent.

    Agents call this at the start of their node function to check their inbox.
    Returns an empty list if no pending messages — agents must handle this.

    Example usage inside a node:
        def analytics_agent_node(state: AgentState) -> dict:
            pending = get_pending(state, for_agent="analytics_agent")
            if not pending:
                return {}  # nothing to do

            msg = pending[0]  # process one at a time
            result = run_analysis(msg["payload"])
            return mark_done(state, msg_id=msg["id"], result=result)
    """
    messages: list = state.get("messages") or []
    return [
        m for m in messages
        if m.get("to_agent") == for_agent and m.get("status") == "pending"
    ]


# ---------------------------------------------------------------------------
# mark_done — mark a message as completed with a result
# ---------------------------------------------------------------------------

def mark_done(
    state: AgentState,
    msg_id: str,
    result: dict[str, Any],
) -> dict:
    """
    Updates message status to "done" and attaches the result.
    Returns a state update dict with the full updated messages list.

    IMPORTANT: unlike send_message (which appends), mark_done returns the
    FULL messages list. This replaces state["messages"] via keep_latest-style
    semantics — LangGraph sees the full list, not just the changed message.
    The append_messages reducer only triggers when you return a list of NEW
    messages, not when you return the full updated list.

    To signal failure instead of success, use mark_failed().

    Example usage:
        result = analyze(msg["payload"])
        return mark_done(state, msg_id=msg["id"], result={"score": result})
    """
    messages: list = state.get("messages") or []
    updated = [
        {**m, "status": "done", "result": result}
        if m.get("id") == msg_id
        else m
        for m in messages
    ]
    return {"messages": updated}


def mark_failed(
    state: AgentState,
    msg_id: str,
    error: str,
) -> dict:
    """
    Updates message status to "failed" with an error description.
    Used when an agent cannot complete a task.

    Example usage:
        except Exception as e:
            return mark_failed(state, msg_id=msg["id"], error=str(e))
    """
    messages: list = state.get("messages") or []
    updated = [
        {**m, "status": "failed", "result": {"error": error}}
        if m.get("id") == msg_id
        else m
        for m in messages
    ]
    return {"messages": updated}


# ---------------------------------------------------------------------------
# request_spawn — ask the graph to create a sub-agent branch
# ---------------------------------------------------------------------------

def request_spawn(
    agent: str,
    task: str,
    payload: dict[str, Any],
) -> dict:
    """
    Returns a state update that triggers the spawn_router to create
    an isolated sub-agent branch for background work.

    The spawning agent continues; the spawned agent runs independently
    and posts its result back as a "done" message on the bus.

    Example usage — content_agent spawning a research sub-agent:
        def content_agent_node(state: AgentState) -> dict:
            # Start background research without blocking content generation
            return request_spawn(
                agent="research_agent",
                task="find_related_trends",
                payload={"topic": state["user_query"]},
            )

    The graph wires spawn_router after this node:
        graph.add_conditional_edges(
            "content_agent",
            spawn_router,
            {"research_agent": "research_node", "continue": "next_node"},
        )
    """
    return {
        "spawn": {
            "agent": agent,
            "task": task,
            "payload": payload,
            "run_id": None,  # filled by the graph when the branch is created
        }
    }


# ---------------------------------------------------------------------------
# Utility — format message thread for LLM context
# ---------------------------------------------------------------------------

def format_thread_for_prompt(messages: list, for_agent: str | None = None) -> str:
    """
    Formats the message thread as plain text for injection into an LLM prompt.

    Agents that participate in discussions call this to get conversational
    context about what other agents have said so far.

    If for_agent is set, highlights messages addressed to that agent.
    If for_agent is None, returns the full thread.

    Example output:
      [content_agent → analytics_agent] (done): analyze_video
        result: {"score": 0.87, "recommendation": "publish"}

      [analytics_agent → distribution_agent] (pending): schedule_post
        payload: {"platform": "tiktok", "video_url": "https://..."}
    """
    lines = []
    for m in messages:
        sender = m.get("from_agent", "?")
        receiver = m.get("to_agent", "?")
        status = m.get("status", "?")
        task = m.get("task", "?")
        highlight = " ← YOUR MESSAGE" if receiver == for_agent else ""

        lines.append(f"[{sender} → {receiver}] ({status}): {task}{highlight}")

        if status == "done" and m.get("result"):
            lines.append(f"  result: {m['result']}")
        elif status == "pending" and m.get("payload"):
            lines.append(f"  payload: {m['payload']}")
        elif status == "failed" and m.get("result"):
            lines.append(f"  error: {m['result'].get('error', '?')}")

    return "\n".join(lines) if lines else "(no messages in thread)"
