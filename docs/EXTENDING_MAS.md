# Extending the multi-agent system (MAS)

This project separates **registry metadata** (YAML, no code) from **executable wiring** (Python graphs + MCP tools). Follow the layers below so new capabilities stay consistent.

## Layers (who changes what)

| Layer | Who | What |
|--------|-----|------|
| **Tool** | Developer | New external API → MCP module + `mcp_server/registry.yaml` + optional `docs/registry/tools/*.yaml` |
| **Agent node** | Developer | New `backend/app/agents/<dept>/<node>.py` + `docs/registry/agents/<id>.yaml` |
| **Workflow** | Developer / power user | `docs/registry/workflows/<type>.yaml` (purpose, examples — drives intent/planner text) |
| **Graph** | Developer | `backend/app/graph/workflows/<name>_graph.py` + **one line** in `base_graph._init_workflow_subgraphs()` |
| **Skill** | Product / partner | `docs/registry/skills/*.yaml` — documents a *composed* feature (optional today) |

**End users of your app** should not edit Python. Expose:

- Natural-language requests (intent/planner already read `docs/registry/workflows/*.yaml`).
- Optional **saved “skills” or templates** in *your* product DB that map to `workflow_plan` + `constraints` you pass into `make_initial_state` / API — without forking this repo.

## Adding a new MCP tool (example: you already did HeyGen)

1. Put the secret in **`.env.mcp`** (loaded by uvicorn). Never commit keys.
2. Add `mcp_server/tools/<name>.py` with `async def run(params) -> ToolResult`.
3. Register in **`mcp_server/registry.yaml`** (`- name:`, `module:`, `auth_env:`).
4. Restart the MCP server.
5. From agents: `await call_tool("mcp.<id>", {...}, meta={"run_id": run_id})`.
6. (Optional) Copy `docs/registry/tools/_template.yaml` → `docs/registry/tools/mcp.<id>.yaml` for documentation.

## Adding a new agent node

1. Create `backend/app/agents/<department>/<node>.py`.
2. Read/write only **`artifacts.workflow_data`** keys you own; reuse shared keys when possible (`items`, `summary`, `content_prompt`, `content_generation`, …).
3. Add **`docs/registry/agents/<agent_id>.yaml`** so the planner text can mention the agent.
4. Wire the node inside a **workflow graph** (next section).

## Adding a new workflow

1. **`docs/registry/workflows/<workflow_type>.yaml`**  
   - Must match the `workflow_type` string the planner will emit.  
   - Include `purpose`, `example_queries`, `department`, `agents_used`, `tools_used`.

2. **`backend/app/graph/workflows/<something>_graph.py`**  
   - `def build_<name>_graph():` → `StateGraph(AgentState)` → nodes + edges → `return g.compile()`.

3. **`backend/app/graph/base_graph.py`** — inside `_init_workflow_subgraphs()`, add one line:  
   `"<workflow_type>": build_<name>_graph,`

4. Restart the CLI / API. Intent and planner pick up new workflows from the registry automatically.

## Chaining research → content generation

- **Same run, two workflows:** planner produces  
  `workflow_plan: ["research_intelligence", "content_generation"]`.  
  After the first subgraph finishes, `artifacts.workflow_data` is still on `AgentState`; `research_node` sets **`content_prompt`** from the synthesis so **`heygen_video_agent`** can use it.

- **Single workflow:** implement a custom graph that runs research nodes then HeyGen in one subgraph (only if you want one persist boundary).

## “Skills” for your customers

Files under `docs/registry/skills/` are **documentation + product mapping**, not runtime routers yet.

For **app users**, define skills in **your** database (name, description, default `workflow_plan`, default `constraints`) and have your API set:

```text
workflow_plan = ["research_intelligence", "content_generation"]
constraints = {"filters": {"video_prefix": "Energetic tone:"}}
```

So they get one-click flows without touching this repository.

## Checklist (copy-paste)

- [ ] `.env.mcp` updated; MCP restarted  
- [ ] Tool in `registry.yaml` (if new API)  
- [ ] Agent node + `docs/registry/agents/*.yaml`  
- [ ] `docs/registry/workflows/*.yaml`  
- [ ] `graph/workflows/*_graph.py`  
- [ ] `_init_workflow_subgraphs()` one new entry  
- [ ] `report_node` extended only if you add a **new** `workflow_data` shape (prefer reusing `content_generation`, etc.)
