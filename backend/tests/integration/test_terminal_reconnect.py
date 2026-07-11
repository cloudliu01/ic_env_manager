import pytest

from ic_env_guard.terminal.manager import TerminalManager


@pytest.mark.integration
def test_terminal_reconnect_replays_retained_output():
    manager = TerminalManager(shell="/bin/sh")
    session = manager.create_terminal(title="reconnect")
    manager.write(session.id, "printf replay-ok\\n\n")
    manager.read_until(session.id, "replay-ok", timeout=5)

    history = manager.history(session.id, cursor=0)

    assert "replay-ok" in history.output
    assert history.truncated is False
    assert history.to_cursor >= history.from_cursor


@pytest.mark.integration
def test_terminal_reconnect_marks_truncated_for_old_cursor():
    manager = TerminalManager(shell="/bin/sh", replay_buffer_bytes=12)
    session = manager.create_terminal(title="truncate")
    manager.write(session.id, "printf abcdefghijklmnopqrstuvwxyz\\n\n")
    manager.read_until(session.id, "abcdefghijklmnopqrstuvwxyz", timeout=5)

    history = manager.history(session.id, cursor=0)

    assert history.truncated is True
    assert history.buffer_start_cursor > 0
    assert history.output


@pytest.mark.integration
def test_terminal_reconnect_future_cursor_streams_only_new_output():
    manager = TerminalManager(shell="/bin/sh")
    session = manager.create_terminal(title="future")
    future_cursor = manager.get(session.id).output_cursor + 100

    history = manager.history(session.id, cursor=future_cursor)

    assert history.output == ""
    assert history.from_cursor == manager.get(session.id).output_cursor
    assert history.truncated is False


@pytest.mark.integration
def test_terminal_reconnect_from_retained_cursor_returns_only_new_output():
    manager = TerminalManager(shell="/bin/sh")
    session = manager.create_terminal(title="cursor")

    try:
        manager.write(session.id, "printf '__BEFORE_''CURSOR__\\n'\n")
        manager.read_until(session.id, "__BEFORE_CURSOR__", timeout=5)
        cursor = manager.history(session.id, cursor=0).to_cursor

        manager.write(session.id, "printf '__AFTER_''CURSOR__\\n'\n")
        manager.read_until(session.id, "__AFTER_CURSOR__", timeout=5)
        history = manager.history(session.id, cursor=cursor)

        assert "__BEFORE_CURSOR__" not in history.output
        assert "__AFTER_CURSOR__" in history.output
        assert history.from_cursor == cursor
        assert history.truncated is False
    finally:
        manager.close(session.id)
