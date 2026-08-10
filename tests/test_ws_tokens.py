"""WebSocket-token tests (mocked httpx) — ENG-9200.

The spec publishes two token minters for two different sockets, and until this
change the SDK implemented only the legacy one while *documenting* it as the way
to reach the current socket:

* ``POST /ws/token`` → the current ``GET /ws`` socket (public **and** per-account
  channels). ``GET /ws``'s own description names this operation as its minter.
* ``POST /ws-tokens`` → the legacy public ``GET /stream`` socket. Its description
  says "Prefer POST /ws/token" and that its token is "for the public /stream
  endpoint".

So the guarantee under test is a *pairing*, not a route: each method must target
its own path, and the two must stay distinct. Presenting a ``/stream`` token to
``/ws`` is not merely a 401 — per the spec the token encodes the account identity
that scopes the per-account channels, so the wrong token is an identity mismatch.

The docstring assertions are deliberate, not decoration. This SDK ships no
WebSocket client, so the docstrings are the *entire* user-facing guidance on
which token opens which socket; that is exactly what was wrong before, and a
plain route test would not have caught it.

Note the spec's ``operationId``s are swapped relative to every other signal
(``createWsTokenLegacy`` names the current route). These tests key on the path,
which is what the client sends.
"""

from __future__ import annotations

import pytest

from nexus_exchange import Client, MissingCredentialsError, Network, WsToken

BASE = "http://localhost:9090"
WS_TOKEN_URL = f"{BASE}/ws/token"
LEGACY_TOKEN_URL = f"{BASE}/ws-tokens"

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_TOKEN = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


# -- POST /ws/token (current) ------------------------------------------------


def test_mint_ws_token_targets_the_current_route(httpx_mock) -> None:
    httpx_mock.add_response(url=WS_TOKEN_URL, json={"token": _TOKEN})
    with _authed() as client:
        token = client.mint_ws_token()

    assert isinstance(token, WsToken)
    assert token.token == _TOKEN
    req = httpx_mock.get_request()
    assert req is not None
    assert req.url.path == "/ws/token"
    assert req.headers["x-api-key"] == "nx_test"
    # No requestBody in the spec: the empty body is what the HMAC digest covers,
    # so sending "{}" would sign something the server does not verify.
    assert req.content == b""


def test_mint_ws_token_requires_credentials() -> None:
    # Signed route: refuse locally rather than sending an unauthenticated POST.
    with Client(Network.LOCAL) as client:
        with pytest.raises(MissingCredentialsError):
            client.mint_ws_token()


def test_mint_ws_token_tolerates_a_missing_token_field(httpx_mock) -> None:
    httpx_mock.add_response(url=WS_TOKEN_URL, json={})
    with _authed() as client:
        assert client.mint_ws_token().token == ""


# -- POST /ws-tokens (legacy) ------------------------------------------------


def test_mint_web_socket_token_still_targets_the_legacy_route(httpx_mock) -> None:
    httpx_mock.add_response(url=LEGACY_TOKEN_URL, json={"token": _TOKEN})
    with _authed() as client:
        token = client.mint_web_socket_token()

    assert token.token == _TOKEN
    assert httpx_mock.get_request().url.path == "/ws-tokens"


def test_the_two_minters_are_distinct_operations(httpx_mock) -> None:
    # The bug this guards: one method serving both sockets, or either method
    # drifting onto the other's path. Both must be requested, at their own paths.
    httpx_mock.add_response(url=WS_TOKEN_URL, json={"token": "for-ws"})
    httpx_mock.add_response(url=LEGACY_TOKEN_URL, json={"token": "for-stream"})
    with _authed() as client:
        assert client.mint_ws_token().token == "for-ws"
        assert client.mint_web_socket_token().token == "for-stream"

    paths = [r.url.path for r in httpx_mock.get_requests()]
    assert paths == ["/ws/token", "/ws-tokens"]


# -- the pairing, as documented ---------------------------------------------


def test_each_socket_is_documented_with_its_own_minter() -> None:
    # `networks.py` documented `/ws` as taking a `/ws-tokens` token, which the
    # spec contradicts. There is no WebSocket client here, so these docstrings are
    # the only thing telling a user which token opens which socket.
    current = Client.mint_ws_token.__doc__ or ""
    legacy = Client.mint_web_socket_token.__doc__ or ""

    assert "POST /ws/token" in current
    assert "ws_authenticated_url" in current
    assert "/ws-tokens" not in current.split("Not interchangeable")[0]

    assert "POST /ws-tokens" in legacy
    assert "/stream" in legacy
    assert "mint_ws_token" in legacy  # points at the current minter


def test_the_two_socket_bases_stay_separate() -> None:
    for network in Network:
        assert network.ws_authenticated_url.endswith("/ws")
        assert network.ws_market_data_url.endswith("/stream")
