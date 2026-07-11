import json
import os

import pytest

from ic_env_guard.logs.models import LogFileUnavailable, LogPathForbidden
from ic_env_guard.logs.policy import LogPathPolicy, LogTailReader


def _reader(root, *, max_bytes=983040):
    return LogTailReader(LogPathPolicy([root]), max_bytes=max_bytes)


def test_tail_returns_only_requested_last_lines(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = _reader(tmp_path).read(path, lines=2)

    assert result.lines == ("three", "four")
    assert result.truncated is True


def test_tail_replaces_invalid_utf8_and_respects_byte_limit(tmp_path):
    path = tmp_path / "run.log"
    path.write_bytes(b"first\ninvalid-\xff\nlast\n")

    result = _reader(tmp_path, max_bytes=16).read(path, lines=100)

    assert "\ufffd" in "".join(result.lines)
    assert len("\n".join(result.lines).encode("utf-8")) <= 16
    assert result.truncated is True


def test_tail_preserves_replacements_when_decoding_expands_past_budget(tmp_path):
    path = tmp_path / "run.log"
    path.write_bytes(6 * b"\xff")

    result = _reader(tmp_path, max_bytes=16).read(path, lines=1)

    assert result.lines == (5 * "\ufffd",)
    assert len(result.lines[0].encode("utf-8")) == 15
    assert result.truncated is True


@pytest.mark.parametrize("lines", [0, 1001])
def test_tail_rejects_line_count_outside_contract(tmp_path, lines):
    path = tmp_path / "run.log"
    path.write_text("ok\n", encoding="utf-8")

    with pytest.raises(ValueError, match="between 1 and 1000"):
        _reader(tmp_path).read(path, lines=lines)


def test_tail_caps_content_at_960_kib(tmp_path):
    path = tmp_path / "run.log"
    path.write_bytes((b"x" * 2047 + b"\n") * 600)

    result = _reader(tmp_path).read(path, lines=1000)

    assert len("\n".join(result.lines).encode("utf-8")) <= 983040
    assert result.truncated is True


def test_tail_reserves_headroom_for_json_escaping(tmp_path):
    path = tmp_path / "run.log"
    path.write_bytes((b'"\\' * 500_000) + b"\n")

    result = _reader(tmp_path).read(path, lines=1)
    wire = json.dumps(
        {
            "id": "run",
            "path": str(path),
            "lines": list(result.lines),
            "line_count": len(result.lines),
            "truncated": result.truncated,
            "last_updated": "2026-07-11T09:59:58Z",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(wire) < 1024 * 1024
    assert result.truncated is True


def test_tail_revalidates_registered_path_after_symlink_replacement(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    path = allowed / "run.log"
    path.write_text("safe", encoding="utf-8")
    outside = tmp_path / "secret.log"
    outside.write_text("secret", encoding="utf-8")
    registered = LogPathPolicy([allowed]).resolve_regular_file(path)
    path.unlink()
    path.symlink_to(outside)

    with pytest.raises(LogPathForbidden):
        _reader(allowed).read(registered, lines=10)


def test_tail_detects_check_open_symlink_race(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    path = allowed / "run.log"
    path.write_text("safe", encoding="utf-8")
    outside = tmp_path / "secret.log"
    outside.write_text("secret", encoding="utf-8")
    real_open = os.open
    swapped = False

    def racing_open(candidate, flags):
        nonlocal swapped
        if not swapped:
            swapped = True
            path.unlink()
            path.symlink_to(outside)
        return real_open(candidate, flags)

    monkeypatch.setattr(os, "open", racing_open)

    with pytest.raises((LogPathForbidden, LogFileUnavailable)):
        _reader(allowed).read(path, lines=10)


def test_tail_detects_open_path_inode_mismatch(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    path = allowed / "run.log"
    path.write_text("safe", encoding="utf-8")
    other = allowed / "other.log"
    other.write_text("other", encoding="utf-8")
    real_open = os.open

    def wrong_open(candidate, flags):
        return real_open(other, flags)

    monkeypatch.setattr(os, "open", wrong_open)

    with pytest.raises(LogFileUnavailable, match="changed while opening"):
        _reader(allowed).read(path, lines=10)
