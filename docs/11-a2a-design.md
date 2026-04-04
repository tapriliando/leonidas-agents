# A2A (Agent-to-Agent) Communication Design

## Core principle

Agents NEVER call each other directly.

```
# WRONG — breaks the architecture
analytics_result = analytics_agent.run(video_url)

# RIGHT — agents communicate via the message bus in state
return send_message(state, from_agent="content_agent",
                    to_agent="analytics_agent",
                    task="analyze_video",
                    payload={"video_url": video_url})
```

The graph decides who runs next — not the agents themselves.

---

## The message bus

```
state["messages"] = list[Message]
```

Every message has this shape:

```python
{
  "id":          "uuid",               # unique per message
  "from_agent":  "content_agent",      # who sent it
  "to_agent":    "analytics_agent",    # who must handle it
  "task":        "analyze_video",      # what to do
  "payload":     {"video_url": "..."},  # input data
  "status":      "pending",            # pending → processing → done | failed
  "result":      None,                 # filled when done or failed
  "provenance":  "inter_session",      # how it was created
  "created_at":  "2026-03-31T..."
}
```

### Message status lifecycle

```
[content_agent sends]
      ↓
  status = "pending"
      ↓
[message_router routes to analytics_agent]
      ↓
  status = "processing"   (optional — agent can set this while working)
      ↓
[analytics_agent calls mark_done()]
      ↓
  status = "done",  result = {...}
      ↓
[or mark_failed() if something goes wrong]
      ↓
  status = "failed", result = {"error": "..."}
```

### Provenance metadata

`provenance` tells agents and logs HOW a message was created:

| Value | Meaning |
|---|---|
| `"inter_session"` | Sent from one agent to another (default for A2A) |
| `"user"` | Forwarded from the end user's message |
| `"scheduler"` | Triggered by a cron/heartbeat run |

This lets you distinguish internal routing from user-initiated messages,
which matters for logging, billing, and audit trails.

---

## The four message bus helpers

All agents import from `app.agents.shared.message_bus` — never touch `state["messages"]` raw.

### 1. `send_message` — enqueue a task for another agent

```python
from app.agents.shared.message_bus import send_message

def content_agent_node(state: AgentState) -> dict:
    video_url = state["artifacts"]["workflow_data"]["video_url"]
    return send_message(
        state,
        from_agent="content_agent",
        to_agent="analytics_agent",
        task="analyze_video",
        payload={"video_url": video_url},
    )
```

Returns `{"messages": [new_message]}` — the `append_messages` reducer appends it.

### 2. `get_pending` — read your inbox

```python
from app.agents.shared.message_bus import get_pending

def analytics_agent_node(state: AgentState) -> dict:
    pending = get_pending(state, for_agent="analytics_agent")
    if not pending:
        return {}  # nothing to do yet
    msg = pending[0]
    ...
```

### 3. `mark_done` — complete a task

```python
from app.agents.shared.message_bus import get_pending, mark_done

def analytics_agent_node(state: AgentState) -> dict:
    pending = get_pending(state, for_agent="analytics_agent")
    msg = pending[0]
    result = run_analysis(msg["payload"]["video_url"])
    return mark_done(state, msg_id=msg["id"], result={"score": result})
```

Returns the full updated `messages` list with the target message marked done.

### 4. `request_spawn` — delegate background work

```python
from app.agents.shared.message_bus import request_spawn

def content_agent_node(state: AgentState) -> dict:
    # Spawn a research sub-agent in the background while content work continues
    return request_spawn(
        agent="research_agent",
        task="find_related_trends",
        payload={"topic": state["user_query"]},
    )
```

---

## The message router

`message_router` in `conditions.py` is the dispatch point:

```python
def message_router(state: AgentState) -> str:
    for msg in state.get("messages") or []:
        if msg.get("status") == "pending":
            return msg["to_agent"]  # e.g. "analytics_agent"
    return "end"
```

Wired in the graph:

