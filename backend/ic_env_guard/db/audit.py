from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from ic_env_guard.db.repositories import bounded_text
from ic_env_guard.db.session import Base

AuditResult = Literal["success", "denied", "rejected", "failed", "timeout"]


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_addr: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operation: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(255), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


@dataclass(frozen=True)
class AuditEventCreate:
    operation: str
    target_type: str
    result: AuditResult
    actor_id: str | None = None
    source_addr: str | None = None
    target_id: str | None = None
    failure_reason: str | None = None
    correlation_id: str | None = None

    def safe_failure_reason(self) -> str | None:
        if self.failure_reason is None:
            return None
        text = self.failure_reason
        for marker in ("token", "password", "private_key", "secret", "bearer"):
            text = text.replace(marker, "<redacted>")
        return bounded_text(text)


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: AuditEventCreate) -> AuditEvent:
        row = AuditEvent(
            timestamp=datetime.now(UTC),
            actor_id=event.actor_id,
            source_addr=event.source_addr,
            operation=bounded_text(event.operation, 255) or event.operation,
            target_type=bounded_text(event.target_type, 255) or event.target_type,
            target_id=bounded_text(event.target_id, 255),
            result=event.result,
            failure_reason=event.safe_failure_reason(),
            correlation_id=bounded_text(event.correlation_id, 255),
        )
        self.session.add(row)
        return row
