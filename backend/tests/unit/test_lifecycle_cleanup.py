import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from ic_env_guard.bootstrap.composition import (
    build_agent_container,
    build_manager_container,
)
from ic_env_guard.bootstrap.lifecycle import close_container, create_lifespan
from ic_env_guard.config.models import (
    AppConfig,
    AuthConfig,
    ControlPlaneConfig,
    EnrollmentConfig,
)
from ic_env_guard.enrollment.socket_server import SocketSecurityError


def _token(tmp_path):
    path = tmp_path / "token"
    path.write_text("admin-token\n", encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_startup_failure_cancels_tasks_stops_socket_and_disposes_once(tmp_path):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    config = AppConfig(
        auth=AuthConfig(token_file=_token(tmp_path)),
        enrollment=EnrollmentConfig(socket_path=unsafe / "enroll.sock"),
    )
    container = build_agent_container(
        config, tmp_path / "state.db", tmp_path / "instance-id"
    )
    app = FastAPI()
    dispose = Mock(wraps=container.database_engine.dispose)
    stop = Mock(wraps=container.enrollment_socket_server.stop)
    container.database_engine.dispose = dispose
    container.enrollment_socket_server.stop = stop

    with pytest.raises(SocketSecurityError, match="directory permissions"):
        async with create_lifespan(container)(app):
            raise AssertionError("startup failure must not enter the application")

    for name in (
        "metrics_refresh_task",
        "observation_cleanup_task",
        "log_cleanup_task",
    ):
        task = getattr(app.state, name)
        assert task.done()
        assert task.cancelled()
    stop.assert_called_once_with()
    dispose.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manager_partial_startup_failure_closes_created_resources(tmp_path, monkeypatch):
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=_token(tmp_path)),
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
    )
    container = build_manager_container(config)
    app = FastAPI()
    dispose = Mock(wraps=container.database_engine.dispose)
    close_client = AsyncMock(wraps=container.agent_client.aclose)
    container.database_engine.dispose = dispose
    container.agent_client.aclose = close_client
    original_create_task = asyncio.create_task
    created = []

    def fail_second_task(coroutine):
        if created:
            coroutine.close()
            raise RuntimeError("availability task failed")
        task = original_create_task(coroutine)
        created.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", fail_second_task)

    with pytest.raises(RuntimeError, match="availability task failed"):
        async with create_lifespan(container)(app):
            raise AssertionError("startup failure must not enter the application")

    assert len(created) == 1
    assert created[0].done()
    assert created[0].cancelled()
    dispose.assert_called_once_with()
    close_client.assert_awaited_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manager_client_closes_when_database_disposal_fails(tmp_path):
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=_token(tmp_path)),
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
    )
    container = build_manager_container(config)
    close_client = AsyncMock(wraps=container.agent_client.aclose)
    container.database_engine.dispose = Mock(side_effect=RuntimeError("dispose failed"))
    container.agent_client.aclose = close_client

    with pytest.raises(RuntimeError, match="dispose failed"):
        await close_container(container)

    close_client.assert_awaited_once_with()
