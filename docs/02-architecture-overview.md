# Architecture overview

## Layers

1. **User / scheduler** — chat, API, or cron triggers a graph run (no user prompt required for scheduled runs).
2. **Orchestration** — LangGraph: nodes (agents), state, edges, conditional routers.
3. **Tools** — MCP server exposes web search, GMaps, RAG, email, PDF, etc.; agents never hold API keys.
4. **Persistence** — Short-term: `AgentState`. Long-term: DB / vector store behind MCP or API (later phases).

## Data flow

```text
Trigger → StateGraph.invoke / stream → node updates state → next edge → … → END
                ↓
         MCP tools (HTTP to mcp-server)
```

## Principles

- **State** is runtime truth for one run; **prompts** are behavior, not storage.
- **Graph** decides order; agents do not call each other directly.
- **MCP** is the stable tool boundary for scaling new integrations.
