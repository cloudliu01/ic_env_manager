# Terminal WebSocket Contract

## Endpoint

```text
WS /ws/terminals/{terminal_id}?ticket={one_use_ticket}&cursor={output_cursor}
```

## Authentication and Authorization

- Browser clients must first authenticate with the generated local bearer token.
- Clients must create or select a terminal session through the HTTP API.
- Clients must request a one-use WebSocket ticket with `POST /api/terminals/{terminal_id}/connect-token`.
- The ticket is valid for at most 60 seconds and is consumed on first successful WebSocket attach.
- The terminal session must be owned by the single authenticated local administrator role.
- Missing, expired, reused, or invalid tickets are rejected before the PTY stream is attached.

## Client-to-Server Messages

MVP terminal input messages are raw terminal input text frames.

```text
<terminal input bytes encoded as text>
```

Rules:

- Input is forwarded only to the PTY for the addressed terminal session.
- Input updates the terminal session `last_active_at` timestamp.
- Terminal input contents are not written to audit logs or SQLite by default.
- Resize is not sent through WebSocket in MVP; use `POST /api/terminals/{id}/resize`.

## Server-to-Client Messages

MVP terminal output messages are raw terminal output text frames.

```text
<terminal output bytes encoded as text>
```

Rules:

- Output is appended to the bounded in-memory replay buffer.
- Output increments the terminal session `output_cursor`.
- Output updates `last_active_at`.
- Terminal output contents are not written to audit logs or SQLite by default.

## Reconnect Semantics

On connect, the client provides its last observed `cursor`.

- If `cursor` is within retained replay range, the server sends retained output after that cursor, then live output.
- If `cursor` is older than `replay_buffer_start_cursor`, the server sends the newest retained tail and marks replay as truncated through terminal history metadata available from HTTP.
- If `cursor` is newer than current `output_cursor`, the server treats it as current and streams only new output.
- If the PTY has exited but is still within exited-session retention, the server may replay retained output and then close the stream.
- If the terminal is closed, timed out, or unknown, the server rejects attach or closes with a terminal-state error.

## Lifecycle Requirements

- Terminal sessions are server-side resources independent of browser WebSocket connections.
- Browser disconnect does not immediately kill a running PTY.
- Running terminals remain attachable until explicitly closed, shell exits, forced termination occurs, or idle timeout expires.
- Idle timeout is configurable from 30 to 120 minutes with a 60-minute default.
- Cleanup must terminate or reap shell processes for closed/timed-out sessions.
- Terminal lifecycle audit events record session creation, attach, detach, close, timeout, and failure metadata but not terminal content.

## Close Behavior

Server may close the WebSocket for these reasons:

- invalid or expired ticket
- terminal not found
- terminal not owned by authenticated administrator
- terminal already closed or timed out
- PTY process exited
- server shutdown
- protocol or transport error

The UI should respond by refreshing terminal metadata through the HTTP API and showing a non-blocking status message.
