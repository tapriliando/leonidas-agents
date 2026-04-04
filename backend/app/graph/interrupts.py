"""Helpers for LangGraph interrupt detection and serialization."""

from __future__ import annotations

from typing import Any


def result_has_interrupt(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("__interrupt__"))
    return False


def interrupt_values_from_result(result: dict) -> list[Any]:
    raw = result.get("__interrupt__") or []
    return [getattr(x, "value", x) for x in raw]


def interrupts_from_snapshot(snap: Any) -> list[dict[str, Any]]:
    """Build JSON-serializable interrupt list from graph.get_state / aget_state snapshot."""
    ints = getattr(snap, "interrupts", None) or ()
    out: list[dict[str, Any]] = []
    for x in ints:
        out.append(
            {
                "id": getattr(x, "id", None),
                "value": getattr(x, "value", x),
            }
        )
    return out
