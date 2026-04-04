# Phase 0 — Design Contract (AI I-Bridge, architecture-first)

**Status:** Locked for implementation phases. Product positioning stays **AI I-Bridge**; this phase optimizes for a **correct, extensible multi-agent architecture** before feature freeze.

---

## 1. Goals (Phase 0)

- **Multi-agent first:** LangGraph nodes, shared state, conditional edges, MCP as the only tool surface for external capabilities.
- **MVP workflow (first vertical slice):**
  1. **Research** — market and trends (e.g. web search + optional RAG over your corpus).
  2. **Map trends → categories** — derive categories/keywords that match trends (structured output in state).
  3. **Lead discovery** — scrape/query **Google Maps** (or equivalent GMaps-backed API) for businesses matching those categories.
  4. **Outreach** — generate **cold-call style emails** (and optionally SMS hooks) using contact fields from leads (email/phone where available).
  5. **Autonomy** — **heartbeat / scheduling** so workflows run on a timer or queue **without** the user chatting first (proactive agents).

- **Human-in-the-loop:** **Not required** in this phase (no approval gates; no pause-for-human nodes).

- **Operator experience:** Run via **`npm run dev`** (web app), with:
  - **Canvas:** visualize the **graph** and **state changes node-by-node** (step-through or stream).
  - **Sidebar:** show **current state** (JSON) and/or **relevant code** (e.g. active node, last transition).

---

## 2. LangGraph contracts (non-negotiable)

| Concept | Rule |
|--------|------|
| **Node** | A Python callable: `(state: AgentState) -> dict`. Return **only keys that changed**. No node calls another node directly. |
| **State** | Typed shared bus (`AgentState` in Phase 1). All reads/writes go through state updates the graph merges. |
| **Edges** | **Unconditional:** fixed next node. **Conditional:** router reads state → returns a **string key** → maps to next node. **Loops** = conditional edges that route backward. |
| **Orchestrator** | Either a dedicated node (planner/router) or the top-level graph entry; still follows the same node contract. |

---

## 3. Logical MVP graph (high level)

Order is illustrative; exact node names will match code in later phases.

```text
[scheduler_tick]  -->  [research_trends]
                              |
                              v
                         [map_trends_to_categories]
                              |
                              v
                         [gmaps_lead_fetch]
                              |
                              v
                         [compose_outreach]
                              |
                              v
                         [persist_run_summary]  -->  END
```

- **scheduler_tick:** entry when the run is triggered by cron/heartbeat (not by user message). Writes `run_id`, `trigger: "schedule"`, timestamps.
- **research_trends:** calls MCP web search (and optionally RAG); writes `trends`, `raw_research`.
- **map_trends_to_categories:** LLM or rules; writes `categories`, `search_queries`.
- **gmaps_lead_fetch:** MCP GMaps tool; writes `leads[]`.
- **compose_outreach:** MCP email drafting (and later send); writes `drafts[]` or `outreach_jobs[]`.
- **persist_run_summary:** optional; writes summary for UI/logs (and later PDF report).

**Autonomy:** A **separate process** (or FastAPI background task / worker) invokes the graph on a schedule; the graph does not assume a user message.

---

## 4. MCP scope (initial tools)

All external I/O goes through **MCP** with stable `tool_id` strings and JSON schemas.

| Area | Example tool IDs (illustrative) |
|------|----------------------------------|
| Web | `mcp.web_search` |
| Maps | `mcp.gmaps_places_search` (or your wrapped GMaps API) |
| Knowledge | `mcp.rag_query` |
| Email | `mcp.email_send` / `mcp.email_draft` |
| Reports | `mcp.pdf_report_generate` |
| Future | Add rows in `registry.yaml` + one handler module per tool — **no** agent-specific API keys inside agent code. |

---

## 5. Memory, caching, feedback (Phase 0 stance)

| Concern | Phase 0 decision |
|--------|-------------------|
| **Short-term memory** | `AgentState` + message/artifact fields inside the run. |
| **Long-term memory** | Deferred; when added: DB or vector store behind MCP, not inside prompts only. |
| **Caching** | Redis (or in-memory for dev) in a later phase; cache key = tool + normalized input. |
| **Feedback loops** | Quality checks can be **nodes** (e.g. retry enrichment) using **conditional edges**, not ad-hoc agent chat. |
| **Skills (`skill.md` / registry)** | Declarative registry: agent ↔ tools ↔ skills so new agents/tools are **registration + graph wiring**, not rewrites. |

---

## 6. Web app + visualization architecture

- **Frontend (npm):** e.g. Vite + React (or your stack). **Dev:** `npm run dev`.
- **Backend:** Python + LangGraph (later FastAPI). Exposes at minimum:
  - **Run graph** with **step events** or **stream**: after each node, emit `{ node_name, state_patch or full_state, step_index }` so the UI can animate the canvas.
- **Canvas:** graph layout (static from compiled graph or from metadata JSON). Highlight **active node** and **completed** edges per step.
- **Sidebar:** pretty-printed `AgentState` + optional snippet of node source (loaded from static paths or build-time map).

This keeps **your learning loop:** implement backend step → see one transition on canvas → read state in sidebar.

---

## 7. Scalability rules (adding agents/tools)

1. **New tool:** implement MCP handler + `registry.yaml` entry + schema tests.
2. **New agent:** new `agents/<name>.py` node function + declare `state_reads` / `state_writes` in registry doc.
3. **New workflow:** new `graph/<name>_graph.py` or compose subgraphs; reuse nodes where possible.
4. **State evolution:** extend `AgentState` with optional fields or a version field; avoid breaking existing nodes by using nested dicts (`artifacts`, `metrics`).

---

## 8. Repo layout (this repository)

The `project/` folder contains `backend/`, `mcp-server/`, `docs/`, `docker-compose.yml`, `pyproject.toml`. A **`frontend/`** folder for `npm run dev` and canvas UI can be added next to `backend/` when you start that phase.

---

## 9. What we explicitly defer

- Human approval gates.
- Full production auth, billing, and WhatsApp-first UX (can follow once the graph + MCP spine is stable).

---

## 10. Next: Phase 1

Expand `backend/app/state.py` with the full `AgentState` fields for research → categories → leads → outreach → run metadata. Add stub node functions that return `{}` after you approve the schema.

---

*End of Phase 0 design contract.*
