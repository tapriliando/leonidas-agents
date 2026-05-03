# Quickstart in 10 Minutes

This guide gets a first-time contributor from install to first successful run quickly.

## 0) Use the correct folder

Your shell should be in the **repository root**: the directory that contains `pyproject.toml`, `backend/`, and `mcp_server/`.

If `python backend/cli.py` works but imports fail, you are usually either in the wrong folder or you skipped the install step.

## 1) Install

```bash
cd leonidas-agents
python -m pip install -e ".[api,dev]"
```

This installs **LangGraph** and other orchestration deps. If you see `No module named 'langgraph'`, re-run the line above with the **same** Python you use for `python backend/cli.py`.

## 2) Start MCP tool gateway

You **do not** need a `.env.mcp` file to start the server. Optional API keys go in `.env.mcp` (copy from `.env.mcp.example`); when that file exists it is loaded automatically by `mcp_server` (no `uvicorn --env-file` required).

```bash
uvicorn mcp_server.main:app --host 127.0.0.1 --port 8001
```

Keep this terminal running.

## 3) Run guided onboarding

In another terminal (still from repo root):

```bash
python backend/cli.py quickstart
```

What this does:
- creates/updates `.env.backend`
- creates optional `.env.mcp` from `.env.mcp.example` if missing
- prompts for `OPENAI_API_KEY` (unless `--non-interactive`)
- validates Markdown agent definitions
- checks MCP health, repo layout, and `langgraph`

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
