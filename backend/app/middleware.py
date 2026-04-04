"""
FastAPI middleware: request ID, duration header, structured logging, safe 500 JSON.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("mas.api")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        t0 = time.perf_counter()
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception("unhandled_error request_id=%s path=%s", rid, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": rid},
            )
        dur_ms = int((time.perf_counter() - t0) * 1000)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Duration-Ms"] = str(dur_ms)
        logger.info(
            "request_completed method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            dur_ms,
            rid,
        )
        return response
