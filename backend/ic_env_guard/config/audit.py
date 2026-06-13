from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ic_env_guard.db.agent_state import AgentStateRepository
from ic_env_guard.db.session import Base


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
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        audit_config_load(session, config_path, result, config_hash, failure_reason)
        session.commit()
    finally:
        session.close()
        engine.dispose()
