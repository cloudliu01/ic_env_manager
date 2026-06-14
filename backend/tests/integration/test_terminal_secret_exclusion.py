import importlib.util
import sqlite3

import pytest

from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.db.migrations import MIGRATIONS_DIR
from ic_env_guard.terminal.manager import TerminalManager

_MIGRATION_PATH = MIGRATIONS_DIR / "0001_initial.py"
_SPEC = importlib.util.spec_from_file_location("migration_0001_initial", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
initial_migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(initial_migration)


@pytest.mark.integration
@pytest.mark.security
def test_terminal_input_output_not_written_to_audit_or_sqlite(tmp_path):
    secret_output = "terminal-secret-value"
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    connection.close()

    manager = TerminalManager(shell="/bin/sh")
    session = manager.create_terminal(title="secret")
    manager.write(session.id, f"printf {secret_output}\\n\n")
    assert secret_output in manager.read_until(session.id, secret_output, timeout=5)

    sqlalchemy_url = f"sqlite:///{db_path}"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(sqlalchemy_url)
    with Session(engine) as sqlalchemy_session:
        repo = AuditRepository(sqlalchemy_session)
        repo.add(
            AuditEventCreate(
                operation="terminal.create",
                target_type="terminal",
                target_id=session.id,
                result="success",
            )
        )
        sqlalchemy_session.commit()

    raw_db = db_path.read_bytes()
    assert secret_output.encode() not in raw_db
