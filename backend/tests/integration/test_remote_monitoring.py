import pytest
from fastapi.testclient import TestClient

from ic_env_guard.main import create_app
from ic_env_guard.monitoring.machines import MonitoredMachine


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return TestClient(create_app(token_file=token_file))


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer secret-token"}


@pytest.mark.integration
def test_remote_machine_snapshot_can_monitor_same_agent(client, auth_headers):
    create = client.post(
        "/api/monitoring/machines",
        headers=auth_headers,
        json={"name": "Loopback", "address": "127.0.0.1", "port": 8765, "key": "secret-token"},
    ).json()

    snapshot = client.get(
        f"/api/monitoring/machines/{create['id']}/snapshot", headers=auth_headers
    )

    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["host_id"] == create["id"]
    assert body["name"] == "Loopback"
    assert body["status"] in {"online", "offline"}
    assert "secret-token" not in snapshot.text


@pytest.mark.integration
def test_remote_machine_offline_snapshot_does_not_leak_key():
    class OfflineClient:
        def fetch(self, machine: MonitoredMachine) -> dict[str, object]:
            assert machine.key == "super-secret-key"
            return {
                "host_id": machine.id,
                "name": machine.name,
                "address": machine.endpoint,
                "status": "offline",
                "error": "connection refused",
                "sampled_at": "2026-06-13T00:00:00+00:00",
                "cpu": {"percent": 0},
                "memory": {"percent": 0, "used_bytes": 0, "total_bytes": 0},
                "swap": {"percent": 0, "used_bytes": 0, "total_bytes": 0},
                "disks": [],
                "network": [],
                "uptime_seconds": 0,
            }

    from ic_env_guard.monitoring.machines import MachineRegistry

    registry = MachineRegistry(remote_client=OfflineClient())
    machine = registry.add_machine(address="10.0.0.5", port=8765, key="super-secret-key")
    snapshot = registry.snapshot(str(machine["id"]))

    assert snapshot["status"] == "offline"
    assert "super-secret-key" not in str(snapshot)
