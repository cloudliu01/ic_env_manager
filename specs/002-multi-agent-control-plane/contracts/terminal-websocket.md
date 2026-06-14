# Control-Plane Terminal WebSocket Contract

## Endpoint

```text
WS /ws/agents/{agent_id}/terminals/{terminal_id}?ticket={gateway_ticket}&cursor={output_cursor}
```

## Ticket Flow

1. The authenticated browser calls the agent-scoped HTTP `connect-token`
   endpoint.
2. The control plane reserves a slot in the bounded ticket store; if full it
   returns `429 gateway_capacity_exceeded` without contacting the agent.
3. The control plane requests a one-use ticket from the target agent; on any
   failure it releases the reservation and returns the appropriate error.
4. The control plane stores a bounded, expiring mapping from a new gateway
   ticket to the upstream ticket.
5. The browser receives only the gateway ticket.
6. On WebSocket attach the control plane atomically acquires a proxy slot and
   then consumes the gateway ticket; if the proxy cap is reached the attach is
   rejected with `4429` before the ticket is consumed, so a valid ticket is
   not wasted. The slot is released on every failure path.

The gateway ticket is bound to actor, agent ID, terminal ID, and expiry.
Missing, expired, reused, or mismatched tickets are rejected before either
WebSocket is attached.

## Connection Establishment

The control plane:

1. checks the global active-proxy cap; if reached, rejects with `4429` before
   touching the ticket store;
2. atomically acquires a proxy slot and consumes the gateway ticket; rejects
   with `4401` if the ticket is missing, expired, reused, or mismatched — the
   slot is released immediately on rejection;
3. resolves the same configured agent;
4. opens the upstream WebSocket using the `websockets` client with an
   `ssl.SSLContext` built from the target's TLS settings (the HTTP client cannot
   open WebSockets);
5. passes the upstream ticket and cursor;
6. accepts the browser WebSocket only after the upstream handshake succeeds;
   releases the slot on any upstream establishment failure.

If upstream establishment fails the browser connection closes with the mapped
gateway close code; no consumed ticket is reusable and the proxy slot is freed.

## Data Frames

The first release preserves the `001` text-frame terminal protocol.

- Browser text frames are forwarded only to the selected upstream terminal.
- Agent text frames are forwarded only to the browser connection bound to the
  same `(agent_id, terminal_id)`.
- Binary frames are rejected with a protocol error.
- Each frame is limited to 64 KiB.
- Terminal content is never logged, audited, persisted by the control plane, or
  used as a metric label.

## Backpressure

Each direction uses a bounded queue of at most 1 MiB. When a peer cannot drain
the queue within 10 seconds, the proxy closes both WebSockets with `4413`. It
does not buffer unbounded terminal content. Independently, the gateway bounds the
total number of concurrent proxied sockets and outstanding tickets; attaches
beyond that global cap are rejected with `4429`.

## Reconnect and Lifecycle

- The upstream agent remains the PTY lifecycle owner.
- Browser or gateway WebSocket disconnect does not immediately terminate the
  upstream PTY.
- Reconnect obtains a new gateway ticket and supplies the browser's last output
  cursor.
- Replay, truncation, shell exit, explicit close, and idle timeout follow the
  host-agent contract from feature `001`.
- Gateway restart invalidates outstanding gateway tickets and active proxy
  sockets, but it does not issue terminal close requests.

## Cancellation

The proxy runs one task per direction. Completion, cancellation, or failure in
either task cancels the other task and closes both sockets. Shutdown waits for
the paired tasks to finish within a bounded grace period.

## Close Codes

| Code | Meaning |
|---|---|
| `4400` | Invalid cursor or request format |
| `4401` | Missing, invalid, expired, reused, or mismatched ticket |
| `4403` | Actor is not authorized for the agent or terminal |
| `4404` | Agent or terminal not found |
| `4409` | Agent disabled or terminal not attachable |
| `4413` | Frame or buffered data limit exceeded |
| `4429` | Gateway terminal-proxy or ticket capacity exceeded |
| `4502` | Upstream protocol or TLS failure |
| `4503` | Agent unavailable |
| `4504` | Upstream connection timeout |
| `1011` | Unexpected proxy failure |
| `1012` | Control-plane shutdown or restart |

Close reasons are sanitized and never contain upstream credentials, private
paths, or raw exceptions.

## Audit

The gateway records attach intent and final outcome with actor, source address,
agent ID, terminal ID, correlation ID, and close category. It does not record
terminal input, output, titles containing secrets, or upstream tickets.

