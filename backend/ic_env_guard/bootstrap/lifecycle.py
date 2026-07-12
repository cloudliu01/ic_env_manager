import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ic_env_guard.bootstrap.composition import AgentContainer, ManagerContainer
from ic_env_guard.fleet.probes import FleetProbeService
from ic_env_guard.logs.cleanup import expiration_loop as log_expiration_loop
from ic_env_guard.metrics.collector import MetricsCollector
from ic_env_guard.observations.cleanup import expiration_loop as observation_expiration_loop


async def _metrics_refresh_loop(collector: MetricsCollector, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        collector.refresh()


async def _fleet_probe_loop(probes: FleetProbeService, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await probes.probe_all()


async def _cancel_tasks(*tasks: asyncio.Task[None] | None) -> None:
    active = tuple(task for task in tasks if task is not None)
    for task in active:
        task.cancel()
    results = await asyncio.gather(*active, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException) and not isinstance(
            result, asyncio.CancelledError
        ):
            raise result


async def close_container(container: AgentContainer | ManagerContainer) -> None:
    try:
        container.database_engine.dispose()
    finally:
        if isinstance(container, ManagerContainer):
            await container.agent_client.aclose()


def create_lifespan(container: AgentContainer | ManagerContainer):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.lifecycle_started = True
        app.state.lifecycle_cleanup_complete = False
        refresh_task: asyncio.Task[None] | None = None
        fleet_probe_task: asyncio.Task[None] | None = None
        observation_cleanup_task: asyncio.Task[None] | None = None
        log_cleanup_task: asyncio.Task[None] | None = None
        enrollment_socket_server = None
        try:
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
                if container.ssh_enrollment_adapter is not None:
                    await container.ssh_enrollment_adapter.check_available()
                await container.enrollment_orchestrator.recover_and_cleanup()
                if container.fleet_probe_service is not None:
                    fleet_probe_task = asyncio.create_task(
                        _fleet_probe_loop(
                            container.fleet_probe_service,
                            container.config.control_plane.poll_interval_seconds,
                        )
                    )
                    app.state.fleet_probe_task = fleet_probe_task
            else:
                enrollment_socket_server = container.enrollment_socket_server
                observation_config = container.config.observations if container.config else None
                interval = (
                    observation_config.cleanup_interval_seconds if observation_config else 60
                )
                retention = (
                    observation_config.expired_retention_seconds
                    if observation_config
                    else 86400
                )
                failures = container.metrics_registry._names_to_collectors[
                    "ic_env_guard_cleanup_failures"
                ]
                observation_cleanup_task = asyncio.create_task(
                    observation_expiration_loop(
                        container.observation_service,
                        interval_seconds=interval,
                        retention_seconds=retention,
                        on_error=lambda: failures.labels(resource="observations").inc(),
                    )
                )
                log_cleanup_task = asyncio.create_task(
                    log_expiration_loop(
                        container.log_source_service,
                        interval_seconds=interval,
                        retention_seconds=retention,
                        on_error=lambda: failures.labels(resource="logs").inc(),
                    )
                )
                app.state.observation_cleanup_task = observation_cleanup_task
                app.state.log_cleanup_task = log_cleanup_task
            if enrollment_socket_server is not None:
                enrollment_socket_server.start()
            yield
        finally:
            try:
                if isinstance(container, ManagerContainer):
                    await container.enrollment_orchestrator.shutdown()
                if enrollment_socket_server is not None:
                    enrollment_socket_server.stop()
            finally:
                try:
                    await _cancel_tasks(
                        refresh_task,
                        fleet_probe_task,
                        observation_cleanup_task,
                        log_cleanup_task,
                    )
                finally:
                    try:
                        await close_container(container)
                    finally:
                        app.state.lifecycle_cleanup_complete = True

    return lifespan
