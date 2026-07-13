import argparse
import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
import websockets

from ic_env_guard.auth.token import load_bearer_token, validate_token_file_permissions

READINESS_TIMEOUT_SECONDS = 10.0
CLEANUP_TIMEOUT_SECONDS = 2.0
SENTINEL = "__LOCAL_V2_TERMINAL_OK__"


class ReadinessError(Exception):
    pass


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReadinessError("readiness deadline exceeded")
    return remaining


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    deadline: float,
    expected_status: int,
    json: dict[str, object] | None = None,
) -> httpx.Response:
    timeout = _remaining(deadline)
    response = await asyncio.wait_for(
        client.request(method, path, json=json, timeout=timeout), timeout=timeout
    )
    if response.status_code != expected_status:
        raise ReadinessError("unexpected Manager response")
    return response


def _json(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ReadinessError("invalid Manager response") from exc
    if not isinstance(payload, dict):
        raise ReadinessError("invalid Manager response")
    return payload


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ReadinessError("invalid Manager response")
    return value


def _manager_urls(manager_url: str) -> tuple[str, str]:
    parsed = urlsplit(manager_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ReadinessError("invalid Manager URL")
    http_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    websocket_scheme = "wss" if parsed.scheme == "https" else "ws"
    websocket_url = urlunsplit((websocket_scheme, parsed.netloc, "", "", ""))
    return http_url, websocket_url


def _manager_client(http_url: str, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=http_url,
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,
    )


async def _verify_websocket(
    websocket_base_url: str,
    token: str,
    agent_id: str,
    terminal_id: str,
    ticket: str,
    deadline: float,
) -> None:
    agent = quote(agent_id, safe="")
    terminal = quote(terminal_id, safe="")
    query = urlencode({"ticket": ticket, "cursor": "0"})
    url = f"{websocket_base_url}/ws/agents/{agent}/terminals/{terminal}?{query}"
    websocket = None
    try:
        timeout = _remaining(deadline)
        connection = websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=timeout,
            close_timeout=CLEANUP_TIMEOUT_SECONDS,
            proxy=None,
        )
        connection.process_redirect = lambda exc: exc
        websocket = await asyncio.wait_for(
            connection,
            timeout=timeout,
        )
        await asyncio.wait_for(
            websocket.send(f"printf '{SENTINEL}\\n'\r"), timeout=_remaining(deadline)
        )
        received = ""
        while SENTINEL not in received:
            message = await asyncio.wait_for(websocket.recv(), timeout=_remaining(deadline))
            if not isinstance(message, str):
                raise ReadinessError("unexpected Terminal frame")
            received += message
    finally:
        if websocket is not None:
            await asyncio.wait_for(websocket.close(), timeout=CLEANUP_TIMEOUT_SECONDS)


async def _verify(manager_url: str, token: str, agent_id: str) -> None:
    http_url, websocket_url = _manager_urls(manager_url)
    agent = quote(agent_id, safe="")
    collection_path = f"/api/agents/{agent}/terminals"
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    terminal_id: str | None = None
    client = _manager_client(http_url, token)
    try:
        try:
            await _request(
                client,
                "GET",
                collection_path,
                deadline=deadline,
                expected_status=200,
            )
            created = _json(
                await _request(
                    client,
                    "POST",
                    collection_path,
                    deadline=deadline,
                    expected_status=201,
                    json={"title": "Startup verification", "rows": 24, "cols": 80},
                )
            )
            terminal_id = _required_text(created, "id")
            terminal_path = f"{collection_path}/{quote(terminal_id, safe='')}"
            await _request(
                client,
                "POST",
                f"{terminal_path}/resize",
                deadline=deadline,
                expected_status=204,
                json={"rows": 30, "cols": 100},
            )
            connect_token = _json(
                await _request(
                    client,
                    "POST",
                    f"{terminal_path}/connect-token",
                    deadline=deadline,
                    expected_status=201,
                )
            )
            ticket = _required_text(connect_token, "ticket")
            await _verify_websocket(
                websocket_url, token, agent_id, terminal_id, ticket, deadline
            )
        finally:
            if terminal_id is not None:
                terminal_path = f"{collection_path}/{quote(terminal_id, safe='')}"
                response = await asyncio.wait_for(
                    client.request(
                        "DELETE", terminal_path, timeout=CLEANUP_TIMEOUT_SECONDS
                    ),
                    timeout=CLEANUP_TIMEOUT_SECONDS,
                )
                if response.status_code != 202:
                    raise ReadinessError("Terminal cleanup failed")
    finally:
        await asyncio.wait_for(client.aclose(), timeout=CLEANUP_TIMEOUT_SECONDS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manager-url", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--agent-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        validate_token_file_permissions(args.token_file)
        token = load_bearer_token(args.token_file)
        asyncio.run(_verify(args.manager_url, token, args.agent_id))
    except Exception:
        print("Local Terminal proxy readiness failed.", file=sys.stderr)
        return 1
    print("Local Terminal proxy ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
