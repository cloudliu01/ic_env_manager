import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

MAX_TERMINAL_FRAME_BYTES = 64 * 1024


@dataclass(frozen=True)
class GatewayTicketReservation:
    id: str
    agent_id: str


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
    def __init__(
        self,
        max_outstanding: int = 128,
        *,
        clock: Callable[[], datetime] | None = None,
        durable_removal_blocker: Callable[[str], bool] | None = None,
    ) -> None:
        self._max_outstanding = max_outstanding
        self._clock = clock or (lambda: datetime.now(UTC))
        self._durable_removal_blocker = durable_removal_blocker
        self._reservations: dict[str, GatewayTicketReservation] = {}
        self._tickets: dict[str, GatewayTicket] = {}
        self._active: dict[str, GatewayTicket] = {}
        self._removing: set[str] = set()
        self._lock = threading.Lock()

    def reserve(self, agent_id: str = "") -> GatewayTicketReservation | None:
        with self._lock:
            self._prune_expired_unlocked()
            if agent_id in self._removing or self._durably_blocked(agent_id):
                return None
            if len(self._reservations) + len(self._tickets) >= self._max_outstanding:
                return None
            reservation = GatewayTicketReservation(secrets.token_urlsafe(16), agent_id)
            self._reservations[reservation.id] = reservation
            return reservation

    def release_reservation(self, reservation: GatewayTicketReservation | None) -> None:
        with self._lock:
            if reservation is not None:
                self._reservations.pop(reservation.id, None)

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
            stored = self._reservations.get(reservation.id)
            if stored is None:
                raise ValueError("gateway ticket reservation is not active")
            if stored.agent_id and stored.agent_id != agent_id:
                raise ValueError("gateway ticket reservation belongs to another agent")
            if agent_id in self._removing:
                raise ValueError("agent removal is in progress")
            self._reservations.pop(reservation.id)
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
            if record is None or record.expires_at <= self._clock():
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
            consumed = self._tickets.pop(ticket)
            self._active[consumed.ticket] = consumed
            return "ok", consumed

    def release_active(self, ticket: GatewayTicket) -> None:
        with self._lock:
            self._active.pop(ticket.ticket, None)

    def begin_removal(self, agent_id: str) -> bool:
        with self._lock:
            self._prune_expired_unlocked()
            if agent_id in self._removing or self._has_usage_unlocked(agent_id):
                return False
            self._removing.add(agent_id)
            return True

    def abort_removal(self, agent_id: str) -> None:
        with self._lock:
            self._removing.discard(agent_id)

    def finish_removal(self, agent_id: str) -> None:
        self.abort_removal(agent_id)

    def has_usage(self, agent_id: str) -> bool:
        with self._lock:
            self._prune_expired_unlocked()
            return self._has_usage_unlocked(agent_id)

    def _has_usage_unlocked(self, agent_id: str) -> bool:
        return any(
            record.agent_id == agent_id
            for records in (
                self._reservations.values(),
                self._tickets.values(),
                self._active.values(),
            )
            for record in records
        )

    def _durably_blocked(self, agent_id: str) -> bool:
        if not agent_id or self._durable_removal_blocker is None:
            return False
        try:
            return self._durable_removal_blocker(agent_id)
        except Exception:
            return True

    def _prune_expired_unlocked(self) -> None:
        now = self._clock()
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
