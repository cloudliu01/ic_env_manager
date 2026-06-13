from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from ic_env_guard.db.session import Base


class TerminalSessionRecord(Base):
    __tablename__ = "terminal_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    command: Mapped[str] = mapped_column(String(1024), nullable=False)
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cols: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    replay_buffer_start_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    idle_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


@dataclass
class TerminalSessionView:
    id: str
    owner: str
    title: str
    command: str
    pid: int | None
    rows: int
    cols: int
    status: str
    output_cursor: int
    replay_buffer_start_cursor: int
    idle_timeout_minutes: int
    created_at: datetime
    last_active_at: datetime
    exited_at: datetime | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner": self.owner,
            "title": self.title,
            "pid": self.pid,
            "rows": self.rows,
            "cols": self.cols,
            "status": self.status,
            "output_cursor": self.output_cursor,
            "replay_buffer_start_cursor": self.replay_buffer_start_cursor,
            "idle_timeout_minutes": self.idle_timeout_minutes,
            "created_at": self.created_at.isoformat(),
            "last_active_at": self.last_active_at.isoformat(),
            "exited_at": self.exited_at.isoformat() if self.exited_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_reason": self.close_reason,
        }


class TerminalSessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_view(self, view: TerminalSessionView) -> TerminalSessionRecord:
        row = self.session.get(TerminalSessionRecord, view.id)
        if row is None:
            row = TerminalSessionRecord(id=view.id)
            self.session.add(row)
        row.owner_id = view.owner
        row.title = view.title
        row.command = view.command
        row.pid = view.pid
        row.rows = view.rows
        row.cols = view.cols
        row.status = view.status
        row.output_cursor = view.output_cursor
        row.replay_buffer_start_cursor = view.replay_buffer_start_cursor
        row.idle_timeout_minutes = view.idle_timeout_minutes
        row.created_at = view.created_at
        row.last_active_at = view.last_active_at
        row.exited_at = view.exited_at
        row.closed_at = view.closed_at
        row.close_reason = view.close_reason
        return row


def utcnow() -> datetime:
    return datetime.now(UTC)
