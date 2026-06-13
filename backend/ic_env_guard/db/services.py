from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ic_env_guard.db.session import Base


class ServiceStatus(StrEnum):
    CONFIGURED = "configured"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    EXITED = "exited"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ManagedServiceRecord(Base):
    __tablename__ = "managed_services"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    systemd_unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    env_keys: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    allowed_operations: Mapped[str] = mapped_column(Text, nullable=False)
    autostart: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    restart_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    start_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    healthcheck_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_rules_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class ServiceStateRecord(Base):
    __tablename__ = "service_state"

    service_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stopped_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restart_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    health_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class ServiceRunRecord(Base):
    __tablename__ = "service_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[str] = mapped_column(String(64), nullable=False)
    stopped_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ServiceOperationRecord(Base):
    __tablename__ = "service_operations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_addr: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ServiceEventRecord(Base):
    __tablename__ = "service_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class HealthCheckResultRecord(Base):
    __tablename__ = "healthcheck_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


@dataclass
class ServiceRuntime:
    id: str
    name: str
    command: str | None = None
    systemd_unit: str | None = None
    allowed_operations: list[str] = field(
        default_factory=lambda: ["start", "stop", "restart", "status"]
    )
    description: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    autostart: bool = False
    restart_policy: str = "never"
    start_timeout_seconds: int = 30
    stop_timeout_seconds: int = 30
    status: str = ServiceStatus.CONFIGURED.value
    pid: int | None = None
    health_status: str = "unknown"
    restart_count: int = 0
    last_error: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "health_status": self.health_status,
            "allowed_operations": self.allowed_operations,
        }

    def detail(self) -> dict[str, object]:
        data = self.summary()
        data.update(
            {
                "restart_policy": self.restart_policy,
                "pid": self.pid,
                "started_at": None,
                "updated_at": self.updated_at,
                "last_error": self.last_error,
            }
        )
        return data
