"""Who may call the server: a bearer token, a list of trusted peer networks, or both.

Both checks are off until configured, so a server on a trusted LAN runs as before.
Behind a tunnel that terminates TLS and forwards to this process, the peer address
is the tunnel's own; set HERDR_MCP_PEER_HEADER to the header it puts the caller's
address in (X-Forwarded-For), and bind to 127.0.0.1 so nothing else can set it.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("herdr-mcp-server.auth")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def networks(spec: str) -> tuple[Network, ...]:
    """Parse a comma-separated list of addresses or CIDR networks."""
    return tuple(
        ipaddress.ip_network(part.strip(), strict=False)
        for part in spec.split(",")
        if part.strip()
    )


def peer_of(scope: Scope, header: str) -> str:
    """The caller's address: the first in ``header`` when set, else the socket peer."""
    if header:
        name = header.casefold().encode()
        for key, value in scope.get("headers") or []:
            if key == name:
                first = value.decode(errors="replace").split(",")[0].strip()
                if first:
                    return first
    client = scope.get("client")
    return client[0] if client else ""


def trusted(peer: str, allowed: tuple[Network, ...]) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in net for net in allowed)


def bearer(scope: Scope) -> str:
    for key, value in scope.get("headers") or []:
        if key == b"authorization":
            scheme, _, token = value.decode(errors="replace").partition(" ")
            return token.strip() if scheme.casefold() == "bearer" else ""
    return ""


class Guard:
    """ASGI middleware refusing requests that fail the configured checks."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str = "",
        peers: tuple[Network, ...] = (),
        peer_header: str = "",
    ) -> None:
        self.app = app
        self.token = token
        self.peers = peers
        self.peer_header = peer_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        peer = peer_of(scope, self.peer_header)
        if self.peers and not trusted(peer, self.peers):
            logger.warning("refused %s: not a trusted peer", peer or "?")
            await _refuse(scope, send, 403)
            return
        if self.token and not hmac.compare_digest(bearer(scope), self.token):
            logger.warning("refused %s: bad or missing bearer token", peer or "?")
            await _refuse(scope, send, 401, ((b"www-authenticate", b"Bearer"),))
            return
        await self.app(scope, receive, send)


async def _refuse(
    scope: Scope, send: Send, status: int, headers: tuple[tuple[bytes, bytes], ...] = ()
) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-length", b"0"), *headers],
        }
    )
    await send({"type": "http.response.body", "body": b""})
