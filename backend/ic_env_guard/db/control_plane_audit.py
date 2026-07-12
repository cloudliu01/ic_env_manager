from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, Select, String, Text, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from ic_env_guard.db.repositories import bounded_text
from ic_env_guard.db.session import Base


class ControlPlaneAuditEvent(Base):
    __tablename__ = "control_plane_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_addr: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    dispatch_state: Mapped[str] = mapped_column(String(32), nullable=False)
    upstream_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "actor_id": self.actor_id,
            "source_addr": self.source_addr,
            "agent_id": self.agent_id,
            "operation": self.operation,
            "target": self.target,
            "result": self.result,
            "dispatch_state": self.dispatch_state,
            "upstream_status": self.upstream_status,
            "correlation_id": self.correlation_id,
            "failure_category": self.failure_category,
        }


@dataclass(frozen=True)
class ControlPlaneAuditEventCreate:
    actor_id: str | None
    source_addr: str | None
    agent_id: str | None
    operation: str
    target: str
    correlation_id: str | None


class ControlPlaneAuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_intent(self, event: ControlPlaneAuditEventCreate) -> ControlPlaneAuditEvent:
        row = ControlPlaneAuditEvent(
            timestamp=datetime.now(UTC),
            actor_id=bounded_text(event.actor_id, 255),
            source_addr=bounded_text(event.source_addr, 255),
            agent_id=bounded_text(event.agent_id, 64),
            operation=bounded_text(event.operation, 255) or event.operation,
            target=bounded_text(event.target, 255) or event.target,
            result="pending",
            dispatch_state="not_dispatched",
            correlation_id=bounded_text(event.correlation_id, 255),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def finalize(
        self,
        event_id: int,
        *,
        result: str,
        dispatch_state: str,
        upstream_status: int | None = None,
        failure_category: str | None = None,
        failure_reason: str | None = None,
    ) -> ControlPlaneAuditEvent:
        row = self.session.get(ControlPlaneAuditEvent, event_id)
        if row is None:
            raise ValueError("control-plane audit event not found")
        row.result = bounded_text(result, 32) or result
        row.dispatch_state = bounded_text(dispatch_state, 32) or dispatch_state
        row.upstream_status = upstream_status
        row.failure_category = bounded_text(failure_category, 64)
        row.failure_reason = bounded_text(failure_reason)
        return row

    def finalize_pending(
        self,
        event_id: int,
        *,
        expected_operation: str,
        expected_target: str,
        result: str,
        dispatch_state: str,
        failure_category: str | None = None,
    ) -> bool:
        outcome = self.session.execute(
            update(ControlPlaneAuditEvent)
            .where(
                ControlPlaneAuditEvent.id == event_id,
                ControlPlaneAuditEvent.result == "pending",
                ControlPlaneAuditEvent.operation == bounded_text(expected_operation, 255),
                ControlPlaneAuditEvent.target == bounded_text(expected_target, 255),
            )
            .values(
                result=bounded_text(result, 32) or result,
                dispatch_state=bounded_text(dispatch_state, 32) or dispatch_state,
                failure_category=bounded_text(failure_category, 64),
            )
        )
        return outcome.rowcount == 1

    def query(
        self,
        *,
        limit: int = 100,
        agent_id: str | None = None,
        operation: str | None = None,
        result: str | None = None,
        correlation_id: str | None = None,
    ) -> Select[tuple[ControlPlaneAuditEvent]]:
        statement = select(ControlPlaneAuditEvent)
        if agent_id:
            statement = statement.where(
                ControlPlaneAuditEvent.agent_id == bounded_text(agent_id, 64)
            )
        if operation:
            statement = statement.where(
                ControlPlaneAuditEvent.operation == bounded_text(operation, 255)
            )
        if result:
            statement = statement.where(ControlPlaneAuditEvent.result == bounded_text(result, 32))
        if correlation_id:
            statement = statement.where(
                ControlPlaneAuditEvent.correlation_id == bounded_text(correlation_id, 255)
            )
        return statement.order_by(ControlPlaneAuditEvent.timestamp.desc()).limit(
            max(1, min(limit, 1000))
        )

    def list_events(
        self,
        *,
        limit: int = 100,
        agent_id: str | None = None,
        operation: str | None = None,
        result: str | None = None,
        correlation_id: str | None = None,
    ) -> list[dict[str, object]]:
        rows = self.session.execute(
            self.query(
                limit=limit,
                agent_id=agent_id,
                operation=operation,
                result=result,
                correlation_id=correlation_id,
            )
        ).scalars()
        return [row.to_safe_dict() for row in rows]
