# Registry guide — how to extend the system

## Core philosophy

This system is **workflow-agnostic**. The same `AgentState`, graph infrastructure,
and MCP client power every client use case. You extend the system by registering
new components — never by modifying the core schema.

| To add... | You create... | Core schema changes? |
|-----------|---------------|----------------------|
| New workflow | `graph/workflows/<name>.py` + registry YAML | **None** |
| New agent | `agents/<dept>/<name>.py` + registry YAML | **None** |
| New MCP tool | `mcp-server/tools/<name>.py` + `registry.yaml` | **None** |
| New department | `agents/<new_dept>/` folder | **None** |
| New skill | `docs/registry/skills/<name>.yaml` | **None** |

The only time you change `state.py` is when the infrastructure itself changes
(e.g. adding a new system-layer field like `priority` or `tenant_id`).
Workflow-specific data always lives inside `artifacts["workflow_data"]`.

---

## Registry folder layout

```
docs/registry/
├── agents/
│   ├── _template.yaml         ← copy this to register a new agent
│   └── intent_node.yaml       ← example: universal first node
├── tools/
│   └── _template.yaml         ← copy this to register a new MCP tool
├── skills/
│   └── _template.yaml         ← copy this to register a new composed skill
└── workflows/
    └── _template.yaml         ← copy this to register a new workflow
```

---

## Adding a new client workflow (step by step)

**Example:** A new client needs "agriculture crop yield analysis".

### Step 1 — Register the workflow

Copy `docs/registry/workflows/_template.yaml` to
`docs/registry/workflows/agriculture.yaml`.

Fill in `workflow_type: agriculture`, `department: operations`, node list,
output schema, and example queries.

### Step 2 — Create the graph

Create `backend/app/graph/workflows/agriculture.py`:

```python
from langgraph.graph import StateGraph, END
from app.state import AgentState
from app.agents.operations import fetch_crop_data_node, analyze_yield_node, report_node

def build_agriculture_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("fetch", fetch_crop_data_node)
    g.add_node("analyze", analyze_yield_node)
    g.add_node("report", report_node)
    g.add_edge("fetch", "analyze")
    g.add_edge("analyze", "report")
    g.add_edge("report", END)
    g.set_entry_point("fetch")
    return g.compile()
```

### Step 3 — Write the agent nodes

Create `backend/app/agents/operations/fetch_crop_data_node.py`:

```python
from app.state import AgentState
from app.mcp_client import call_tool

def fetch_crop_data_node(state: AgentState) -> dict:
    filters = (state.get("constraints") or {}).get("filters", {})
    result = call_tool("mcp.crop_data_api", {"region": filters.get("region")})
    updated = {**(state["artifacts"] or {}), "workflow_data": {"field_reports": result["data"]}}
    return {"artifacts": updated, "metrics": {**(state["metrics"] or {}), "item_count": len(result["data"])}}
```

### Step 4 — Register the agent

Copy `docs/registry/agents/_template.yaml` to
`docs/registry/agents/fetch_crop_data_node.yaml` and fill it in.

### Step 5 — Wire routing in conditions.py

In `backend/app/graph/conditions.py`, add a route for `"agriculture"` in the
department router function:

```python
def department_router(state: AgentState) -> str:
    return state.get("department") or "fail"
```

And in `base_graph.py`, add the new graph to the routing map:

```python
from app.graph.workflows.agriculture import build_agriculture_graph
WORKFLOW_GRAPHS = {
    "supply_chain": build_supply_chain_graph,
    "lead_gen": build_lead_gen_graph,
    "agriculture": build_agriculture_graph,  # ← added, nothing else changes
}
```

### Step 6 — Register the MCP tool (if new)

Add to `mcp-server/registry.yaml` and create `mcp-server/tools/crop_data_api.py`.

---

## Naming conventions

| Component | Convention | Example |
|-----------|-----------|---------|
| `workflow_type` | `snake_case` string | `"supply_chain"` |
| `department` | `snake_case` string | `"operations"` |
| Agent file | `<agent_id>.py` | `fetch_crop_data_node.py` |
| Graph file | `<workflow_type>.py` | `agriculture.py` |
| MCP tool id | `mcp.<snake_case>` | `mcp.crop_data_api` |
| Registry file | `<id>.yaml` | `fetch_crop_data_node.yaml` |
| `workflow_data` key | `snake_case` | `"field_reports"`, `"risk_map"` |

---

## Current registered components

### Agents
| agent_id | department | workflow_types |
|----------|-----------|----------------|
| `intent_node` | shared | all |
| (add more as you build phases 3–6) | | |

### Tools
| tool_id | provider | status |
|---------|----------|--------|
| (added in Phase 2) | | |

### Workflows
| workflow_type | department | status |
|---------------|-----------|--------|
| `lead_gen` | distribution | Phase 3+ |
| `supply_chain` | analytics | Phase 3+ |
| `marketing` | content | Phase 3+ |

---

## Why `workflow_data` instead of typed fields?

The key design decision:

```python
# BAD — requires schema migration for every new client use case:
class Artifacts(TypedDict):
    raw_complaints: Optional[list]    # complaint-specific
    raw_suppliers: Optional[list]     # supply chain-specific
    raw_leads: Optional[list]         # lead gen-specific
    ...

# GOOD — one field, any workflow, zero schema changes:
class Artifacts(TypedDict):
    workflow_data: Optional[dict]     # {"suppliers": [...], "risk_map": {...}}
    report: Optional[str]
```

`workflow_data` is the contract boundary. Everything workflow-specific lives inside
that dict. The shape of `workflow_data` is documented per workflow in the registry,
not enforced by the Python type system. This is intentional — flexibility over rigidity
at this stage of the system.
