from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.agents.terminal_proxy import GatewayTicketStore


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
