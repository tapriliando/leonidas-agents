# State and LangGraph

## Node contract

```python
def my_node(state: AgentState) -> dict:
    return {"field_only_i_changed": value}
```

LangGraph merges the returned dict into `state`. Return **only** keys you updated.

## State

- Use `TypedDict` with `total=False` for optional fields.
- Prefer nested buckets (`artifacts`, `metrics`, `leads`) when the schema grows.
- Version or namespace fields if you run multiple subgraphs in one state.

## Edges

- **Unconditional:** `add_edge("a", "b")` — always `a` then `b`.
- **Conditional:** `add_conditional_edges("a", router_fn, {"ok": "b", "retry": "a"})` — `router_fn(state) -> str` must match a key.

## Loops

Route back to an earlier node with a conditional edge when a condition fails (e.g. low quality, missing data).

## Files in this repo

| Path | Role |
|------|------|
| `backend/app/state.py` | `AgentState` (Phase 1: expand) |
| `backend/app/graph/conditions.py` | Router functions |
| `backend/app/graph/lead_graph.py` | Lead pipeline graph |
| `backend/app/graph/video_graph.py` | Video workflow graph |
| `backend/app/graph/base_graph.py` | Shared helpers |
