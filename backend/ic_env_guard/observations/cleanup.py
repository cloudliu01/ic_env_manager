import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol


class ExpirationService(Protocol):
    def delete_expired(self, cutoff: datetime, *, limit: int) -> int: ...


async def expiration_loop(
    service: ExpirationService,
    *,
    interval_seconds: int,
    retention_seconds: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_error: Callable[[], None] = lambda: None,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            cutoff = clock().astimezone(UTC) - timedelta(seconds=retention_seconds)
            while service.delete_expired(cutoff, limit=500) == 500:
                await asyncio.sleep(0)
        except Exception:
            on_error()
