"""
OpenClaw-inspired JSON frames over WebSocket.

Frames:
  - Server → client: { "type": "event", "event": str, "payload"?: object, "seq"?: int }
  - Client → server: { "type": "req", "id": str, "method": str, "params"?: object }
  - Server → client: { "type": "res", "id": str, "ok": bool, "payload"?: object, "error"?: object }
"""

from __future__ import annotations

import time
from typing import Any, Optional

TICK_INTERVAL_MS = 30_000
HEARTBEAT_AGENT_INTERVAL_SEC_DEFAULT = 600


def event_frame(event: str, payload: Optional[dict[str, Any]] = None, seq: Optional[int] = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "event", "event": event}
    if payload is not None:
        msg["payload"] = payload
    if seq is not None:
        msg["seq"] = seq
    return msg


def res_frame(req_id: str, ok: bool, payload: Any = None, error: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "res", "id": req_id, "ok": ok}
    if payload is not None:
        out["payload"] = payload
    if error is not None:
        out["error"] = error
    return out


def tick_payload() -> dict[str, Any]:
    return {"ts": int(time.time() * 1000)}
