from __future__ import annotations

from fastapi import HTTPException, Request


def get_graph(request: Request):
    g = getattr(request.app.state, "graph", None)
    if g is None:
        raise HTTPException(status_code=503, detail="Graph not initialized")
    return g
