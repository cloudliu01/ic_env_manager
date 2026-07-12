import sqlite3
from pathlib import Path

from ic_env_guard.db.migrations import (
    MigrationError,
    _ensure_no_failed_migrations,
    _load_migration,
)

CONTROL_PLANE_MIGRATIONS_DIR = Path(__file__).parent.parent / "control_plane_migrations"


def run_control_plane_migrations(db_path: Path) -> None:
    paths = sorted(
        CONTROL_PLANE_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.py")
    )
    supported_version = max(int(path.name[:4]) for path in paths)
    connection = sqlite3.connect(db_path)
    try:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if user_version > supported_version:
            raise MigrationError(
                "database uses a newer control-plane schema version"
            )
        _ensure_no_failed_migrations(connection)
        for path in paths:
            module = _load_migration(path)
            module.upgrade(connection)
        _ensure_no_failed_migrations(connection)
    finally:
        connection.close()
