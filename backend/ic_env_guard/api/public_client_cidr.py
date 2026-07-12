from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from typing import Any

from ic_env_guard.api.v2_errors import resolve_v2_correlation_id, v2_error_response

PUBLIC_CLIENT_FORBIDDEN = "public_client_forbidden"
PUBLIC_CLIENT_FORBIDDEN_MESSAGE = "public access requires a trusted client network"
PUBLIC_CLIENT_FORBIDDEN_WS_CODE = 4403


class PublicClientCidrMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        networks: tuple[IPv4Network | IPv6Network, ...],
    ) -> None:
        self.app = app
        self.networks = networks

    @staticmethod
    def _header(scope: dict[str, Any], name: bytes) -> str | None:
        for key, value in scope.get("headers", ()):
            if key.lower() == name:
                return value.decode("latin-1")
        return None

    def _allows(self, scope: dict[str, Any]) -> bool:
        client = scope.get("client")
        if not isinstance(client, tuple) or len(client) != 2:
            return False
        host, port = client
        if not isinstance(host, str) or not host:
            return False
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            return False
        try:
            peer: IPv4Address | IPv6Address = ip_address(host)
        except ValueError:
            return False
        if isinstance(peer, IPv6Address) and peer.ipv4_mapped is not None:
            peer = peer.ipv4_mapped
        return any(peer.version == network.version and peer in network for network in self.networks)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"} or self._allows(scope):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send(
                {
                    "type": "websocket.close",
                    "code": PUBLIC_CLIENT_FORBIDDEN_WS_CODE,
                    "reason": PUBLIC_CLIENT_FORBIDDEN,
                }
            )
            return

        correlation_id = resolve_v2_correlation_id(self._header(scope, b"x-correlation-id"))
        response = v2_error_response(
            403,
            PUBLIC_CLIENT_FORBIDDEN,
            PUBLIC_CLIENT_FORBIDDEN_MESSAGE,
            correlation_id,
        )
        await response(scope, receive, send)
