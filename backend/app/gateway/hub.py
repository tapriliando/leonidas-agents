"""
GatewayHub — fan-out events, transport tick, agent heartbeat broadcasts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Optional

from fastapi import WebSocket

from app.gateway.protocol import (
    HEARTBEAT_AGENT_INTERVAL_SEC_DEFAULT,
    TICK_INTERVAL_MS,
    event_frame,
    res_frame,
    tick_payload,
)

logger = logging.getLogger("mas.gateway")


class GatewayHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._clients: list[WebSocket] = []
        self._seq = 0
        self._last_heartbeat: dict[str, Any] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast_event(self, event: str, payload: Optional[dict[str, Any]] = None) -> None:
        async with self._lock:
            self._seq += 1
            seq = self._seq
            msg = event_frame(event, payload=payload, seq=seq)
        dead: list[WebSocket] = []
        async with self._lock:
            snapshot = list(self._clients)
        for ws in snapshot:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._clients:
                        self._clients.remove(ws)

    async def run_tick_loop(self) -> None:
        interval = TICK_INTERVAL_MS / 1000.0
        while True:
            await asyncio.sleep(interval)
            try:
                await self.broadcast_event("tick", tick_payload())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("tick broadcast error: %s", exc)

    async def run_agent_heartbeat_loop(self) -> None:
        raw = os.getenv("HEARTBEAT_AGENT_INTERVAL_SEC", str(HEARTBEAT_AGENT_INTERVAL_SEC_DEFAULT))
        try:
            interval = max(60.0, float(raw))
        except ValueError:
            interval = float(HEARTBEAT_AGENT_INTERVAL_SEC_DEFAULT)
        while True:
            await asyncio.sleep(interval)
            payload = {
                "kind": "agent",
                "message": "HEARTBEAT_OK",
                "ts": int(time.time() * 1000),
            }
            self._last_heartbeat = payload
            try:
                await self.broadcast_event("heartbeat", payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("heartbeat broadcast error: %s", exc)

    async def handle_websocket(self, websocket: WebSocket) -> None:
        await websocket.accept()
        nonce = str(uuid.uuid4())
        await websocket.send_json(event_frame("connect.challenge", {"nonce": nonce, "ts": int(time.time() * 1000)}))

        try:
            raw = await websocket.receive_json()
        except Exception:
            await websocket.close(code=1008)
            return

        if not isinstance(raw, dict) or raw.get("type") != "req" or raw.get("method") != "connect":
            rid = str(raw.get("id", "0")) if isinstance(raw, dict) else "0"
            await websocket.send_json(
                res_frame(
                    rid,
                    False,
                    error={"code": "INVALID_HANDSHAKE", "message": "expected connect req"},
                )
            )
            await websocket.close(code=1008)
            return

        params = raw.get("params") or {}
        if not isinstance(params, dict) or params.get("nonce") != nonce:
            await websocket.send_json(
                res_frame(
                    str(raw.get("id", "0")),
                    False,
                    error={"code": "NONCE_MISMATCH", "message": "nonce mismatch"},
                )
            )
            await websocket.close(code=1008)
            return

        req_id = str(raw.get("id") or "0")
        hello = {
            "tickIntervalMs": TICK_INTERVAL_MS,
            "features": {"events": ["tick", "heartbeat", "run.complete", "connect.challenge"]},
        }
        await websocket.send_json(res_frame(req_id, True, payload={"hello": hello}))

        async with self._lock:
            self._clients.append(websocket)

        try:
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "req":
                    mid = str(msg.get("id") or "0")
                    method = msg.get("method")
                    if method == "last-heartbeat":
                        await websocket.send_json(res_frame(mid, True, payload={"last": self._last_heartbeat}))
                    elif method == "ping":
                        await websocket.send_json(res_frame(mid, True, payload={"pong": True}))
                    else:
                        await websocket.send_json(
                            res_frame(mid, False, error={"code": "UNKNOWN_METHOD", "message": str(method)})
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            async with self._lock:
                if websocket in self._clients:
                    self._clients.remove(websocket)
            try:
                await websocket.close()
            except Exception:
                pass
