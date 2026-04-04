# Agents and skills registry pattern

## Agent

- One **narrow** responsibility per module under `backend/app/agents/`.
- Each agent is a **node function**: same `(state) -> dict` contract.
- Declare which state keys you read and write (in docs or YAML registry) for maintainability.

## Tool

- Concrete capability: `mcp.web_search`, `mcp.gmaps_places_search`, etc.
- Implemented once on the MCP server; many agents can reuse it.

## Skill

- A **composed capability** (e.g. “lead pipeline”) that maps to one or more agents + tools.
- Keep skills **declarative** (YAML/markdown registry) so you add workflows without rewriting core code.

## Adding a new agent

1. Add `agents/<name>.py` with the node function.
2. Register in your skills doc or `registry` artifact.
3. Wire the node into the right `graph/*.py` and add routers in `conditions.py` if needed.

## Adding a new tool

1. Implement handler under `mcp-server/tools/`.
2. Add entry to `registry.yaml`.
3. Call via `mcp_client.call_tool(tool_id, payload)` from nodes.