```python
graph.add_conditional_edges(
    "dispatcher_node",
    message_router,
    {
        "analytics_agent":    "analytics_node",
        "content_agent":      "content_node",
        "distribution_agent": "distribution_node",
        "end":                END,
        # ← one line per agent that can receive messages
    },
)
```

**To add a new A2A-capable agent:** add one entry to this mapping dict.

---

## Sync vs async communication

### Sync (simple — Phase 3 default)

```
content_agent sends message → message_router routes → analytics_agent processes → done
```

The graph runs sequentially. content_agent writes a message, the router
picks it up immediately on the next graph step, analytics_agent processes it.
One step at a time. Easy to reason about.

### Async (production — Phase 4+)

```
content_agent sends message → continues its own work
                           → (separately) analytics_agent processes when ready
```

In production, messages that don't need an immediate reply can be placed
into a Redis queue. The analytics_agent picks them up asynchronously.
This maps to LangGraph's `Send` API and background task patterns.

For now (Phase 1-3): sync is correct. All A2A calls wait for a reply
before the workflow continues.

---

## The spawn pattern

Spawn creates an isolated sub-agent branch for work that can run independently.

```python
# content_agent spawns research_agent in the background
return request_spawn("research_agent", task="find trends", payload={...})
```

The `spawn_router` in `conditions.py` handles this:

```python
def spawn_router(state: AgentState) -> str:
    spawn = state.get("spawn")
    if spawn and spawn.get("agent"):
        return spawn["agent"]   # route to spawned agent
    return "continue"
```

After the spawned agent finishes, it posts its result back as a `done` message
and clears `state["spawn"]` so the router doesn't re-trigger.

---

## Full A2A flow example

User: "Create a video about AI trends and check if it's good enough to post."

```
[content_agent]
  → creates script + video
  → sends message: to="analytics_agent", task="analyze_video", payload={video_url}
  → returns {"messages": [new_msg]}

[message_router]
  → finds pending message to "analytics_agent"
  → returns "analytics_agent"
  → graph routes to analytics_node

[analytics_agent]
  → calls get_pending(state, "analytics_agent")
  → runs analysis on video_url
  → returns mark_done(state, msg["id"], result={"score": 0.87, "ok": True})

[message_router]
  → no more pending messages
  → returns "end"

[graph continues]
  → reads message result from state["messages"]
  → if score >= threshold: route to distribution_agent
  → else: route back to content_agent to revise
```

The entire exchange is visible in `state["messages"]` — fully auditable.

---

## The prompt template (`prompts/message_agent.txt`)

Agents that process message tasks load this template:

- `{{ agent_role }}` — who the agent is
- `{{ task }}` — the task name from the message
- `{{ payload }}` — the input data from the message
- `{{ discussion_thread }}` — formatted history of all messages so far
- `{{ domain_context }}` — from `state["context"]["domain_context"]`

The `format_thread_for_prompt(messages)` helper in `message_bus.py` formats
`state["messages"]` into readable text for the prompt.

---

## Critical rules (never break these)

| Rule | Why |
|---|---|
| Agents never call other agents directly | Direct calls bypass the graph, break routing, and create untraceable dependencies |
| Agents never decide routing | Only router functions in `conditions.py` decide what runs next |
| Agents never store API keys | All external calls go through MCP — agents only call `call_tool()` |
| Use `send_message()`, never raw dict construction | Ensures consistent schema, ID generation, and timestamps |
| Use `mark_done()` / `mark_failed()`, never mutate messages directly | Consistent status lifecycle; makes the bus auditable |

---

## Adding a new A2A-capable agent (checklist)

1. Create `agents/<dept>/<agent_id>.py` with the node function
2. Import `get_pending`, `mark_done` from `message_bus.py`
3. Register the agent in `docs/registry/agents/<agent_id>.yaml`
4. Add one entry to the `message_router` mapping dict in the relevant graph file:
   ```python
   {"your_new_agent": "your_new_node", ...}
   ```
5. No changes to `state.py`, `conditions.py`, or `message_bus.py`
