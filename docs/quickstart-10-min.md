# Quickstart in 10 Minutes

This guide gets a first-time contributor from install to first successful run quickly.

## 1) Install

```bash
cd leonidas-agents
pip install -e ".[api,dev]"
```

## 2) Start MCP tool gateway

```bash
uvicorn mcp_server.main:app --port 8001 --env-file .env.mcp
```

Keep this terminal running.

## 3) Run guided onboarding

In another terminal:

```bash
python backend/cli.py quickstart
```

What this does:
- creates/updates `.env.backend`
- prompts for `OPENAI_API_KEY` (unless `--non-interactive`)
- validates Markdown agent definitions
- checks MCP health

## 4) First task

```bash
python backend/cli.py "Explain the difference between LangGraph and a plain agent loop."
```

## 5) Optional: run API server

```bash
uvicorn app.api.main:app --app-dir backend --reload
```

Then call `POST /run`.

## Non-interactive setup (CI/classroom)

```bash
python backend/cli.py quickstart --openai-key "<YOUR_KEY>" --non-interactive
```

