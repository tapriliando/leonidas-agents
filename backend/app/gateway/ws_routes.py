"""WebSocket touchpoint gateway routes."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["gateway"])


@router.websocket("/gateway/ws")
async def gateway_ws(websocket: WebSocket):
    hub = getattr(websocket.app.state, "gateway_hub", None)
    if hub is None:
        await websocket.close(code=1011)
        return
    try:
        await hub.handle_websocket(websocket)
    except WebSocketDisconnect:
        pass
