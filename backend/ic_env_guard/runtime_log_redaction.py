import logging
import re
from collections.abc import Mapping

_TICKET_QUERY_PARAMETER = re.compile(
    r"([?&]ticket=)[^&#\s\"']*",
    flags=re.IGNORECASE,
)
_UVICORN_LOGGERS = ("uvicorn.error", "uvicorn.access")


def _redact_ticket(value: str) -> str:
    return _TICKET_QUERY_PARAMETER.sub(r"\1<redacted>", value)


class TerminalTicketRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_ticket(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _redact_ticket(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, Mapping):
            record.args = {
                key: _redact_ticket(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def install_uvicorn_ticket_redaction() -> None:
    for logger_name in _UVICORN_LOGGERS:
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(item, TerminalTicketRedactionFilter) for item in logger.filters
        ):
            logger.addFilter(TerminalTicketRedactionFilter())
