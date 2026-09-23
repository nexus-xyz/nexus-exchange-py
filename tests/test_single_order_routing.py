"""Single-order routes carry the engine-required ``?market_id=`` (ENG-17118).

The engine routes a single-order operation straight to the owning market actor
(ENG-3123), and ``market_id`` in its ``OrderRoutingQuery`` is a plain ``String``,
so a request without it is rejected at the extractor before any handler runs. The
pinned spec marks it a required query parameter on every ``/orders/{order_id}``
operation.

``cancel_order`` and ``fetch_order`` shipped without it, and nothing noticed
because no test looked at the outgoing URL. These do: each asserts the exact URL
on the wire and that the query is inside the signed canonical string, so dropping
the parameter — or sending it unsigned — fails here.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from nexus_exchange import Client, Network

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_BASE = "http://localhost:9090"
_MARKET = "BTC-USDX-PERP"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _assert_signed_with_query(req, method: str, path: str, query: str) -> None:
    assert req.method == method
    assert req.url.raw_path.decode() == f"{path}?{query}"
    ts = req.headers["x-timestamp"]
    body_hash = hashlib.sha256(req.content).hexdigest()
    canonical = "\n".join([ts, method, path, query, body_hash])
    expected = hmac.new(bytes.fromhex(_SECRET), canonical.encode(), hashlib.sha256).hexdigest()
    assert req.headers["x-signature"] == expected


def test_cancel_order_sends_market_id(httpx_mock) -> None:
    url = f"{_BASE}/api/v1/orders/o1?market_id={_MARKET}"
    httpx_mock.add_response(url=url, method="DELETE", json={"cancelled": True})
    with _authed() as client:
        assert client.cancel_order("o1", _MARKET) == {"cancelled": True}
    req = httpx_mock.get_request()
    assert str(req.url) == url
    _assert_signed_with_query(req, "DELETE", "/api/v1/orders/o1", f"market_id={_MARKET}")


def test_fetch_order_sends_market_id(httpx_mock) -> None:
    url = f"{_BASE}/orders/o1?market_id={_MARKET}"
    httpx_mock.add_response(url=url, method="GET", json={"id": "o1", "market_id": _MARKET})
    with _authed() as client:
        assert client.fetch_order("o1", _MARKET).id == "o1"
    req = httpx_mock.get_request()
    assert str(req.url) == url
    _assert_signed_with_query(req, "GET", "/orders/o1", f"market_id={_MARKET}")


def test_market_id_and_order_id_are_encoded(httpx_mock) -> None:
    # Neither value may break out of its slot: a reserved character in the order
    # id stays in the path segment, one in the market id stays in the query value.
    url = f"{_BASE}/api/v1/orders/a%2Fb?market_id=X%26Y"
    httpx_mock.add_response(url=url, method="DELETE", json={})
    with _authed() as client:
        client.cancel_order("a/b", "X&Y")
    assert str(httpx_mock.get_request().url) == url


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.cancel_order("o1", ""),
        lambda c: c.fetch_order("o1", ""),
    ],
    ids=["cancel_order", "fetch_order"],
)
def test_empty_market_id_fails_locally(call) -> None:
    # No response is registered: pytest-httpx fails the test if a request leaves.
    with _authed() as client:
        with pytest.raises(ValueError, match="market_id is required"):
            call(client)


@pytest.mark.parametrize("name", ["cancel_order", "fetch_order"])
def test_market_id_is_not_optional(name) -> None:
    # A default would reintroduce the argless call the engine rejects.
    with _authed() as client:
        with pytest.raises(TypeError):
            getattr(client, name)("o1")
