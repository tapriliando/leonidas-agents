# Thesis evaluation hooks

## Metrics (JSONL)

When the API completes a run (`POST /run`), an append-only line is written to `var/metrics.jsonl` unless `EVAL_METRICS_DISABLED=1`.

Each line is JSON:

```json
{"ts":"...","event":"run.complete","data":{"run_id":"...","status":"...","workflow_type":"..."}}
```

Use this file for **before/after** comparisons (e.g. Markdown-only agents vs hardcoded nodes).

## Benchmarks

Named scenarios live in `backend/app/evaluation/benchmarks.py` (`BENCHMARKS` dict). Extend with your own tasks and parse `var/metrics.jsonl` in your thesis notebook.

## Gateway events

WebSocket `GET ws://.../gateway/ws` (see OpenClaw-inspired handshake: `connect.challenge` → `connect` req → `hello` in `res` payload). Events include `tick` (transport liveness) and `heartbeat` (agent-level cadence). Correlate with run latency in experiments.
