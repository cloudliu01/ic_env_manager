from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class LoginRateLimiter:
    def __init__(
        self,
        *,
        capacity: int = 5,
        refill_seconds: float = 60.0,
        max_sources: int = 1024,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if capacity < 1 or refill_seconds <= 0 or max_sources < 1:
            raise ValueError("login rate-limit settings must be positive")
        self._capacity = capacity
        self._refill_seconds = refill_seconds
        self._max_sources = max_sources
        self._clock = clock
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = Lock()

    def allow(self, source_addr: str) -> bool:
        with self._lock:
            return self._allow(source_addr)

    def _allow(self, source_addr: str) -> bool:
        now = self._clock()
        bucket = self._buckets.get(source_addr)
        if bucket is None:
            if len(self._buckets) >= self._max_sources:
                self._buckets.popitem(last=False)
            bucket = _Bucket(tokens=float(self._capacity), updated_at=now)
            self._buckets[source_addr] = bucket
        else:
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                float(self._capacity), bucket.tokens + elapsed / self._refill_seconds
            )
            bucket.updated_at = now
            self._buckets.move_to_end(source_addr)
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True
