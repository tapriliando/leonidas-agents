# MCP server and client

## Roles

- **mcp-server** — Registers tools, holds secrets (env), rate limits, logging. Agents call **tool ids**, not raw vendor URLs.
- **backend `mcp_client.py`** — Single place to invoke MCP (HTTP or stdio per your Phase 2 choice).

## Registry

`mcp-server/registry.yaml` lists tool ids and metadata. Each tool has an implementation under `mcp-server/tools/` (one file per external API when you add them).

## Phase 0

- `main.py` serves **GET /health** with the stdlib HTTP server (no FastAPI required).
- `registry.yaml` has `tools: {}` until Phase 2.

## Security

- API keys in environment or secret store — never in prompts or committed config.
