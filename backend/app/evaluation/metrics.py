"""
Append-only JSONL metrics for reproducible thesis experiments.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def metrics_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    out = root / "var" / "metrics.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def append_metric_event(event: str, data: dict[str, Any]) -> None:
    if os.getenv("EVAL_METRICS_DISABLED", "").lower() in ("1", "true", "yes"):
        return
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "data": data,
    }
    path = metrics_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_metrics_tail(max_lines: int = 50) -> list[dict[str, Any]]:
    path = metrics_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
