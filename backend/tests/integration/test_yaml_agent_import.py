import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ic_env_guard.config.models import AgentConfig
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.fleet.importer import (
    AgentConfigImportError,
    AgentConfigImportOutcomeUncertain,
    import_yaml_agents_once,
)
from ic_env_guard.fleet.models import AgentQuery
from ic_env_guard.storage.manager_registry import (
    AgentStatusRepository,
    ManagerRegistryRepository,
)


def _token(path: Path, value: str, *, mode: int = 0o600) -> Path:
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(mode)
    return path


def _agent(tmp_path: Path, agent_id: str, **changes) -> AgentConfig:
    values = {
        "id": agent_id,
        "name": f"Agent {agent_id}",
        "base_url": f"https://{agent_id}.example",
        "token_file": _token(tmp_path / f"{agent_id}.token", f"secret-{agent_id}"),
        "enabled": True,
    }
    values.update(changes)
    return AgentConfig(**values)


@pytest.fixture
def import_context(tmp_path):
    database = tmp_path / "control-plane.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    store = CredentialStore(tmp_path / "credentials")
    yield engine, store, ManagerRegistryRepository(engine), AgentStatusRepository(engine)
    engine.dispose()


def _marker(engine):
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT value FROM manager_metadata WHERE key='yaml_agents_imported_v1'"
        ).first()
    return row[0] if row else None


@pytest.mark.integration
def test_first_import_commits_agents_and_initial_status_atomically(tmp_path, import_context):
    engine, store, registry, statuses = import_context
    agents = [
        _agent(tmp_path, "lab-01"),
        _agent(tmp_path, "lab-02", enabled=False),
    ]

    assert import_yaml_agents_once(engine, store, agents, manager_token=b"manager-secret")

    records = registry.list(AgentQuery()).items
    assert [record.agent_id for record in records] == ["lab-01", "lab-02"]
    assert records[0].instance_id is None
    assert records[0].source == "config_import"
    assert records[0].enrollment_method.value == "legacy_admin_token"
    assert records[0].normalized_endpoint == "https://lab-01.example:443"
    assert store.read(records[0].credential_ref) == b"secret-lab-01"
    assert statuses.get("lab-01").connection_status == "unknown"
    assert statuses.get("lab-02").connection_status == "disabled"
    assert _marker(engine) == "complete"


@pytest.mark.integration
def test_empty_yaml_marks_import_complete_and_deleted_last_agent_does_not_resurrect(
    tmp_path, import_context
):
    engine, store, registry, _ = import_context
    assert import_yaml_agents_once(engine, store, [], manager_token=b"manager")
    assert _marker(engine) == "complete"
    assert not import_yaml_agents_once(
        engine, store, [_agent(tmp_path, "late")], manager_token=b"manager"
    )
    assert registry.list(AgentQuery()).items == ()


@pytest.mark.integration
def test_pre_marker_nonempty_registry_is_marked_without_reading_yaml(
    tmp_path, import_context
):
    engine, store, registry, _ = import_context
    original = _agent(tmp_path, "lab-01")
    assert import_yaml_agents_once(engine, store, [original], manager_token=b"manager")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DELETE FROM manager_metadata WHERE key='yaml_agents_imported_v1'"
        )
    original.token_file.unlink()

    assert not import_yaml_agents_once(engine, store, [original], manager_token=b"manager")
    assert _marker(engine) == "complete"
    assert len(registry.list(AgentQuery()).items) == 1


@pytest.mark.integration
@pytest.mark.parametrize("duplicate", ["id", "endpoint"])
def test_import_rejects_duplicate_identity_fields_before_copy(
    tmp_path, import_context, duplicate
):
    engine, store, registry, _ = import_context
    first = _agent(tmp_path, "lab-01")
    second = _agent(tmp_path, "lab-02")
    if duplicate == "id":
        second.id = first.id
    else:
        second.base_url = "HTTPS://LAB-01.EXAMPLE:443/"

    with pytest.raises(AgentConfigImportError):
        import_yaml_agents_once(engine, store, [first, second], manager_token=b"manager")

    assert registry.list(AgentQuery()).items == ()
    assert tuple(store.directory.iterdir()) == ()


