"""Rate Limiting Middleware — Per-IP request throttling."""
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timedelta
import os
import structlog

logger = structlog.get_logger()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter. Per IP, per minute."""

    def __init__(self, app):
        super().__init__(app)
        self._requests: dict[str, list[datetime]] = defaultdict(list)
        self._limit = int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "60"))

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=1)

        # Clean old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > window_start
        ]

        # Check limit
        if len(self._requests[client_ip]) >= self._limit:
            logger.warning("rate_limit_exceeded", ip=client_ip, limit=self._limit)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {self._limit} requests per minute."
            )

        self._requests[client_ip].append(now)
        response = await call_next(request)
        return response
