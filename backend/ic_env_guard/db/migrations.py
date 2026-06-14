import importlib.util
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


class MigrationError(Exception):
    pass


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise MigrationError(f"cannot load migration {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema_versions_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_versions'"
    ).fetchone()
    return row is not None


def _ensure_no_failed_migrations(connection: sqlite3.Connection) -> None:
    if not _schema_versions_exists(connection):
        return
    failed = connection.execute(
        "SELECT version, failure_reason FROM schema_versions "
        "WHERE result = 'failed' ORDER BY version"
    ).fetchone()
    if failed:
        version, reason = failed
        detail = f": {reason}" if reason else ""
        raise MigrationError(f"failed migration {version}{detail}")


def run_migrations(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        _ensure_no_failed_migrations(connection)
        for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.py")):
            module = _load_migration(path)
            module.upgrade(connection)
        _ensure_no_failed_migrations(connection)
    finally:
        connection.close()