@pytest.mark.integration
def test_import_rejects_unsafe_and_manager_shared_tokens(tmp_path, import_context):
    engine, store, registry, _ = import_context
    unsafe = _agent(
        tmp_path,
        "unsafe",
        token_file=_token(tmp_path / "unsafe-other.token", "unsafe", mode=0o644),
        enabled=False,
    )
    shared = _agent(
        tmp_path,
        "shared",
        token_file=_token(tmp_path / "shared-other.token", "manager-secret"),
    )

    for agent in (unsafe, shared):
        with pytest.raises(AgentConfigImportError):
            import_yaml_agents_once(
                engine, store, [agent], manager_token=b"manager-secret"
            )
        assert registry.list(AgentQuery()).items == ()
        assert tuple(store.directory.iterdir()) == ()


@pytest.mark.integration
def test_disabled_agent_without_token_imports_but_cannot_be_enabled(tmp_path, import_context):
    engine, store, registry, statuses = import_context
    disabled = _agent(tmp_path, "disabled", enabled=False, token_file=None)

    assert import_yaml_agents_once(engine, store, [disabled], manager_token=b"manager")

    record = registry.get("disabled")
    assert record.enabled is False
    assert record.transport_profile_id == "legacy-disabled-no-credential"
    assert statuses.get("disabled").connection_status == "disabled"


class _FailingCopyStore(CredentialStore):
    def __init__(self, directory: Path, fail_on: int) -> None:
        super().__init__(directory)
        self._calls = 0
        self._fail_on = fail_on
        self.armed = False

    def put(self, secret: bytes) -> str:
        self._calls += 1
        if self.armed and self._calls == self._fail_on:
            raise CredentialStoreError("injected copy failure")
        return super().put(secret)


@pytest.mark.integration
@pytest.mark.parametrize("fail_on", [1, 2])
def test_copy_failure_removes_only_new_credentials_and_rolls_back_rows(
    tmp_path, import_context, fail_on
):
    engine, _store, registry, _ = import_context
    store = _FailingCopyStore(tmp_path / f"failing-{fail_on}", fail_on)
    retained = store.put(b"pre-existing")
    store._calls = 0
    store.armed = True

    with pytest.raises(AgentConfigImportError):
        import_yaml_agents_once(
            engine,
            store,
            [_agent(tmp_path, "lab-01"), _agent(tmp_path, "lab-02")],
            manager_token=b"manager",
        )

    assert registry.list(AgentQuery()).items == ()
    assert {entry.name for entry in store.directory.iterdir()} == {retained}


@pytest.mark.integration
@pytest.mark.parametrize("boundary", ["_insert_agent", "_insert_initial_status"])
def test_database_failure_removes_copied_credentials(
    tmp_path, import_context, monkeypatch, boundary
):
    engine, store, registry, _ = import_context

    def fail_status(*_args, **_kwargs):
        raise RuntimeError("injected status insert failure")

    monkeypatch.setattr(f"ic_env_guard.fleet.importer.{boundary}", fail_status)
    with pytest.raises(AgentConfigImportError):
        import_yaml_agents_once(
            engine, store, [_agent(tmp_path, "lab-01")], manager_token=b"manager"
        )

    assert registry.list(AgentQuery()).items == ()
    assert tuple(store.directory.iterdir()) == ()


@pytest.mark.integration
def test_commit_then_raise_is_verified_as_success_without_credential_cleanup(
    tmp_path, import_context, monkeypatch
):
    engine, store, registry, _ = import_context

    def commit_then_raise(connection):
        connection.commit()
        raise RuntimeError("lost commit acknowledgement")

    monkeypatch.setattr("ic_env_guard.fleet.importer._commit_transaction", commit_then_raise)
    assert import_yaml_agents_once(
        engine, store, [_agent(tmp_path, "lab-01")], manager_token=b"manager"
    )
    record = registry.get("lab-01")
    assert store.read(record.credential_ref) == b"secret-lab-01"
    assert _marker(engine) == "complete"


