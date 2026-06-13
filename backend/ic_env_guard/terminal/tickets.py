from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass
class TerminalTicket:
    ticket: str
    terminal_id: str
    expires_at: float
    consumed: bool = False


class TerminalTicketManager:
    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = min(ttl_seconds, 60)
        self._tickets: dict[str, TerminalTicket] = {}

    def issue(self, terminal_id: str) -> TerminalTicket:
        ticket = secrets.token_urlsafe(24)
        record = TerminalTicket(
            ticket=ticket,
            terminal_id=terminal_id,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._tickets[ticket] = record
        return record

    def consume(self, ticket: str, terminal_id: str) -> bool:
        record = self._tickets.get(ticket)
        if record is None:
            return False
        if record.consumed or record.terminal_id != terminal_id or record.expires_at < time.time():
            return False
        record.consumed = True
        return True
