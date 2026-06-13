# Terminal Safety and Privacy

The browser terminal is a privileged local-host control feature. Treat terminal access as equivalent to shell access for the configured runtime user.

## Authentication

Terminal HTTP routes and WebSocket ticket creation require the generated local bearer token. WebSocket connections use short-lived one-use tickets created by authenticated API calls.

## Lifecycle controls

Each terminal session has server-side metadata:

- owner
- creation time
- last activity time
- process ID where available
- current row/column size
- output cursor
- replay buffer cursor range
- idle timeout
- close reason

Supported lifecycle operations:

- create
- attach via WebSocket ticket
- resize
- reconnect with cursor replay
- close
- idle timeout cleanup
- process exit reaping

## Replay behavior

Terminal output is retained only in a bounded in-memory replay buffer for reconnect. If the requested cursor is older than the retained range, replay is marked truncated and only the retained tail is returned. If a future cursor is supplied, the session streams only new output.

## Privacy guarantees

By default, terminal content is not persisted to SQLite, audit records, metrics, or logs. Durable records contain lifecycle metadata only.

Do not add terminal transcript persistence unless a future spec explicitly changes the privacy model and updates security review requirements.

## Operational guidance

- Keep the default local-only bind unless remote browser access is intentionally configured.
- Use network-level protection such as SSH tunnels, VPNs, or reverse proxies for remote access.
- Keep idle timeouts within the supported 30-120 minute range.
- Close sessions when finished.
- Review audit lifecycle events for unexpected terminal creation or attach attempts.
