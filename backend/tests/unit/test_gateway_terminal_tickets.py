from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.agents.terminal_proxy import GatewayProxyLimiter, GatewayTicketStore


@pytest.mark.unit
def test_gateway_ticket_store_reserves_capacity_before_upstream_ticket():
    store = GatewayTicketStore(max_outstanding=1)

    reservation = store.reserve()

    assert reservation is not None
    assert store.reserve() is None


@pytest.mark.unit
def test_gateway_ticket_store_releases_reservation_on_failure():
    store = GatewayTicketStore(max_outstanding=1)
    reservation = store.reserve()

    store.release_reservation(reservation)

    assert store.reserve() is not None


@pytest.mark.unit
def test_gateway_ticket_store_expires_uncommitted_reservation_and_releases_usage():
    now = [datetime.now(UTC)]
    limiter = GatewayProxyLimiter(1)
    store = GatewayTicketStore(
        max_outstanding=1,
        clock=lambda: now[0],
        reservation_ttl_seconds=5,
    )
    reservation = store.reserve("lab-01", slot=limiter.reserve())

    assert reservation is not None
    assert reservation.expires_at == now[0] + timedelta(seconds=5)
    assert store.has_usage("lab-01")
    assert limiter.reserve() is None

    now[0] += timedelta(seconds=6)

    assert not store.has_usage("lab-01")
    replacement_reservation = store.reserve("lab-02")
    assert replacement_reservation is not None
    store.release_reservation(replacement_reservation)
    assert store.begin_removal("lab-01")
    released = limiter.reserve()
    assert released is not None
    released.release()


@pytest.mark.unit
def test_gateway_ticket_is_bound_and_one_use():
    store = GatewayTicketStore(max_outstanding=1)
    reservation = store.reserve()
    ticket = store.commit(
        reservation,
        actor_id="local-admin",
        agent_id="lab-01",
        terminal_id="term-1",
        intended_ws_path="/ws/agents/lab-01/terminals/term-1",
        upstream_ticket="upstream-ticket",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )

    consumed = store.consume(
        ticket.ticket,
        actor_id="local-admin",
        agent_id="lab-01",
        terminal_id="term-1",
        intended_ws_path="/ws/agents/lab-01/terminals/term-1",
    )

    assert consumed is not None
    assert consumed.upstream_ticket == "upstream-ticket"
    assert store.consume(
        ticket.ticket,
        actor_id="local-admin",
        agent_id="lab-01",
        terminal_id="term-1",
        intended_ws_path="/ws/agents/lab-01/terminals/term-1",
    ) is None


@pytest.mark.unit
def test_gateway_ticket_rejects_mismatched_actor():
    store = GatewayTicketStore(max_outstanding=1)
    reservation = store.reserve()
    ticket = store.commit(
        reservation,
        actor_id="local-admin",
        agent_id="lab-01",
        terminal_id="term-1",
        intended_ws_path="/ws/agents/lab-01/terminals/term-1",
        upstream_ticket="upstream-ticket",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )

    assert store.consume(
        ticket.ticket,
        actor_id="other",
        agent_id="lab-01",
        terminal_id="term-1",
        intended_ws_path="/ws/agents/lab-01/terminals/term-1",
    ) is None


@pytest.mark.unit
def test_gateway_ticket_ttl_and_reservation_failure_release_proxy_slot():
    now = [datetime.now(UTC)]
    limiter = GatewayProxyLimiter(1)
    store = GatewayTicketStore(clock=lambda: now[0])
    reservation_slot = limiter.reserve()
    reservation = store.reserve("lab-01", slot=reservation_slot)
    store.release_reservation(reservation)
    replacement = limiter.reserve()
    assert replacement is not None
    replacement.release()

    ticket_slot = limiter.reserve()
    reservation = store.reserve("lab-01", slot=ticket_slot)
    store.commit(
        reservation,
        actor_id="local-admin",
        agent_id="lab-01",
        terminal_id="term-1",
        intended_ws_path="/ws/agents/lab-01/terminals/term-1",
        upstream_ticket="upstream-ticket",
        expires_at=now[0] + timedelta(seconds=1),
    )
    assert limiter.reserve() is None
    now[0] += timedelta(seconds=2)
    assert not store.has_usage("lab-01")
    released = limiter.reserve()
    assert released is not None
    released.release()


@pytest.mark.unit
def test_gateway_ticket_commit_rechecks_removal_and_releases_reserved_slot():
    blocked = [False]
    limiter = GatewayProxyLimiter(1)
    store = GatewayTicketStore(durable_removal_blocker=lambda _agent: blocked[0])
    slot = limiter.reserve()
    reservation = store.reserve("lab-01", slot=slot)
    blocked[0] = True

    with pytest.raises(ValueError, match="removal"):
        store.commit(
            reservation,
            actor_id="local-admin",
            agent_id="lab-01",
            terminal_id="term-1",
            intended_ws_path="/ws/agents/lab-01/terminals/term-1",
            upstream_ticket="upstream-ticket",
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
    store.release_reservation(reservation)
    replacement = limiter.reserve()
    assert replacement is not None
    replacement.release()
