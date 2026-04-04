# Phased roadmap (reference)

| Phase | Focus | Main artifacts |
|-------|--------|----------------|
| **0** | Repo layout, docs, stubs | `project/`, `docs/*`, stubs |
| **1** | `AgentState` complete for MVP pipeline | `backend/app/state.py` |
| **2** | MCP client + server tool dispatch | `mcp_client.py`, `mcp-server/*`, `registry.yaml` |
| **3–6** | Agent nodes + `StateGraph` + routers | `agents/`, `graph/lead_graph.py`, … |
| **7** | Redis cache, FastAPI, production hardening | `cache/`, `api/` |

**Frontend (canvas + sidebar):** add `frontend/` when you start; not part of the original backend-only tree — coordinate with Phase 6–7 for SSE/step streaming.
