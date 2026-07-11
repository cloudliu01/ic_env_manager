import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.bootstrap.composition import AgentContainer, ManagerContainer
from ic_env_guard.metrics.collector import MetricsCollector


async def _metrics_refresh_loop(collector: MetricsCollector, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        collector.refresh()


async def _agent_availability_probe_loop(
    availability: AgentAvailabilityService, interval_seconds: int
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await availability.probe_all()


def create_lifespan(container: AgentContainer | ManagerContainer):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        refresh_task: asyncio.Task[None] | None = None
        availability_probe_task: asyncio.Task[None] | None = None
        metrics_config = container.metrics_config
        if metrics_config.enabled:
            refresh_task = asyncio.create_task(
                _metrics_refresh_loop(
                    container.metrics_collector,
                    metrics_config.collect_interval_seconds,
                )
            )
            app.state.metrics_refresh_task = refresh_task
        if isinstance(container, ManagerContainer):
            availability_probe_task = asyncio.create_task(
                _agent_availability_probe_loop(
                    container.agent_availability,
                    container.config.control_plane.poll_interval_seconds,
                )
            )
            app.state.agent_availability_probe_task = availability_probe_task
        try:
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await refresh_task
            if availability_probe_task is not None:
                availability_probe_task.cancel()
                with suppress(asyncio.CancelledError):
                    await availability_probe_task
            container.database_engine.dispose()
            if isinstance(container, ManagerContainer):
                await container.agent_client.aclose()

    return lifespan
