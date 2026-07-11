import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.observations.cleanup import expiration_loop


class CleanupService:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.cutoffs = []

    def delete_expired(self, cutoff, *, limit):
        self.cutoffs.append((cutoff, limit))
        value = next(self.batches)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_cleanup_uses_retention_cutoff_batches_and_yields(monkeypatch):
    now = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    service = CleanupService([500, 2])
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await expiration_loop(
            service,
            interval_seconds=60,
            retention_seconds=300,
            clock=lambda: now,
        )

    assert service.cutoffs == [(now - timedelta(seconds=300), 500)] * 2
    assert sleeps[:2] == [60, 0]


@pytest.mark.asyncio
async def test_cleanup_counts_failure_and_keeps_running(monkeypatch):
    service = CleanupService([RuntimeError("db unavailable"), 0])
    failures = []
    sleeps = 0

    async def fake_sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await expiration_loop(
            service,
            interval_seconds=1,
            retention_seconds=0,
            on_error=lambda: failures.append(1),
        )

    assert failures == [1]
    assert sleeps == 3
