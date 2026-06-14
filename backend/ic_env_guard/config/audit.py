from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from ic_env_guard.db.agent_state import AgentStateRepository
from ic_env_guard.db.migrations import run_migrations
from ic_env_guard.db.session import create_sqlite_engine


def audit_config_load(
    session: Session,
    config_path: Path | str,
    result: str,
    config_hash: str | None = None,
    failure_reason: str | None = None,
) -> None:
    AgentStateRepository(session).add_configuration_load_event(
        config_path=str(config_path),
        result=result,
        config_hash=config_hash,
        failure_reason=failure_reason,
    )


def audit_config_load_to_db(
    db_path: Path,
    config_path: Path | str,
    result: str,
    config_hash: str | None = None,
    failure_reason: str | None = None,
) -> None:
    run_migrations(db_path)
    engine = create_sqlite_engine(db_path)
    session = sessionmaker(bind=engine, future=True)()
    try:
        audit_config_load(session, config_path, result, config_hash, failure_reason)
        session.commit()
    finally:
        session.close()
        engine.dispose()
