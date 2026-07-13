from __future__ import annotations

import os
import select
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ptyprocess import PtyProcess

from ic_env_guard.terminal.replay_buffer import ReplayBuffer, ReplayHistory


@dataclass
class TerminalSession:
    id: str
    owner: str
    title: str
    command: str
    process: PtyProcess | None
    pid: int | None
    rows: int
    cols: int
    status: str
    output_cursor: int
    replay_buffer_start_cursor: int
    idle_timeout_minutes: int
    created_at: float
    last_active_at: float
    exited_at: float | None = None
    closed_at: float | None = None
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
            "created_at": _iso(self.created_at),
            "last_active_at": _iso(self.last_active_at),
            "exited_at": _iso(self.exited_at) if self.exited_at else None,
            "closed_at": _iso(self.closed_at) if self.closed_at else None,
            "close_reason": self.close_reason,
        }


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


class TerminalManager:
    def __init__(
        self,
        shell: str = "/bin/sh",
        idle_timeout_minutes: int = 60,
        replay_buffer_bytes: int = 2 * 1024 * 1024,
        exited_retention_minutes: int = 30,
    ) -> None:
        if idle_timeout_minutes < 30 or idle_timeout_minutes > 120:
            raise ValueError("idle_timeout_minutes must be between 30 and 120")
        if exited_retention_minutes < 0 or exited_retention_minutes > 120:
            raise ValueError("exited_retention_minutes must be between 0 and 120")
        self.shell = shell
        self.idle_timeout_minutes = idle_timeout_minutes
        self.replay_buffer_bytes = replay_buffer_bytes
        self.exited_retention_minutes = exited_retention_minutes
        self.sessions: dict[str, TerminalSession] = {}
        self._buffers: dict[str, ReplayBuffer] = {}

    def create_terminal(
        self,
        title: str = "Terminal",
        rows: int = 24,
        cols: int = 80,
        owner: str = "local-admin",
        cwd: str | None = None,
    ) -> TerminalSession:
        terminal_id = str(uuid.uuid4())
        process = PtyProcess.spawn([self.shell], cwd=cwd, dimensions=(rows, cols))
        now = time.time()
        session = TerminalSession(
            id=terminal_id,
            owner=owner,
            title=title or "Terminal",
            command=self.shell,
            process=process,
            pid=process.pid,
            rows=rows,
            cols=cols,
            status="running",
            output_cursor=0,
            replay_buffer_start_cursor=0,
            idle_timeout_minutes=self.idle_timeout_minutes,
            created_at=now,
            last_active_at=now,
        )
        self.sessions[terminal_id] = session
        self._buffers[terminal_id] = ReplayBuffer(self.replay_buffer_bytes)
        return session

    def list(self) -> list[TerminalSession]:
        self._poll_exits()
        self._purge_expired()
        return list(self.sessions.values())

    def get(self, terminal_id: str) -> TerminalSession:
        self._poll_exits()
        self._purge_expired()
        try:
            return self.sessions[terminal_id]
        except KeyError:
            raise KeyError(terminal_id) from None

    def write(self, terminal_id: str, text: str) -> None:
        session = self.get(terminal_id)
        if session.process is None or session.status != "running":
            raise RuntimeError("terminal is not running")
        session.process.write(text.encode())
        session.last_active_at = time.time()

    def read_available(self, terminal_id: str, timeout: float = 0.05) -> str:
        session = self.get(terminal_id)
        if session.process is None:
            return ""
        output: list[str] = []
        while True:
            ready, _, _ = select.select([session.process.fd], [], [], timeout)
            if not ready:
                break
            try:
                data = os.read(session.process.fd, 4096)
            except OSError:
                self._mark_exited(session, "shell_exited")
                break
            if not data:
                self._mark_exited(session, "shell_exited")
                break
            text = data.decode("utf-8", errors="replace")
            output.append(text)
            buffer = self._buffers[terminal_id]
            buffer.append(text)
            session.output_cursor = buffer.cursor
            session.replay_buffer_start_cursor = buffer.start_cursor
            session.last_active_at = time.time()
            timeout = 0
        return "".join(output)

    def read_until(self, terminal_id: str, needle: str, timeout: float = 5) -> str:
        deadline = time.time() + timeout
        output = ""
        while time.time() < deadline:
            output += self.read_available(terminal_id, timeout=0.05)
            if needle in output:
                return output
        return output

    def resize(self, terminal_id: str, rows: int, cols: int) -> None:
        session = self.get(terminal_id)
        session.rows = rows
        session.cols = cols
        if session.process is not None and session.status == "running":
            session.process.setwinsize(rows, cols)
        session.last_active_at = time.time()

    def history(self, terminal_id: str, cursor: int) -> ReplayHistory:
        session = self.get(terminal_id)
        buffer = self._buffers[terminal_id]
        output, from_cursor, truncated = buffer.read_from(cursor)
        return ReplayHistory(
            terminal_id=terminal_id,
            from_cursor=from_cursor,
            to_cursor=buffer.cursor,
            buffer_start_cursor=buffer.start_cursor,
            truncated=truncated,
            status=session.status,
            output=output,
        )

    def close(self, terminal_id: str, reason: str = "user_closed") -> TerminalSession:
        session = self.get(terminal_id)
        if session.process is not None and session.status == "running":
            try:
                session.process.terminate(force=True)
            except Exception:
                if session.pid:
                    try:
                        os.kill(session.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        session.status = "closed" if reason == "user_closed" else "timed_out"
        session.closed_at = time.time()
        session.close_reason = reason
        session.process = None
        return session

    def cleanup_idle_sessions(self) -> None:
        now = time.time()
        for session in list(self.sessions.values()):
            if (
                session.status == "running"
                and now - session.last_active_at > session.idle_timeout_minutes * 60
            ):
                self.close(session.id, reason="idle_timeout")

    def _poll_exits(self) -> None:
        for session in self.sessions.values():
            if (
                session.process is not None
                and session.status == "running"
                and not session.process.isalive()
            ):
                self._mark_exited(session, "shell_exited")

    def _purge_expired(self) -> None:
        now = time.time()
        retention_seconds = self.exited_retention_minutes * 60
        for terminal_id, session in list(self.sessions.items()):
            terminal_at = (
                session.closed_at
                if session.status == "closed"
                else session.exited_at if session.status == "exited" else None
            )
            if terminal_at is not None and now - terminal_at >= retention_seconds:
                del self.sessions[terminal_id]
                self._buffers.pop(terminal_id, None)

    def _mark_exited(self, session: TerminalSession, reason: str) -> None:
        session.status = "exited"
        session.exited_at = time.time()
        session.close_reason = reason
        session.process = None
