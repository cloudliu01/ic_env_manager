from dataclasses import replace

import httpx
import pytest

from ic_env_guard.bootstrap.composition import build_manager_container
from ic_env_guard.config.models import AgentConfig, AppConfig, AuthConfig, ControlPlaneConfig


def _token(tmp_path, name: str, value: str):
    path = tmp_path / name
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _config(tmp_path, *, yaml_name: str) -> AppConfig:
    return AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=_token(tmp_path, "manager.token", "manager-secret")),
        control_plane=ControlPlaneConfig(
            audit_database=tmp_path / "control-plane.db",
            credential_directory=tmp_path / "credentials",
        ),
        agents=[
            AgentConfig(
                id="lab-01",
                name=yaml_name,
                base_url="https://lab-01.example",
                token_file=_token(tmp_path, "agent.token", "agent-secret"),
            )
        ],
    )


@pytest.mark.integration
def test_sqlite_registry_changes_survive_restart_and_yaml_is_ignored(tmp_path):
    first = build_manager_container(_config(tmp_path, yaml_name="YAML name"))
    record = first.registry_repository.get("lab-01")
    first.registry_repository.update_if_revision(
        replace(record, display_name="Web rename"), expected_revision=record.revision
    )
    first.database_engine.dispose()

    second = build_manager_container(_config(tmp_path, yaml_name="Changed YAML name"))
    try:
        assert second.agent_registry.get("lab-01").name == "Web rename"
    finally:
        second.database_engine.dispose()


@pytest.mark.integration
def test_deleting_last_sqlite_agent_does_not_reimport_yaml_on_restart(tmp_path):
    config = _config(tmp_path, yaml_name="Lab 01")
    first = build_manager_container(config)
    first.registry_repository.delete("lab-01")
    first.database_engine.dispose()

    second = build_manager_container(config)
    try:
        assert second.agent_registry.list_configs() == []
    finally:
        second.database_engine.dispose()


@pytest.mark.integration
def test_disabled_import_is_not_routable_after_restart(tmp_path):
    config = _config(tmp_path, yaml_name="Lab 01")
    config.agents[0].enabled = False
    first = build_manager_container(config)
    first.database_engine.dispose()

    second = build_manager_container(config)
    try:
        assert second.agent_registry.get("lab-01").enabled is False
        assert second.agent_registry.summary("lab-01")["status"] == "disabled"
    finally:
        second.database_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manager_client_reads_imported_token_through_credential_store(
    tmp_path, monkeypatch
):
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"ok": True})

    container = build_manager_container(_config(tmp_path, yaml_name="Lab 01"))
    container.agent_client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "ic_env_guard.agents.client.load_bearer_token",
        lambda _path: (_ for _ in ()).throw(AssertionError("raw path read")),
    )
    try:
        response = await container.agent_client.request(
            container.agent_registry.get("lab-01"), "GET", "/api/capabilities"
        )
        assert response.status_code == 200
        assert observed["authorization"] == "Bearer agent-secret"
    finally:
        await container.agent_client.aclose()
        container.database_engine.dispose()