@pytest.mark.integration
def test_commit_and_rollback_errors_keep_credentials_when_verification_is_unavailable(
    tmp_path, import_context, monkeypatch
):
    engine, store, _registry, _ = import_context

    monkeypatch.setattr(
        "ic_env_guard.fleet.importer._commit_transaction",
        lambda _connection: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )
    monkeypatch.setattr(
        "ic_env_guard.fleet.importer._prepare_import_if_needed", lambda _engine: True
    )
    monkeypatch.setattr(
        "ic_env_guard.fleet.importer._rollback_transaction",
        lambda _connection: (_ for _ in ()).throw(RuntimeError("rollback failed")),
    )
    monkeypatch.setattr(
        "ic_env_guard.fleet.importer._verify_committed_import",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("verification failed")),
    )

    with pytest.raises(AgentConfigImportOutcomeUncertain):
        import_yaml_agents_once(
            engine, store, [_agent(tmp_path, "lab-01")], manager_token=b"manager"
        )
    assert len(tuple(store.directory.iterdir())) == 1


class _BarrierStore(CredentialStore):
    def __init__(self, directory: Path, barrier: threading.Barrier) -> None:
        super().__init__(directory)
        self._barrier = barrier

    def put(self, secret: bytes) -> str:
        reference = super().put(secret)
        self._barrier.wait(timeout=5)
        return reference


@pytest.mark.integration
def test_concurrent_initial_imports_converge_and_loser_cleans_only_its_credential(
    tmp_path, import_context
):
    engine, _store, registry, _ = import_context
    barrier = threading.Barrier(2)
    stores = [
        _BarrierStore(tmp_path / "concurrent-credentials", barrier),
        _BarrierStore(tmp_path / "concurrent-credentials", barrier),
    ]

    def run(store):
        return import_yaml_agents_once(
            engine, store, [_agent(tmp_path, "lab-01")], manager_token=b"manager"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, stores))

    assert sorted(outcomes) == [False, True]
    record = registry.get("lab-01")
    assert {entry.name for entry in stores[0].directory.iterdir()} == {
        record.credential_ref
    }


@pytest.mark.integration
def test_existing_registry_skips_yaml_without_reading_its_tokens(
    tmp_path, import_context
):
    engine, store, registry, _ = import_context
    original = _agent(tmp_path, "lab-01")
    assert import_yaml_agents_once(engine, store, [original], manager_token=b"manager")
    original.token_file.unlink()

    assert not import_yaml_agents_once(
        engine,
        store,
        [_agent(tmp_path, "yaml-new-name", name="Ignored YAML")],
        manager_token=b"manager",
    )
    assert [item.agent_id for item in registry.list(AgentQuery()).items] == ["lab-01"]


@pytest.mark.integration
def test_source_token_read_rejects_symlink_swap_and_oversize(
    tmp_path, import_context, monkeypatch
):
    engine, store, registry, _ = import_context
    target = _token(tmp_path / "target.token", "secret")
    symlink = tmp_path / "symlink.token"
    symlink.symlink_to(target)
    oversized = _token(tmp_path / "oversized.token", "x" * (64 * 1024 + 1))

    for path in (symlink, oversized):
        with pytest.raises(AgentConfigImportError):
            import_yaml_agents_once(
                engine,
                store,
                [_agent(tmp_path, "lab-01", token_file=path)],
                manager_token=b"manager",
            )

    original_open = os.open
    source = _token(tmp_path / "swap.token", "before")

    def swap_then_open(path, flags, *args):
        if Path(path) == source:
            replacement = _token(tmp_path / "replacement.token", "after")
            replacement.replace(source)
        return original_open(path, flags, *args)

    monkeypatch.setattr("ic_env_guard.fleet.importer.os.open", swap_then_open)
    with pytest.raises(AgentConfigImportError):
        import_yaml_agents_once(
            engine,
            store,
            [_agent(tmp_path, "lab-01", token_file=source)],
            manager_token=b"manager",
        )
    assert registry.list(AgentQuery()).items == ()
    assert tuple(store.directory.iterdir()) == ()
