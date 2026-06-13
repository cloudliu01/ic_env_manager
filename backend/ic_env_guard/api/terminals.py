from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.terminal.manager import TerminalManager
from ic_env_guard.terminal.tickets import TerminalTicketManager

router = APIRouter(prefix="/api/terminals", tags=["terminals"])


class CreateTerminalRequest(BaseModel):
    title: str = Field(default="Terminal", max_length=80)
    rows: int = Field(default=24, ge=1)
    cols: int = Field(default=80, ge=1)
    cwd: str | None = None


class ResizeTerminalRequest(BaseModel):
    rows: int = Field(ge=1)
    cols: int = Field(ge=1)


class ConnectTokenResponse(BaseModel):
    ticket: str
    expires_in_seconds: int


def get_terminal_manager() -> TerminalManager:
    raise RuntimeError("TerminalManager dependency was not configured")


def get_ticket_manager() -> TerminalTicketManager:
    raise RuntimeError("TerminalTicketManager dependency was not configured")


def _terminal_or_404(manager: TerminalManager, terminal_id: str):
    try:
        return manager.get(terminal_id)
    except KeyError:
        raise ApiError(404, "not_found", "terminal session not found") from None


@router.get("")
def list_terminals(
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
) -> dict[str, list[dict[str, object]]]:
    return {"terminals": [session.to_dict() for session in manager.list()]}


@router.post("", status_code=201)
def create_terminal(
    payload: CreateTerminalRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
) -> dict[str, object]:
    session = manager.create_terminal(
        title=payload.title,
        rows=payload.rows,
        cols=payload.cols,
        owner=auth.actor_id,
        cwd=payload.cwd,
    )
    return session.to_dict()


@router.get("/{terminal_id}")
def get_terminal(
    terminal_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
) -> dict[str, object]:
    return _terminal_or_404(manager, terminal_id).to_dict()


@router.get("/{terminal_id}/history")
def get_terminal_history(
    terminal_id: str,
    cursor: int,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
) -> dict[str, object]:
    _terminal_or_404(manager, terminal_id)
    history = manager.history(terminal_id, cursor)
    return {
        "terminal_id": history.terminal_id,
        "from_cursor": history.from_cursor,
        "to_cursor": history.to_cursor,
        "buffer_start_cursor": history.buffer_start_cursor,
        "truncated": history.truncated,
        "status": history.status,
        "output": history.output,
    }


@router.post("/{terminal_id}/connect-token", status_code=201)
def create_connect_token(
    terminal_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
    tickets: Annotated[TerminalTicketManager, Depends(get_ticket_manager)],
) -> ConnectTokenResponse:
    _terminal_or_404(manager, terminal_id)
    ticket = tickets.issue(terminal_id)
    return ConnectTokenResponse(ticket=ticket.ticket, expires_in_seconds=tickets.ttl_seconds)


@router.post("/{terminal_id}/resize", status_code=204)
def resize_terminal(
    terminal_id: str,
    payload: ResizeTerminalRequest,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
) -> None:
    _terminal_or_404(manager, terminal_id)
    manager.resize(terminal_id, payload.rows, payload.cols)


@router.delete("/{terminal_id}", status_code=202)
def close_terminal(
    terminal_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
) -> dict[str, object]:
    _terminal_or_404(manager, terminal_id)
    return manager.close(terminal_id).to_dict()
