import logging
from urllib.parse import parse_qsl

import pytest

from ic_env_guard.runtime_log_redaction import (
    TerminalTicketRedactionFilter,
    install_uvicorn_ticket_redaction,
)


def _record(name: str, message: str, args: tuple[object, ...]) -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 1, message, args, None)


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    ("name", "message", "args"),
    [
        (
            "uvicorn.error",
            '%s - "WebSocket %s" [accepted]',
            ("127.0.0.1:1234", "/ws/terminal?ticket=ws-synthetic&cursor=0"),
        ),
        (
            "uvicorn.access",
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1:1234",
                "GET",
                "/terminal?cursor=0&TICKET=http-synthetic&safe=yes",
                "1.1",
                200,
            ),
        ),
    ],
)
def test_uvicorn_record_arguments_redact_terminal_ticket(name, message, args):
    record = _record(name, message, args)

    assert TerminalTicketRedactionFilter().filter(record)

    rendered = record.getMessage()
    assert "synthetic" not in rendered
    assert "ticket=<redacted>" in rendered.lower()
    assert "cursor=0" in rendered
    if name == "uvicorn.access":
        assert isinstance(record.args[-1], int)


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    "path",
    [
        "/ws?ticket=percent%2Fvalue%3D",
        "/ws?safe=1&ticket=percent%252Fvalue&cursor=0",
    ],
)
def test_percent_encoded_ticket_values_are_fully_redacted(path):
    record = _record("uvicorn.error", "%s", (path,))

    TerminalTicketRedactionFilter().filter(record)

    rendered = record.getMessage()
    assert "percent" not in rendered
    assert "ticket=<redacted>" in rendered


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    ("name", "message", "args", "raw_query", "expected_path"),
    [
        (
            "uvicorn.error",
            '%s - "WebSocket %s" [accepted]',
            ("127.0.0.1:1234", "/ws?%74icket=encoded-key-synthetic&safe=1"),
            "%74icket=encoded-key-synthetic&safe=1",
            "/ws?%74icket=<redacted>&safe=1",
        ),
        (
            "uvicorn.access",
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1:1234",
                "GET",
                "/terminal?safe=1&ti%63ket=encoded-key-synthetic#fragment",
                "1.1",
                200,
            ),
            "safe=1&ti%63ket=encoded-key-synthetic",
            "/terminal?safe=1&ti%63ket=<redacted>#fragment",
        ),
    ],
)
def test_percent_encoded_ticket_keys_are_redacted(
    name, message, args, raw_query, expected_path
):
    parsed = dict(parse_qsl(raw_query))
    assert parsed["ticket"] == "encoded-key-synthetic"
    record = _record(name, message, args)

    TerminalTicketRedactionFilter().filter(record)

    rendered = record.getMessage()
    assert "encoded-key-synthetic" not in rendered
    assert expected_path in rendered


@pytest.mark.unit
@pytest.mark.security
def test_preformatted_message_redacts_each_encoded_ticket_key():
    record = _record(
        "uvicorn.error",
        'WebSocket /ws?%74icket=first-synthetic&safe=%2F&TI%43KET=second-synthetic '
        "[accepted]",
        (),
    )

    TerminalTicketRedactionFilter().filter(record)

    assert record.getMessage() == (
        "WebSocket /ws?%74icket=<redacted>&safe=%2F&TI%43KET=<redacted> "
        "[accepted]"
    )


@pytest.mark.unit
@pytest.mark.security
def test_preformatted_message_is_redacted_without_changing_safe_fields():
    record = _record(
        "uvicorn.error",
        '127.0.0.1 - "WebSocket /ws?safe=1&ticket=message-synthetic&cursor=2" '
        "[accepted]",
        (),
    )

    TerminalTicketRedactionFilter().filter(record)

    assert record.getMessage() == (
        '127.0.0.1 - "WebSocket /ws?safe=1&ticket=<redacted>&cursor=2" [accepted]'
    )


@pytest.mark.unit
def test_non_ticket_log_and_non_string_arguments_are_unchanged():
    record = _record(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", "/healthz?check=ready", "1.1", 204),
    )
    original_message = record.msg
    original_args = record.args

    TerminalTicketRedactionFilter().filter(record)

    assert record.msg == original_message
    assert record.args == original_args
    assert record.args[-1] == 204


@pytest.mark.unit
def test_filter_installation_is_idempotent(monkeypatch):
    loggers = [logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access")]
    for logger in loggers:
        monkeypatch.setattr(logger, "filters", [])

    install_uvicorn_ticket_redaction()
    install_uvicorn_ticket_redaction()

    for logger in loggers:
        installed = [
            item for item in logger.filters if isinstance(item, TerminalTicketRedactionFilter)
        ]
        assert len(installed) == 1
