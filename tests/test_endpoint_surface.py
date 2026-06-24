"""Path + verb + parse coverage for the signed endpoint surface (mocked httpx).

``test_account_orders.py`` covers the headline account/order flows; this file
fills the e2e gaps for the rest of the #9 surface — positions, withdrawals,
single-order fetch, cancel-all, key/agent revocation, the WS-token mint, and the
admin tier reads/writes — and pins, for *every* typed method, the exact path the
SDK hits, the HTTP verb it uses, and that the request was signed. Together with
the existing files this exercises every implemented REST route at least once.
"""

from __future__ import annotations

import pytest

from nexus_exchange import Client, MissingCredentialsError, Network

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_BASE = "http://localhost:9090"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _assert_signed(req, method: str, path: str) -> None:
    """Every signed request carries the HMAC headers and hits the right route."""
    assert req.method == method
    assert req.url.raw_path.decode().split("?")[0] == path
    assert req.headers["x-api-key"] == "nx_test"
    assert "x-timestamp" in req.headers
    assert "x-signature" in req.headers


# -- signed account reads with thin existing coverage --------------------------
def test_fetch_positions_parses_and_signs(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/positions",
        json=[
            {
                "market_id": "ETH-USDX-PERP",
                "side": "short",
                "size": "2",
                "entry_price": "3000",
                "unrealized_pnl": "-5.5",
                "realized_pnl": "0",
                "liquidation_price": "3300",
            }
        ],
    )
    with _authed() as client:
        positions = client.fetch_positions()
    assert positions[0].market_id == "ETH-USDX-PERP"
    assert positions[0].side == "short"
    assert str(positions[0].liquidation_price) == "3300"
    _assert_signed(httpx_mock.get_request(), "GET", "/positions")


def test_fetch_withdrawals_parses_and_signs(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/withdrawals",
        json=[{"id": "w1", "amount": "250.00", "timestamp": 1776033900000, "status": "pending"}],
    )
    with _authed() as client:
        withdrawals = client.fetch_withdrawals()
    assert withdrawals[0].id == "w1"
    assert str(withdrawals[0].amount) == "250.00"
    assert withdrawals[0].status == "pending"
    _assert_signed(httpx_mock.get_request(), "GET", "/withdrawals")


# -- single-order fetch + cancel-all -------------------------------------------
def test_fetch_order_hits_id_path_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/orders/o42",
        json={
            "id": "o42",
            "market_id": "BTC-USDX-PERP",
            "side": "Buy",
            "order_type": "Limit",
            "price": "49000",
            "quantity": "0.25",
            "filled_qty": "0",
            "status": "Open",
            "time_in_force": "GTC",
            "created_at": 1776033900000,
            "updated_at": 1776033900000,
        },
    )
    with _authed() as client:
        order = client.fetch_order("o42")
    assert order.id == "o42"
    assert str(order.price) == "49000"
    _assert_signed(httpx_mock.get_request(), "GET", "/orders/o42")


def test_cancel_all_orders_signs_delete_collection(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/orders", method="DELETE", json={"cancelled": 3})
    with _authed() as client:
        result = client.cancel_all_orders()
    assert result == {"cancelled": 3}
    _assert_signed(httpx_mock.get_request(), "DELETE", "/orders")


# -- keys / agents revocation + WS token ---------------------------------------
def test_delete_api_key_hits_id_path(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/keys/nx_a", method="DELETE", json={"deleted": True})
    with _authed() as client:
        client.delete_api_key("nx_a")
    _assert_signed(httpx_mock.get_request(), "DELETE", "/keys/nx_a")


def test_revoke_agent_hits_address_path(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/agents/0xagent", method="DELETE", json={"revoked": True})
    with _authed() as client:
        client.revoke_agent("0xagent")
    _assert_signed(httpx_mock.get_request(), "DELETE", "/agents/0xagent")


def test_mint_web_socket_token_posts_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/ws-tokens", method="POST", json={"token": "wst_abc123"})
    with _authed() as client:
        tok = client.mint_web_socket_token()
    assert tok.token == "wst_abc123"
    _assert_signed(httpx_mock.get_request(), "POST", "/ws-tokens")


# -- admin tier reads / reset --------------------------------------------------
def test_fetch_tier_overrides_parses_list(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/admin/tiers",
        method="GET",
        json=[
            {"address": "0xabc", "tier": "marketmaker"},
            {"address": "0xdef", "tier": "pro"},
        ],
    )
    with _authed() as client:
        overrides = client.fetch_tier_overrides()
    assert [o.tier for o in overrides] == ["marketmaker", "pro"]
    _assert_signed(httpx_mock.get_request(), "GET", "/admin/tiers")


def test_reset_account_tier_deletes_address_path(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/admin/tiers/0xabc", method="DELETE", json={"reset": True})
    with _authed() as client:
        client.reset_account_tier("0xabc")
    _assert_signed(httpx_mock.get_request(), "DELETE", "/admin/tiers/0xabc")


# -- path-encoding + verb assertions for the public surface --------------------
def test_market_id_is_url_encoded_in_path(httpx_mock) -> None:
    # A market id with a slash must be percent-encoded into a single path segment
    # so it can't traverse the route tree.
    httpx_mock.add_response(
        url=f"{_BASE}/markets/BTC%2FUSDX/ticker",
        json={"symbol": "BTC/USDX"},
    )
    with Client(Network.LOCAL) as client:
        ticker = client.fetch_ticker("BTC/USDX")
    assert ticker.market_id == "BTC/USDX"
    assert httpx_mock.get_request().url.raw_path.decode() == "/markets/BTC%2FUSDX/ticker"


def test_create_order_uses_post_verb(httpx_mock) -> None:
    from decimal import Decimal

    from nexus_exchange import OrderRequest

    httpx_mock.add_response(
        url=f"{_BASE}/orders",
        method="POST",
        json={
            "order": {
                "id": "o1",
                "market_id": "BTC-USDX-PERP",
                "side": "Buy",
                "order_type": "Market",
                "quantity": "0.1",
                "filled_qty": "0.1",
                "status": "Filled",
                "time_in_force": "IOC",
                "created_at": 1,
                "updated_at": 1,
            },
            "fills": [],
        },
    )
    with _authed() as client:
        resp = client.create_order(OrderRequest.market("BTC-USDX-PERP", "Buy", Decimal("0.1")))
    assert resp.order.status == "Filled"
    _assert_signed(httpx_mock.get_request(), "POST", "/orders")


# -- credential guard applies across the whole signed surface ------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.fetch_positions(),
        lambda c: c.fetch_withdrawals(),
        lambda c: c.fetch_order("o1"),
        lambda c: c.cancel_all_orders(),
        lambda c: c.fetch_api_keys(),
        lambda c: c.delete_api_key("nx_a"),
        lambda c: c.fetch_agents(),
        lambda c: c.revoke_agent("0xagent"),
        lambda c: c.mint_web_socket_token(),
        lambda c: c.fetch_tier_overrides(),
        lambda c: c.reset_account_tier("0xabc"),
    ],
)
def test_signed_methods_require_credentials(call) -> None:
    with Client(Network.LOCAL) as client:
        with pytest.raises(MissingCredentialsError):
            call(client)
