"""AI Gateway edge concerns: rate limiting, cost ceiling, access gate.

The architecture puts "AuthN/Z · rate limit · quota · cost ceiling" at the
edge, before anything else runs. On a laptop none of it matters. On a public
URL all of it does, and the cost ceiling is the one that stops a stranger
turning your OpenAI key into a bill overnight.

Deliberately in-process: a dict and a lock. That is correct for a single
container and honestly wrong for more than one, which is what
`docs/DEPLOY.md` says. Swap the bucket store for Redis when you scale out;
the middleware contract does not change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Paths that must answer even when a caller is rate limited or the ceiling is
# hit, or the platform's own health checks start failing the deploy.
EXEMPT_PREFIXES = (
    "/health",
    "/ready",
    "/ui",
    "/docs",
    "/redoc",
    "/swagger",
    "/openapi.json",
    "/favicon",
)

# Only metered endpoints count against the budget. Reading a trace is free;
# starting an agent run is not.
METERED_PREFIXES = ("/api/chat", "/api/eval/run")


@dataclass
class Bucket:
    tokens: float
    updated: float
    day: str = ""
    day_count: int = 0

    def take(self, rate_per_min: float, burst: int) -> bool:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(float(burst), self.tokens + elapsed * (rate_per_min / 60.0))
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


@dataclass
class Meter:
    """Daily spend ceiling, tracked in whole requests rather than money.

    Requests are the unit you can enforce at the edge before knowing the token
    count. `cost_per_task_inr` from the eval runs tells you what one is worth,
    so the conversion is a multiplication you do when setting the number.
    """

    daily_limit: int
    day: str = ""
    count: int = 0

    def allow(self) -> bool:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.count = today, 0
        if self.daily_limit <= 0:
            return True
        if self.count >= self.daily_limit:
            return False
        self.count += 1
        return True

    def snapshot(self) -> dict:
        return {"day": self.day, "used": self.count, "limit": self.daily_limit}


class GatewayMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        rate_per_min: int = 30,
        burst: int = 10,
        daily_limit: int = 0,
        access_password: str = "",
    ):
        super().__init__(app)
        self.rate_per_min = rate_per_min
        self.burst = burst
        self.access_password = access_password
        self.meter = Meter(daily_limit=daily_limit)
        self._buckets: dict[str, Bucket] = {}

    def _client(self, request: Request) -> str:
        # Behind Render/Fly/Cloud Run the real address is in X-Forwarded-For.
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _bucket(self, key: str) -> Bucket:
        b = self._buckets.get(key)
        if b is None:
            b = Bucket(tokens=float(self.burst), updated=time.monotonic())
            self._buckets[key] = b
        if len(self._buckets) > 5000:  # crude bound; restart clears it
            self._buckets.clear()
            self._buckets[key] = b
        return b

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(EXEMPT_PREFIXES) or path == "/":
            return await call_next(request)

        if self.access_password:
            supplied = request.headers.get("x-demo-password") or request.query_params.get("k", "")
            if supplied != self.access_password:
                return JSONResponse(
                    {"detail": "This demo is password protected. Ask the owner for the key."},
                    status_code=401,
                )

        if not self._bucket(self._client(request)).take(self.rate_per_min, self.burst):
            return JSONResponse(
                {"detail": f"Rate limit: {self.rate_per_min} requests per minute. Try again shortly."},
                status_code=429,
                headers={"Retry-After": "10"},
            )

        if path.startswith(METERED_PREFIXES) and not self.meter.allow():
            return JSONResponse(
                {
                    "detail": (
                        "The daily budget for this demo is spent. It resets at 00:00 UTC. "
                        "Run it locally for unlimited use: github.com/<you>/retailmind-ai"
                    ),
                    "budget": self.meter.snapshot(),
                },
                status_code=429,
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rate_per_min)
        if self.meter.daily_limit:
            remaining = max(0, self.meter.daily_limit - self.meter.count)
            response.headers["X-Budget-Remaining"] = str(remaining)
        return response
