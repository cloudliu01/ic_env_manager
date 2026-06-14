import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

MAX_TERMINAL_FRAME_BYTES = 64 * 1024


@dataclass(frozen=True)
class GatewayTicketReservation:
    id: str


@dataclass(frozen=True)
class GatewayTicket:
    ticket: str
    actor_id: str
    agent_id: str
    terminal_id: str
    intended_ws_path: str
    upstream_ticket: str
    expires_at: datetime


class GatewayTicketStore:
    def __init__(self, max_outstanding: int = 128) -> None:
        self._max_outstanding = max_outstanding
        self._reservations: set[str] = set()
        self._tickets: dict[str, GatewayTicket] = {}
        self._lock = threading.Lock()

    def reserve(self) -> GatewayTicketReservation | None:
        with self._lock:
            self._prune_expired_unlocked()
            if len(self._reservations) + len(self._tickets) >= self._max_outstanding:
                return None
            reservation = GatewayTicketReservation(secrets.token_urlsafe(16))
            self._reservations.add(reservation.id)
            return reservation

    def release_reservation(self, reservation: GatewayTicketReservation | None) -> None:
        with self._lock:
            if reservation is not None:
                self._reservations.discard(reservation.id)

    def commit(
        self,
        reservation: GatewayTicketReservation,
        *,
        actor_id: str,
        agent_id: str,
        terminal_id: str,
        intended_ws_path: str,
        upstream_ticket: str,
        expires_at: datetime,
    ) -> GatewayTicket:
        with self._lock:
            if reservation.id not in self._reservations:
                raise ValueError("gateway ticket reservation is not active")
            self._reservations.remove(reservation.id)
            record = GatewayTicket(
                ticket=secrets.token_urlsafe(24),
                actor_id=actor_id,
                agent_id=agent_id,
                terminal_id=terminal_id,
                intended_ws_path=intended_ws_path,
                upstream_ticket=upstream_ticket,
                expires_at=expires_at,
            )
            self._tickets[record.ticket] = record
            return record

    def consume(
        self,
        ticket: str,
        *,
        actor_id: str,
        agent_id: str,
        terminal_id: str,
        intended_ws_path: str,
    ) -> GatewayTicket | None:
        status, record = self.consume_for_websocket(
            ticket,
            agent_id=agent_id,
            terminal_id=terminal_id,
            intended_ws_path=intended_ws_path,
            actor_id=actor_id,
        )
        return record if status == "ok" else None

    def consume_for_websocket(
        self,
        ticket: str,
        *,
        agent_id: str,
        terminal_id: str,
        intended_ws_path: str,
        actor_id: str | None = None,
    ) -> tuple[str, GatewayTicket | None]:
        with self._lock:
            record = self._tickets.get(ticket)
            if record is None or record.expires_at <= datetime.now(UTC):
                self._tickets.pop(ticket, None)
                return "invalid", None
            if actor_id is not None and record.actor_id != actor_id:
                return "actor_mismatch", record
            if (
                record.agent_id != agent_id
                or record.terminal_id != terminal_id
                or record.intended_ws_path != intended_ws_path
            ):
                return "invalid", None
            return "ok", self._tickets.pop(ticket)

    def _prune_expired_unlocked(self) -> None:
        now = datetime.now(UTC)
        expired = [ticket for ticket, record in self._tickets.items() if record.expires_at <= now]
        for ticket in expired:
            self._tickets.pop(ticket, None)


class GatewayProxyLimiter:
    def __init__(self, max_active: int = 64) -> None:
        self._max_active = max_active
        self._active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self._max_active:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active > 0:
                self._active -= 1
