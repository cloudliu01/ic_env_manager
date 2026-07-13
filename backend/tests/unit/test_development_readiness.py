import asyncio
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ic_env_guard.development import readiness


def _token_file(tmp_path: Path, content: bytes = b"manager-token\n") -> Path:
    path = tmp_path / "token"
    path.write_bytes(content)
    path.chmod(0o600)
    return path


@pytest.mark.unit
def test_readiness_token_loader_reads_owner_only_regular_file(tmp_path):
    assert readiness._load_readiness_token(_token_file(tmp_path)) == "manager-token"


@pytest.mark.unit
def test_readiness_token_loader_rejects_symlink(tmp_path):
    target = _token_file(tmp_path)
    link = tmp_path / "token-link"
    link.symlink_to(target)

    with pytest.raises(readiness.ReadinessError):
        readiness._load_readiness_token(link)


@pytest.mark.unit
def test_readiness_token_loader_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "token-fifo"
    os.mkfifo(fifo, 0o600)

    with pytest.raises(readiness.ReadinessError):
        readiness._load_readiness_token(fifo)


@pytest.mark.unit
def test_readiness_token_loader_rejects_group_or_other_permissions(tmp_path):
    path = _token_file(tmp_path)
    path.chmod(0o640)

    with pytest.raises(readiness.ReadinessError):
        readiness._load_readiness_token(path)


@pytest.mark.unit
def test_readiness_token_loader_rejects_oversize_file(tmp_path):
    path = _token_file(tmp_path, b"x" * (readiness.TOKEN_FILE_MAX_BYTES + 1))

    with pytest.raises(readiness.ReadinessError):
        readiness._load_readiness_token(path)


@pytest.mark.unit
@pytest.mark.parametrize("content", [b" \n", b"\xff"])
def test_readiness_token_loader_rejects_empty_or_invalid_utf8(tmp_path, content):
    path = _token_file(tmp_path, content)

    with pytest.raises(readiness.ReadinessError):
        readiness._load_readiness_token(path)


@pytest.mark.unit
def test_readiness_token_metadata_rejects_wrong_owner():
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_uid=os.getuid() + 1,
        st_size=10,
    )

    with pytest.raises(readiness.ReadinessError):
        readiness._validate_token_metadata(metadata)


@pytest.mark.unit
def test_readiness_token_loader_reads_opened_inode_when_path_is_replaced(
    tmp_path, monkeypatch
):
    path = _token_file(tmp_path, b"opened-token\n")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"replacement-token\n")
    replacement.chmod(0o600)
    real_open = os.open

    def open_then_replace(open_path, flags):
        descriptor = real_open(open_path, flags)
        os.replace(replacement, path)
        return descriptor

    monkeypatch.setattr(readiness.os, "open", open_then_replace)

    assert readiness._load_readiness_token(path) == "opened-token"
    assert path.read_text(encoding="utf-8").strip() == "replacement-token"


@pytest.mark.unit
def test_readiness_malformed_cli_uses_only_stable_error(capsys):
    assert readiness.main(["--token-file", "sensitive-path"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Local Terminal proxy readiness failed.\n"
    assert "sensitive-path" not in captured.err


@pytest.mark.unit
def test_readiness_help_remains_available(capsys):
    with pytest.raises(SystemExit) as exc:
        readiness.main(["--help"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "usage:" in captured.out
    assert captured.err == ""


@pytest.mark.unit
def test_readiness_runtime_failure_uses_only_stable_error(tmp_path, monkeypatch, capsys):
    token_file = _token_file(tmp_path, b"never-print-this-token\n")

    async def fail(*_args):
        raise RuntimeError("never-print-this-token")

    monkeypatch.setattr(readiness, "_verify", fail)

    assert (
        readiness.main(
            [
                "--manager-url",
                "http://127.0.0.1:8765",
                "--token-file",
                str(token_file),
                "--agent-id",
                "local-agent",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Local Terminal proxy readiness failed.\n"


@pytest.mark.unit
def test_readiness_rejects_terminal_output_over_limit(
    tmp_path, monkeypatch, capsys
):
    class FloodingWebSocket:
        def __init__(self):
            self.messages = iter(
                [
                    "x" * (readiness.MAX_SENTINEL_OUTPUT_BYTES // 2 + 1),
                    "y" * (readiness.MAX_SENTINEL_OUTPUT_BYTES // 2 + 1),
                    readiness.SENTINEL,
                ]
            )
            self.closed = False

        async def send(self, _message):
            return None

        async def recv(self):
            return next(self.messages)

        async def close(self):
            self.closed = True

    class Connection:
        process_redirect = None

        def __init__(self, websocket):
            self.websocket = websocket

        def __await__(self):
            async def connect():
                return self.websocket

            return connect().__await__()

    websocket = FloodingWebSocket()
    monkeypatch.setattr(
        readiness.websockets,
        "connect",
        lambda *_args, **_kwargs: Connection(websocket),
    )

    with pytest.raises(readiness.ReadinessError) as overflow:
        asyncio.run(
            readiness._verify_websocket(
                "ws://127.0.0.1:8765",
                "manager-token",
                "local-agent",
                "terminal-1",
                "gateway-ticket",
                time.monotonic() + 2,
            )
        )
    assert websocket.closed is True

    token_file = _token_file(tmp_path)

    async def fail_with_overflow(*_args):
        raise overflow.value

    monkeypatch.setattr(readiness, "_verify", fail_with_overflow)
    assert (
        readiness.main(
            [
                "--manager-url",
                "http://127.0.0.1:8765",
                "--token-file",
                str(token_file),
                "--agent-id",
                "local-agent",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Local Terminal proxy readiness failed.\n"
