import sqlite3
from pathlib import Path

from ic_env_guard.db.migrations import _ensure_no_failed_migrations, _load_migration

CONTROL_PLANE_MIGRATIONS_DIR = Path(__file__).parent.parent / "control_plane_migrations"


def run_control_plane_migrations(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        _ensure_no_failed_migrations(connection)
        for path in sorted(
            CONTROL_PLANE_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.py")
        ):
            module = _load_migration(path)
            module.upgrade(connection)
        _ensure_no_failed_migrations(connection)
    finally:
        connection.close()
