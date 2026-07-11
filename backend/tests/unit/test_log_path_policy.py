import os
import shutil
import socket
import tempfile

import pytest

from ic_env_guard.logs.models import LogFileUnavailable, LogPathForbidden
from ic_env_guard.logs.policy import LogPathPolicy


def test_resolves_regular_file_beneath_allowed_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    path = allowed / "run.log"
    path.write_text("ok", encoding="utf-8")

    assert LogPathPolicy([allowed]).resolve_regular_file(path) == path.resolve()


def test_rejects_path_outside_allowed_root_without_prefix_confusion(tmp_path):
    allowed = tmp_path / "logs"
    lookalike = tmp_path / "logs-secret"
    allowed.mkdir()
    lookalike.mkdir()
    path = lookalike / "run.log"
    path.write_text("secret", encoding="utf-8")

    with pytest.raises(LogPathForbidden, match="outside allowed roots"):
        LogPathPolicy([allowed]).resolve_regular_file(path)


def test_symlink_escape_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside.log"
    allowed.mkdir()
    outside.write_text("secret", encoding="utf-8")
    (allowed / "run.log").symlink_to(outside)

    with pytest.raises(LogPathForbidden):
        LogPathPolicy([allowed]).resolve_regular_file(allowed / "run.log")


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "socket"])
def test_rejects_unavailable_or_non_regular_targets(tmp_path, kind):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    short_root = None
    if kind == "socket":
        short_root = tempfile.mkdtemp(prefix="ieg-log-", dir="/tmp")
        allowed = type(tmp_path)(short_root)
    path = allowed / "run.log"
    open_socket = None
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "socket":
        open_socket = socket.socket(socket.AF_UNIX)
        try:
            open_socket.bind(str(path))
        except PermissionError:
            open_socket.close()
            shutil.rmtree(short_root)
            pytest.skip("sandbox does not permit creating Unix sockets")

    try:
        with pytest.raises(LogFileUnavailable, match="regular file"):
            LogPathPolicy([allowed]).resolve_regular_file(path)
    finally:
        if open_socket is not None:
            open_socket.close()
        if short_root is not None:
            shutil.rmtree(short_root)


def test_allowed_root_must_be_an_existing_directory(tmp_path):
    with pytest.raises(ValueError, match="existing directory"):
        LogPathPolicy([tmp_path / "missing"])
