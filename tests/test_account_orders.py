"""Unit tests for the signed account / orders / admin endpoints (mocked httpx).

Mirrors the Rust SDK's ``tests/account.rs`` and ``tests/orders.rs``: every
method signs (asserted via the ``x-api-key`` header on the captured request),
money decodes as exact ``Decimal`` strings, optional fields tolerate omission,
and ``OrderRequest`` serializes the body the API expects.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from nexus_exchange import (
    BatchOrderResult,
    Client,
    MissingCredentialsError,
    Network,
    OrderRequest,
)

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def test_fetch_balance_parses_and_signs(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/account",
        json={
            "balance": "1000.00",
            "collateral": "1000.00",
            "equity": "1012.34",
            "available_margin": "812.34",
            "positions": [
                {
                    "market_id": "BTC-USDX-PERP",
                    "side": "long",
                    "size": "0.5",
                    "entry_price": "50000",
                    "unrealized_pnl": "12.34",
                    "realized_pnl": "0",
                    "liquidation_price": "40000",
                }
            ],
        },
    )
    with _authed() as client:
        acct = client.fetch_balance()
    assert acct.equity == Decimal("1012.34")
    assert acct.positions[0].market_id == "BTC-USDX-PERP"
    assert acct.positions[0].liquidation_price == Decimal("40000")
    assert httpx_mock.get_request().headers["x-api-key"] == "nx_test"


def test_fetch_balance_tolerates_missing_liquidation_price(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/account",
        json={
            "balance": "1000.00",
            "collateral": "1000.00",
            "equity": "1000.00",
            "available_margin": "1000.00",
            "positions": [
                {
                    "market_id": "ETH-USDX-PERP",
                    "side": "long",
                    "size": "1",
                    "entry_price": "3000",
                    "unrealized_pnl": "0",
                    "realized_pnl": "0",
                }
            ],
        },
    )
    with _authed() as client:
        acct = client.fetch_balance()
    assert acct.positions[0].liquidation_price is None


def test_fetch_my_trades_parses_fills(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/fills",
        json=[
            {
                "id": "f1",
                "order_id": "o1",
                "market_id": "BTC-USDX-PERP",
                "side": "sell",
                "price": "50010.5",
                "size": "0.1",
                "fee": "0.25",
                "taker_or_maker": "maker",
                "timestamp": 1776033900000,
                "is_liquidation": False,
            }
        ],
    )
    with _authed() as client:
        fills = client.fetch_my_trades()
    assert fills[0].side == "sell"
    assert fills[0].fee == Decimal("0.25")


def test_fetch_rate_limit_status_handles_nulls(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/account/rate-limit",
        json={"tier": "unlimited", "limit": None, "remaining": None, "reset_at_ms": None},
    )
    with _authed() as client:
        rl = client.fetch_rate_limit_status()
    assert rl.tier == "unlimited"
    assert rl.limit is None


def test_deposit_sends_amount_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/account/deposit", json={"balance": "1500.00"}
    )
    with _authed() as client:
        result = client.deposit("500.00")
    assert result.balance == Decimal("1500.00")
    req = httpx_mock.get_request()
    assert json.loads(req.content) == {"amount": "500.00"}


def test_claim_credit_omits_amount_when_none(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/account/credit",
        json={"amount": "100", "credited_today": "100", "daily_limit": "1000"},
    )
    with _authed() as client:
        result = client.claim_credit()
    assert result.daily_limit == Decimal("1000")
    assert json.loads(httpx_mock.get_request().content) == {}


def test_create_order_serializes_limit_body(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders",
        json={
            "order": {
                "id": "o1",
                "market_id": "BTC-USDX-PERP",
                "side": "Buy",
                "order_type": "Limit",
                "price": "50000",
                "quantity": "0.5",
                "filled_qty": "0",
                "status": "Open",
                "time_in_force": "GTC",
                "created_at": 1776033900000,
                "updated_at": 1776033900000,
            },
            "fills": [],
        },
    )
    order = OrderRequest.limit("BTC-USDX-PERP", "Buy", Decimal("50000"), Decimal("0.5"))
    with _authed() as client:
        resp = client.create_order(order)
    assert resp.order.id == "o1"
    assert resp.order.price == Decimal("50000")
    body = json.loads(httpx_mock.get_request().content)
    assert body == {
        "market_id": "BTC-USDX-PERP",
        "side": "Buy",
        "order_type": "Limit",
        "quantity": "0.5",
        "time_in_force": "GTC",
        "price": "50000",
    }


def test_create_order_post_only_sends_exact_wire_value(httpx_mock) -> None:
    # PostOnly is PascalCase on the wire (unlike uppercase GTC/IOC/FOK) — the
    # engine rejects "POSTONLY", so the value must pass through verbatim.
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders",
        json={
            "order": {
                "id": "o2",
                "market_id": "BTC-USDX-PERP",
                "side": "Buy",
                "order_type": "Limit",
                "price": "50000",
                "quantity": "0.5",
                "filled_qty": "0",
                "status": "Open",
                "time_in_force": "PostOnly",
                "created_at": 1776033900000,
                "updated_at": 1776033900000,
            },
            "fills": [],
        },
    )
    order = OrderRequest.limit(
        "BTC-USDX-PERP", "Buy", Decimal("50000"), Decimal("0.5"), time_in_force="PostOnly"
    )
    with _authed() as client:
        resp = client.create_order(order)
    body = json.loads(httpx_mock.get_request().content)
    assert body["time_in_force"] == "PostOnly"
    assert resp.order.time_in_force == "PostOnly"


def test_market_order_omits_price() -> None:
    payload = OrderRequest.market("BTC-USDX-PERP", "Sell", Decimal("1")).to_payload()
    assert "price" not in payload
    assert payload["order_type"] == "Market"
    assert payload["time_in_force"] == "IOC"


def test_create_orders_batch_sends_array(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders/batch", json=[{"outcome": "ok"}]
    )
    orders = [
        OrderRequest.limit("BTC-USDX-PERP", "Buy", Decimal("50000"), Decimal("0.1")),
        OrderRequest.market("ETH-USDX-PERP", "Sell", Decimal("1")),
    ]
    with _authed() as client:
        client.create_orders(orders)
    body = json.loads(httpx_mock.get_request().content)
    assert isinstance(body, list) and len(body) == 2
    assert body[0]["market_id"] == "BTC-USDX-PERP"


def test_create_orders_batch_parses_typed_results(httpx_mock) -> None:
    # Per-order results, in request order: one placed order (ok) and one rejection
    # (err). The batch is non-atomic, so both outcomes coexist in a 201 response.
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders/batch",
        json=[
            {
                "outcome": "ok",
                "order": {
                    "id": "o1",
                    "market_id": "BTC-USDX-PERP",
                    "side": "Buy",
                    "order_type": "Limit",
                    "price": "50000",
                    "quantity": "0.1",
                    "filled_qty": "0.1",
                    "status": "Filled",
                    "time_in_force": "GTC",
                    "created_at": 1776033900000,
                    "updated_at": 1776033900000,
                },
                "fills": [
                    {
                        "id": "f1",
                        "order_id": "o1",
                        "market_id": "BTC-USDX-PERP",
                        "side": "buy",
                        "price": "50000",
                        "size": "0.1",
                        "fee": "0.25",
                        "taker_or_maker": "taker",
                        "timestamp": 1776033900000,
                        "is_liquidation": False,
                    }
                ],
            },
            {
                "outcome": "err",
                "error": "insufficient_margin",
                "message": "not enough collateral for this order",
            },
        ],
    )
    orders = [
        OrderRequest.limit("BTC-USDX-PERP", "Buy", Decimal("50000"), Decimal("0.1")),
        OrderRequest.market("ETH-USDX-PERP", "Sell", Decimal("1")),
    ]
    with _authed() as client:
        results = client.create_orders(orders)

    assert isinstance(results, list) and len(results) == 2
    assert all(isinstance(r, BatchOrderResult) for r in results)

    ok, err = results
    assert ok.is_ok and not ok.is_err
    assert ok.outcome == "ok"
    assert ok.order is not None
    assert ok.order.id == "o1"
    assert ok.order.price == Decimal("50000")
    assert len(ok.fills) == 1
    assert ok.fills[0].fee == Decimal("0.25")
    assert ok.error is None and ok.message is None

    assert err.is_err and not err.is_ok
    assert err.outcome == "err"
    assert err.order is None
    assert err.fills == []
    assert err.error == "insufficient_margin"
    assert err.message == "not enough collateral for this order"


def test_fetch_open_orders_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders",
        json=[
            {
                "id": "o1",
                "market_id": "BTC-USDX-PERP",
                "side": "Buy",
                "order_type": "Limit",
                "price": "50000",
                "quantity": "0.5",
                "filled_qty": "0.1",
                "status": "PartiallyFilled",
                "time_in_force": "GTC",
                "created_at": 1776033900000,
                "updated_at": 1776033900001,
            }
        ],
    )
    with _authed() as client:
        orders = client.fetch_open_orders()
    assert orders[0].filled_qty == Decimal("0.1")
    assert orders[0].status == "PartiallyFilled"


def test_cancel_order_signs_delete(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders/o1", method="DELETE", json={"cancelled": True}
    )
    with _authed() as client:
        client.cancel_order("o1")
    req = httpx_mock.get_request()
    assert req.method == "DELETE"
    assert req.headers["x-api-key"] == "nx_test"


def test_fetch_api_keys_and_agents(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/keys", json=[{"key_id": "nx_a", "tier": "pro"}]
    )
    httpx_mock.add_response(
        url="http://localhost:9090/agents",
        json=[
            {
                "address": "0xagent",
                "expiresAt": 1776033900000,
                "registeredAt": 1776000000000,
                "label": "my-bot",
            }
        ],
    )
    with _authed() as client:
        keys = client.fetch_api_keys()
        agents = client.fetch_agents()
    assert keys[0].key_id == "nx_a"
    # camelCase wire fields map onto snake_case attrs.
    assert agents[0].expires_at == 1776033900000
    assert agents[0].label == "my-bot"


def test_set_account_tier_sends_body(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/admin/tiers",
        method="PUT",
        json={"address": "0xabc", "tier": "marketmaker"},
    )
    with _authed() as client:
        override = client.set_account_tier("0xabc", "marketmaker")
    assert override.tier == "marketmaker"
    assert json.loads(httpx_mock.get_request().content) == {
        "address": "0xabc",
        "tier": "marketmaker",
    }


def test_signed_endpoint_without_credentials_raises() -> None:
    with Client(Network.LOCAL) as client:
        with pytest.raises(MissingCredentialsError):
            client.fetch_balance()
