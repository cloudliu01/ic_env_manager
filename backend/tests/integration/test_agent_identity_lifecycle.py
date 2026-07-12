import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.bootstrap.identity import (
    InstanceIdentityError,
    SQLiteIdentityInitialization,
    load_or_create_instance_id,
)
from ic_env_guard.config.models import AppConfig
from ic_env_guard.db.migrations import run_migrations


def _config(tmp_path) -> AppConfig:
    token = tmp_path / "token"
    token.write_text("secret-token\n", encoding="utf-8")
    token.chmod(0o600)
    return AppConfig.model_validate(
        {
            "mode": "agent",
            "auth": {"token_file": token},
            "state_database": tmp_path / "state.db",
        }
    )


def _marker(db_path) -> str | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM agent_metadata WHERE key = 'instance_identity_initialized'"
        ).fetchone()
    return row[0] if row else None


def test_new_install_creates_identity_once_and_persists_marker(tmp_path):
    config = _config(tmp_path)
    first = build_agent_container(config, config.state_database)
    first_id = first.instance_id
    first.database_engine.dispose()

    second = build_agent_container(config, config.state_database)
    second.database_engine.dispose()

    assert second.instance_id == first_id
    assert _marker(config.state_database) == "initialized"
    assert stat.S_IMODE((tmp_path / "instance-id").stat().st_mode) == 0o600


def test_pre_v2_legacy_database_may_create_identity_once(tmp_path):
    config = _config(tmp_path)
    with sqlite3.connect(config.state_database) as connection:
        connection.execute("CREATE TABLE schema_versions (version TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO schema_versions VALUES ('0001_initial')")

    container = build_agent_container(config, config.state_database)
    container.database_engine.dispose()

    assert (tmp_path / "instance-id").is_file()
    assert _marker(config.state_database) == "initialized"


def test_existing_v2_database_without_identity_or_marker_fails_closed(tmp_path):
    config = _config(tmp_path)
    run_migrations(config.state_database)

    with pytest.raises(InstanceIdentityError, match="restore instance identity"):
        build_agent_container(config, config.state_database)

    assert not (tmp_path / "instance-id").exists()
    assert _marker(config.state_database) is None


def test_deleting_initialized_identity_fails_closed(tmp_path):
    config = _config(tmp_path)
    container = build_agent_container(config, config.state_database)
    container.database_engine.dispose()
    (tmp_path / "instance-id").unlink()

    with pytest.raises(InstanceIdentityError, match="restore instance identity"):
        build_agent_container(config, config.state_database)


def test_existing_identity_safely_backfills_missing_marker(tmp_path):
    config = _config(tmp_path)
    run_migrations(config.state_database)
    expected = uuid4()
    identity_path = tmp_path / "instance-id"
    identity_path.write_text(f"{expected}\n", encoding="ascii")
    identity_path.chmod(0o600)

    container = build_agent_container(config, config.state_database)
    container.database_engine.dispose()

    assert container.instance_id == expected
    assert _marker(config.state_database) == "initialized"


def test_concurrent_first_initialization_returns_one_atomic_identity(tmp_path):
    db_path = tmp_path / "state.db"
    run_migrations(db_path)
    marker = SQLiteIdentityInitialization(db_path, bootstrap_allowed=True)
    identity_path = tmp_path / "instance-id"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: load_or_create_instance_id(
                    identity_path,
                    allow_create=True,
                    initialization=marker,
                ),
                range(2),
            )
        )

    assert results[0] == results[1]
    assert _marker(db_path) == "initialized"


def test_concurrent_new_container_builds_share_cutoff_and_identity(tmp_path):
    config = _config(tmp_path)

    def build(_index):
        container = build_agent_container(config, config.state_database)
        try:
            return container.instance_id
        finally:
            container.database_engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(build, range(2)))

    assert results[0] == results[1]
    assert _marker(config.state_database) == "initialized"


def test_pre_v2_upgrade_retries_after_migrations_commit_before_identity(tmp_path, monkeypatch):
    from ic_env_guard.bootstrap import composition

    config = _config(tmp_path)
    real_migrate = composition.run_migrations
    calls = 0

    def migrate_then_crash(database):
        nonlocal calls
        calls += 1
        real_migrate(database)
        if calls == 1:
            raise RuntimeError("crash after migrations")

    monkeypatch.setattr(composition, "run_migrations", migrate_then_crash)
    with pytest.raises(RuntimeError, match="crash after migrations"):
        build_agent_container(config, config.state_database)

    intent = config.state_database.with_name(".instance-identity-bootstrap")
    assert intent.is_file()
    assert stat.S_IMODE(intent.stat().st_mode) == 0o600

    container = build_agent_container(config, config.state_database)
    container.database_engine.dispose()

    assert (tmp_path / "instance-id").is_file()
    assert _marker(config.state_database) == "initialized"
    assert not intent.exists()


@pytest.mark.parametrize("unsafe", ["symlink", "permissions", "content"])
def test_existing_v2_rejects_forged_or_unsafe_bootstrap_intent(tmp_path, unsafe):
    config = _config(tmp_path)
    run_migrations(config.state_database)
    intent = config.state_database.with_name(".instance-identity-bootstrap")
    if unsafe == "symlink":
        target = tmp_path / "target"
        target.write_text("instance-identity-bootstrap-v1\n", encoding="ascii")
        intent.symlink_to(target)
    else:
        content = "wrong\n" if unsafe == "content" else "instance-identity-bootstrap-v1\n"
        intent.write_text(content, encoding="ascii")
        intent.chmod(0o644 if unsafe == "permissions" else 0o600)

    with pytest.raises(InstanceIdentityError, match="bootstrap intent"):
        build_agent_container(config, config.state_database)

    assert not (tmp_path / "instance-id").exists()


def test_stale_valid_intent_cannot_replace_an_initialized_missing_identity(tmp_path):
    config = _config(tmp_path)
    container = build_agent_container(config, config.state_database)
    container.database_engine.dispose()
    (tmp_path / "instance-id").unlink()
    intent = config.state_database.with_name(".instance-identity-bootstrap")
    intent.write_text("instance-identity-bootstrap-v1\n", encoding="ascii")
    intent.chmod(0o600)

    with pytest.raises(InstanceIdentityError, match="restore instance identity"):
        build_agent_container(config, config.state_database)

    assert not (tmp_path / "instance-id").exists()
