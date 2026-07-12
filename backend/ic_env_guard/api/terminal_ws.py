import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ic_env_guard.api.terminals import get_terminal_manager, get_ticket_manager
from ic_env_guard.terminal.manager import TerminalManager
from ic_env_guard.terminal.tickets import TerminalTicketManager

router = APIRouter(tags=["terminal-websocket"])


POLL_INTERVAL_SECONDS = 0.05


async def _pump_terminal_output(
    websocket: WebSocket,
    manager: TerminalManager,
    terminal_id: str,
) -> None:
    while True:
        output = await asyncio.to_thread(
            manager.read_available,
            terminal_id,
            POLL_INTERVAL_SECONDS,
        )
        if output:
            await websocket.send_text(output)
        session = manager.get(terminal_id)
        if session.status != "running":
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@router.websocket("/ws/terminals/{terminal_id}")
async def terminal_websocket(
    websocket: WebSocket,
    terminal_id: str,
    manager: Annotated[TerminalManager, Depends(get_terminal_manager)],
    tickets: Annotated[TerminalTicketManager, Depends(get_ticket_manager)],
) -> None:
    ticket = websocket.query_params.get("ticket")
    try:
        cursor = int(websocket.query_params.get("cursor", "0"))
    except ValueError:
        await websocket.close(code=4400)
        return

    if not ticket or not tickets.consume(ticket, terminal_id):
        await websocket.close(code=4401)
        return
    try:
        session = manager.get(terminal_id)
    except KeyError:
        await websocket.close(code=4404)
        return
    if session.status != "running":
        await websocket.close(code=4409)
        return

    await websocket.accept()
    history = manager.history(terminal_id, cursor)
    if history.output:
        await websocket.send_text(history.output)

    output_task = asyncio.create_task(_pump_terminal_output(websocket, manager, terminal_id))
    try:
        while True:
            text = await websocket.receive_text()
            await asyncio.to_thread(manager.write, terminal_id, text)
    except WebSocketDisconnect:
        return
    except Exception:
        await websocket.close(code=1011)
    finally:
        output_task.cancel()
        try:
            await output_task
        except asyncio.CancelledError:
            pass
