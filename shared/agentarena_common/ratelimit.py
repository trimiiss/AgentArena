"""In-process token bucket used to protect write routes.

The limit applies per replica, so the effective limit grows with the replica
count. The ingress adds a coarse per-IP limit in front of this one.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, status

from agentarena_common.metrics import RATE_LIMITED


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    def __init__(
        self,
        rate_per_second: float,
        burst: int,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate_per_second
        self.burst = burst
        self.max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def check(self, key: str) -> float | None:
        """Take one token. Returns None when allowed, else seconds to wait."""
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.burst), updated_at=now)
            self._buckets[key] = bucket
            if len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(key)
            bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated_at) * self.rate)
            bucket.updated_at = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return None
        return (1 - bucket.tokens) / self.rate


def enforce(limiter: TokenBucketLimiter, key: str, *, service: str, scope: str) -> None:
    retry_after = limiter.check(key)
    if retry_after is not None:
        RATE_LIMITED.labels(service, scope).inc()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(1, round(retry_after)))},
        )
