import httpx
import pytest

import auth


async def _ok(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _client(guard: auth.Guard, peer: str = "127.0.0.1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=guard, client=(peer, 1234))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_nothing_configured_lets_everything_through() -> None:
    async with _client(auth.Guard(_ok)) as c:
        assert (await c.get("/mcp")).status_code == 200


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Authorization": "Basic c2VjcmV0"}, 401),
        ({"Authorization": "Bearer secret"}, 200),
        ({"Authorization": "bearer secret"}, 200),
    ],
)
async def test_a_token_is_required_once_set(headers, status) -> None:
    async with _client(auth.Guard(_ok, token="secret")) as c:
        resp = await c.get("/mcp", headers=headers)
    assert resp.status_code == status
    if status == 401:
        assert resp.headers["www-authenticate"] == "Bearer"


async def test_peers_are_checked_by_socket_address() -> None:
    guard = auth.Guard(_ok, peers=auth.networks("10.0.0.0/8, 127.0.0.1"))
    async with _client(guard, peer="10.1.2.3") as c:
        assert (await c.get("/mcp")).status_code == 200
    async with _client(guard, peer="192.168.1.9") as c:
        assert (await c.get("/mcp")).status_code == 403


async def test_a_forwarded_address_is_the_peer_behind_a_tunnel() -> None:
    guard = auth.Guard(
        _ok, peers=auth.networks("160.79.104.0/21"), peer_header="X-Forwarded-For"
    )
    async with _client(guard) as c:
        # the socket peer is the tunnel; the header names the caller
        assert (await c.get("/mcp")).status_code == 403
        far = {"X-Forwarded-For": "160.79.105.7, 100.64.0.1"}
        assert (await c.get("/mcp", headers=far)).status_code == 200
        near = {"X-Forwarded-For": "203.0.113.5"}
        assert (await c.get("/mcp", headers=near)).status_code == 403


async def test_peer_is_checked_before_the_token() -> None:
    guard = auth.Guard(_ok, token="secret", peers=auth.networks("10.0.0.0/8"))
    async with _client(guard, peer="192.168.1.9") as c:
        resp = await c.get("/mcp", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 403


def test_networks_parses_addresses_and_ranges() -> None:
    nets = auth.networks("160.79.104.0/21,2607:6bc0::/48, 127.0.0.1")
    assert [str(n) for n in nets] == [
        "160.79.104.0/21",
        "2607:6bc0::/48",
        "127.0.0.1/32",
    ]
    assert auth.networks("") == ()
