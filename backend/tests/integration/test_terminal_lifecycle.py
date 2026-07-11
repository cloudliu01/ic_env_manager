import os
import time

import pytest

from ic_env_guard.terminal.manager import TerminalManager


@pytest.mark.integration
def test_terminal_pty_create_output_resize_close_and_reap():
    manager = TerminalManager(shell="/bin/sh")
    session = manager.create_terminal(title="lifecycle", rows=24, cols=80)

    assert session.pid is not None
    assert session.status == "running"

    manager.write(session.id, "printf lifecycle-ok\\n\n")
    output = manager.read_until(session.id, "lifecycle-ok", timeout=5)
    assert "lifecycle-ok" in output

    manager.resize(session.id, rows=40, cols=120)
    assert manager.get(session.id).rows == 40
    assert manager.get(session.id).cols == 120

    pid = manager.get(session.id).pid
    closed = manager.close(session.id)
    assert closed.status in {"closed", "exited"}

    time.sleep(0.1)
    if pid:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.mark.integration
def test_terminal_idle_cleanup_times_out_session():
    manager = TerminalManager(shell="/bin/sh", idle_timeout_minutes=30)
    session = manager.create_terminal(title="idle")
    manager.sessions[session.id].last_active_at -= 31 * 60

    manager.cleanup_idle_sessions()

    assert manager.get(session.id).status == "timed_out"


@pytest.mark.integration
def test_terminal_pty_inherits_runtime_user_permissions():
    manager = TerminalManager(shell="/bin/sh")
    session = manager.create_terminal(
        owner="local-admin", title="identity", rows=24, cols=80
    )

    try:
        manager.write(session.id, "id -u\n")
        output = manager.read_until(session.id, str(os.getuid()), timeout=5)
        manager.write(session.id, "printf '__PTY_''OK__\\n'\n")
        manager.read_until(session.id, "__PTY_OK__", timeout=5)
        history = manager.history(session.id, cursor=0)

        assert session.owner == "local-admin"
        assert str(os.getuid()) in {line.strip() for line in output.splitlines()}
        assert "__PTY_OK__" in history.output
    finally:
        manager.close(session.id)
