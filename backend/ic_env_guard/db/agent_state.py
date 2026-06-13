from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from ic_env_guard.db.repositories import bounded_text
from ic_env_guard.db.session import Base


class AgentLifecycleEvent(Base):
    __tablename__ = "agent_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigurationLoadEvent(Base):
    __tablename__ = "configuration_load_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    config_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_lifecycle_event(
        self, event_type: str, status: str, message: str | None = None
    ) -> AgentLifecycleEvent:
        row = AgentLifecycleEvent(
            event_type=bounded_text(event_type, 255) or event_type,
            status=bounded_text(status, 64) or status,
            message=bounded_text(message),
            created_at=datetime.now(UTC),
        )
        self.session.add(row)
        return row

    def add_configuration_load_event(
        self,
        config_path: str,
        result: str,
        config_hash: str | None = None,
        failure_reason: str | None = None,
    ) -> ConfigurationLoadEvent:
        row = ConfigurationLoadEvent(
            config_path=bounded_text(config_path, 1024) or config_path,
            config_hash=bounded_text(config_hash, 255),
            result=bounded_text(result, 64) or result,
            failure_reason=bounded_text(failure_reason),
            loaded_at=datetime.now(UTC),
        )
        self.session.add(row)
        return row
