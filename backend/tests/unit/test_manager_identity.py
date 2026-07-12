from uuid import UUID

import pytest

from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.storage.manager_registry import ManagerRegistryRepository


@pytest.mark.unit
def test_manager_id_is_created_once_and_persisted(tmp_path):
    database = tmp_path / "control-plane.db"
    run_control_plane_migrations(database)

    first_engine = create_sqlite_engine(database)
    first = ManagerRegistryRepository(first_engine).get_or_create_manager_id()
    first_engine.dispose()

    second_engine = create_sqlite_engine(database)
    second = ManagerRegistryRepository(second_engine).get_or_create_manager_id()
    second_engine.dispose()

    assert first == second
    assert str(first) == str(UUID(str(first)))
