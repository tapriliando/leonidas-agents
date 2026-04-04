# Workflow chaining — running multiple workflows in one request

## What this enables

A user can send one message that triggers a full cross-department pipeline:

```
"Research today's trending news about AI in agriculture,
 create a short video script from it,
 then publish to TikTok, Instagram, and Facebook."
```

The system runs three workflows **in sequence**, across three departments:

```
[research_intelligence]  →  [content_pipeline]  →  [social_publishing]
     (research dept)           (content dept)         (distribution dept)
```

Each workflow hands its output to the next through the shared `workflow_data` dict.

---

## How the planner builds the chain

After `intent_node` understands the user's request, `planner_node` sets:

```python
{
    "workflow_plan": [
        "research_intelligence",   # step 1
        "content_pipeline",        # step 2
        "social_publishing",       # step 3
    ],
    "current_workflow_index": 0,   # start at step 1
}
```

The meta-orchestrator graph reads `workflow_plan[current_workflow_index]`
and routes to the right subgraph. When a subgraph finishes, the index advances
by 1 and the next subgraph starts.

---

## How data flows between workflows

`artifacts.workflow_data` is a single dict that grows across all workflows.
Each workflow adds its output; the next workflow reads what it needs.

| After which workflow | What's in workflow_data |
|---|---|
| **research_intelligence** finishes | `{"summary": "AI crop yields up 30%...", "raw_sources": [...], "key_topics": [...]}` |
| **content_pipeline** finishes | `{...previous..., "script": "Today in AgriTech...", "video_url": "https://..."}` |
| **social_publishing** finishes | `{...previous..., "publish_results": {"tiktok": "ok", "instagram": "ok", "facebook": "ok"}}` |

Content pipeline reads `workflow_data["summary"]` as its input.
Social publishing reads `workflow_data["video_url"]` as its input.
No extra wiring needed — it's just a growing shared dictionary.

---

## How the planner knows which workflows it can chain

The planner reads the registry at runtime via `registry.py`:

```python
format_workflows_for_intent()
# Returns:
#   - research_intelligence: Gathers news and expert analysis on a topic
#   - content_pipeline: Creates video scripts and assets from a research brief
#   - social_publishing: Publishes content to TikTok, Instagram, and Facebook
#   - lead_gen: Finds and scores business leads from maps and directories
```

When you add a new workflow YAML file, the planner automatically knows
it exists and can include it in chains — no code change needed.

---

## Adding a new workflow to the chain (non-technical)

**Example:** You want to add a "WhatsApp notification" step at the end.

1. Create `docs/registry/workflows/whatsapp_notify.yaml`
2. Fill in `workflow_type: whatsapp_notify`, describe the purpose, list agents and tools

That's your part. The developer then:
- Builds `graph/workflows/whatsapp_notify.py`
- Adds one line to `WORKFLOW_SUBGRAPHS` in `base_graph.py`

The planner will automatically know it can include `whatsapp_notify` in chains
on the next run.

---

## Parallel vs sequential workflows

Currently, workflows in the chain run **sequentially** (one after the other)
because each depends on the previous workflow's output.

For workflows that **don't depend on each other**, LangGraph supports parallel
fan-out (using the `Send` API). This is a Phase 4+ feature. Example use case:
- Run `lead_gen` and `research_intelligence` at the same time
- Then merge both outputs for a combined report

---

## State fields involved

| Field | Type | Purpose |
|---|---|---|
| `workflow_plan` | `list[str]` | Ordered workflow_types to execute |
| `current_workflow_index` | `int` (operator.add) | Which step is active; advances by 1 each completion |
| `artifacts.workflow_data` | `dict` | Shared output bucket — grows across all workflows |
| `workflow_type` | `str` | The currently active workflow_type |
| `department` | `str` | The department running the current workflow |

`current_workflow_index` uses `operator.add` — the same reducer pattern as
`iteration_count`. This means the `workflow_transition_node` returns
`{"current_workflow_index": 1}` to advance, never setting an absolute value.

---

## The routing logic (for developers)

```
planner_node
    ↓
workflow_progression_router(state)
    → reads: workflow_plan[current_workflow_index]
    → returns: "research_intelligence" (or "content_pipeline", or "end")
    ↓
[research_intelligence subgraph runs]
    ↓
workflow_transition_node
    → returns: {"current_workflow_index": 1}   ← adds 1
    ↓
workflow_progression_router(state)
    → reads: workflow_plan[1] = "content_pipeline"
    → returns: "content_pipeline"
    ↓
[content_pipeline subgraph runs]
    ↓
workflow_transition_node
    → returns: {"current_workflow_index": 1}   ← adds 1 (total now 2)
    ↓
workflow_progression_router(state)
    → reads: workflow_plan[2] = "social_publishing"
    ...and so on until index >= len(plan) → returns "end"
```

---

## Safety limits

Defined in `graph/conditions.py`:

```python
MAX_WORKFLOW_STEPS = 10   # max workflows in a single chain
MAX_ITERATIONS = 5        # max retries within a single workflow
```

If `current_workflow_index >= MAX_WORKFLOW_STEPS`, the router returns `"end"`
regardless of what's left in `workflow_plan`. This prevents runaway chains.
