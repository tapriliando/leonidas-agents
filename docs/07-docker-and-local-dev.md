# Docker and local development

## Redis (`docker-compose.yml`)

```bash
docker compose up -d
```

Exposes Redis on `localhost:6379` for Phase 7 caching.

## Backend (from `project/`)

```bash
pip install -e ".[dev]"
pytest
```

## MCP server health (Phase 0)

```bash
cd mcp-server
python main.py
# GET http://127.0.0.1:8080/health → {"status": "ok"}
```

## Python path

Tests and apps expect `backend` on `PYTHONPATH` (see `pyproject.toml` `[tool.pytest.ini_options]` `pythonpath`).
