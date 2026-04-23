# Add a new agent (Markdown, no Python)

1. Copy [`docs/registry/agents/_template.agent.md`](../docs/registry/agents/_template.agent.md) to `docs/registry/agents/<your_agent_id>.md`.
2. Edit the YAML frontmatter:
   - `agent_id` — unique slug (`a-z`, `0-9`, `_`, `-`, `.`).
   - `workflow_types` — include `markdown_chain` and/or `"*"` for all workflows.
   - `tools` — MCP tool IDs exactly as in [`mcp_server/registry.yaml`](../mcp_server/registry.yaml).
3. Write the Markdown body as **system-style instructions** (role, output format, safety).
4. Validate:

```bash
cd leonidas-agents
python backend/cli.py validate
```

5. List agents via API: `GET /registry/agents` or CLI `python backend/cli.py agents`.

6. To run a chain, edit `docs/registry/workflows/markdown_chain.yaml` → `agent_steps` list (ordered).

For linear chains you do **not** add Python nodes — the generic executor runs each step.
