"""
Named benchmark scenarios for thesis evaluation (or CI smoke).
"""

from __future__ import annotations

BENCHMARKS: dict[str, dict[str, str]] = {
    "markdown_chain_smoke": {
        "user_query": "What is LangGraph in one sentence?",
        "expected_workflow_hint": "markdown_chain",
    },
}


def list_benchmarks() -> list[str]:
    return sorted(BENCHMARKS.keys())


def get_benchmark(name: str) -> dict[str, str]:
    if name not in BENCHMARKS:
        raise KeyError(name)
    return dict(BENCHMARKS[name])
