"""Conditional order placement: stop / take-profit / trailing-stop (ENG-17125).

The pinned spec's ``OrderRequest`` takes six conditional ``order_type`` values.
``TrailingLimit`` has its own suite (``test_trailing_limit.py``); this one covers
the other five. Each test asserts the **exact** JSON body sent to
``POST /orders`` — ``trigger_price`` as a decimal string, no deprecated
``stop_price``, no ``price`` on the market-family — and then the client-side
checks the spec's per-type field requirements imply.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from nexus_exchange import Client, Network, OrderRequest

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_URL = "http://localhost:9090/api/v1/orders"
_M = "BTC-USDX-PERP"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _order_json(order_type: str, side: str) -> dict[str, Any]:
    return {
        "order": {
            "id": "o1",
            "market_id": _M,
            "side": side,
            "order_type": order_type,
            "price": None,
            "quantity": "0.5",
            "filled_qty": "0",
            "status": "Open",
            "time_in_force": "GTC",
            "created_at": 1776033900000,
            "updated_at": 1776033900000,
        },
        "fills": [],
    }


_CASES = [
    pytest.param(
        OrderRequest.stop_limit(_M, "Sell", Decimal("59000.50"), Decimal("58900"), Decimal("0.5")),
        {
            "market_id": _M,
            "side": "Sell",
            "order_type": "StopLimit",
            "quantity": "0.5",
            "time_in_force": "GTC",
            "price": "58900",
            "trigger_price": "59000.50",
        },
        id="StopLimit",
    ),
    pytest.param(
        OrderRequest.stop_market(_M, "Sell", Decimal("59000"), Decimal("0.5"), reduce_only=True),
        {
            "market_id": _M,
            "side": "Sell",
            "order_type": "StopMarket",
            "quantity": "0.5",
            "time_in_force": "GTC",
            "reduce_only": True,
            "trigger_price": "59000",
        },
        id="StopMarket",
    ),
    pytest.param(
        OrderRequest.take_profit_limit(
            _M, "Sell", Decimal("71000"), Decimal("71000.00"), Decimal("0.5"), "PostOnly"
        ),
        {
            "market_id": _M,
            "side": "Sell",
            "order_type": "TakeProfitLimit",
            "quantity": "0.5",
            "time_in_force": "PostOnly",
            "price": "71000.00",
            "trigger_price": "71000",
        },
        id="TakeProfitLimit",
    ),
    pytest.param(
        OrderRequest.take_profit_market(_M, "Buy", Decimal("0.00012345"), Decimal("0.5"), "IOC"),
        {
            "market_id": _M,
            "side": "Buy",
            "order_type": "TakeProfitMarket",
            "quantity": "0.5",
            "time_in_force": "IOC",
            "trigger_price": "0.00012345",
        },
        id="TakeProfitMarket",
    ),
    pytest.param(
        OrderRequest.trailing_stop(_M, "Sell", Decimal("0.5"), 150, reduce_only=False),
        {
            "market_id": _M,
            "side": "Sell",
            "order_type": "TrailingStop",
            "quantity": "0.5",
            "time_in_force": "GTC",
            "reduce_only": False,
            "trailing_offset_bps": 150,
        },
        id="TrailingStop",
    ),
]


@pytest.mark.parametrize(("order", "expected"), _CASES)
def test_create_order_sends_the_exact_body(
    httpx_mock, order: OrderRequest, expected: dict[str, Any]
) -> None:
    httpx_mock.add_response(url=_URL, json=_order_json(order.order_type, order.side))
    with _authed() as client:
        resp = client.create_order(order)
    assert resp.order.order_type == order.order_type

    req = httpx_mock.get_request()
    assert req.method == "POST"
    assert req.headers["x-api-key"] == "nx_test"
    body = json.loads(req.content)
    assert body == expected
    # Never the deprecated trigger field.
    assert "stop_price" not in body
    # Money rides as strings; bps as JSON integers.
    if "trigger_price" in body:
        assert isinstance(body["trigger_price"], str)
    if "trailing_offset_bps" in body:
        assert type(body["trailing_offset_bps"]) is int


def test_trigger_price_keeps_the_exact_decimal_text() -> None:
    # No float round-trip and no normalisation: trailing zeros reach the wire
    # exactly as the caller wrote the Decimal.
    for text in ("60000.10", "0.1", "0.00012345", "59000"):
        payload = OrderRequest.stop_market(_M, "Sell", Decimal(text), Decimal("1")).to_payload()
        assert payload["trigger_price"] == text


def test_trigger_price_renders_like_price() -> None:
    # Same rendering as `price` (``str(Decimal)``), including Python's exponent
    # form for very small or exponent-constructed values — one rule for all money.
    for text in ("1E+2", "0.000000001", "58900.00"):
        d = Decimal(text)
        payload = OrderRequest.stop_limit(_M, "Sell", d, d, Decimal("1")).to_payload()
        assert payload["trigger_price"] == payload["price"] == str(d)


def test_trailing_stop_accepts_a_zero_offset() -> None:
    # The spec documents 0 as valid: it fires at the first mark evaluation.
    payload = OrderRequest.trailing_stop(_M, "Buy", Decimal("1"), 0).to_payload()
    assert payload["trailing_offset_bps"] == 0


def test_create_orders_batch_carries_trigger_price(httpx_mock) -> None:
    httpx_mock.add_response(url=_URL + "/batch", json={"results": []})
    legs = [
        OrderRequest.take_profit_market(
            _M, "Sell", Decimal("71000"), Decimal("1"), reduce_only=True
        ),
        OrderRequest.stop_market(_M, "Sell", Decimal("59000"), Decimal("1"), reduce_only=True),
    ]
    with _authed() as client:
        client.create_orders(legs)
    body = json.loads(httpx_mock.get_request().content)
    assert [o["trigger_price"] for o in body] == ["71000", "59000"]
    assert all("stop_price" not in o for o in body)


def test_existing_payloads_are_unchanged() -> None:
    # Adding the field must not leak a `trigger_price` key into the old types.
    assert (
        "trigger_price"
        not in OrderRequest.limit(_M, "Buy", Decimal("1"), Decimal("1")).to_payload()
    )
    assert "trigger_price" not in OrderRequest.market(_M, "Buy", Decimal("1")).to_payload()
    assert (
        "trigger_price"
        not in OrderRequest.trailing_limit(_M, "Buy", Decimal("1"), 10, 5).to_payload()
    )


# -- client-side validation --------------------------------------------------


def _req(order_type: str, **kw: Any) -> OrderRequest:
    base: dict[str, Any] = {
        "market_id": _M,
        "side": "Sell",
        "order_type": order_type,
        "quantity": Decimal("1"),
        "time_in_force": "GTC",
    }
    base.update(kw)
    return OrderRequest(**base)


@pytest.mark.parametrize(
    "order_type", ["StopLimit", "StopMarket", "TakeProfitLimit", "TakeProfitMarket"]
)
def test_trigger_types_require_trigger_price(order_type: str) -> None:
    with pytest.raises(ValueError, match=f"{order_type} requires trigger_price"):
        _req(order_type, price=Decimal("1"))


@pytest.mark.parametrize("order_type", ["Limit", "StopLimit", "TakeProfitLimit"])
def test_limit_family_requires_price(order_type: str) -> None:
    with pytest.raises(ValueError, match=f"{order_type} requires price"):
        _req(order_type, trigger_price=Decimal("1") if order_type != "Limit" else None)


@pytest.mark.parametrize(
    ("order_type", "extra"),
    [
        ("Limit", {"price": Decimal("1")}),
        ("Market", {}),
        ("TrailingStop", {"trailing_offset_bps": 10}),
        ("TrailingLimit", {"trailing_offset_bps": 10, "limit_offset_bps": 5}),
    ],
)
def test_non_trigger_types_reject_trigger_price(order_type: str, extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=f"{order_type} does not take trigger_price"):
        _req(order_type, trigger_price=Decimal("1"), **extra)


@pytest.mark.parametrize(
    ("order_type", "extra"),
    [
        ("StopMarket", {"trigger_price": Decimal("1")}),
        ("TakeProfitMarket", {"trigger_price": Decimal("1")}),
        ("TrailingStop", {"trailing_offset_bps": 10}),
    ],
)
def test_market_family_conditionals_reject_price(order_type: str, extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=f"{order_type} does not take price"):
        _req(order_type, price=Decimal("1"), **extra)


def test_trailing_stop_requires_offset_and_rejects_limit_offset() -> None:
    with pytest.raises(ValueError, match="TrailingStop requires trailing_offset_bps"):
        _req("TrailingStop")
    with pytest.raises(ValueError, match="TrailingStop does not take limit_offset_bps"):
        _req("TrailingStop", trailing_offset_bps=10, limit_offset_bps=5)


@pytest.mark.parametrize("bad", [-1, 1.5, "10", True])
def test_trailing_stop_builder_rejects_a_bad_offset(bad: Any) -> None:
    with pytest.raises(ValueError, match="trailing_offset_bps must be a non-negative integer"):
        OrderRequest.trailing_stop(_M, "Sell", Decimal("1"), bad)


@pytest.mark.parametrize("bad", [59000.5, "59000", 59000, True])
def test_trigger_price_must_be_a_decimal(bad: Any) -> None:
    with pytest.raises(ValueError, match="trigger_price must be a decimal.Decimal"):
        OrderRequest.stop_market(_M, "Sell", bad, Decimal("1"))


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_trigger_price_must_be_finite(bad: str) -> None:
    with pytest.raises(ValueError, match="trigger_price must be a finite Decimal"):
        OrderRequest.stop_market(_M, "Sell", Decimal(bad), Decimal("1"))


def test_validation_fails_before_any_request(httpx_mock) -> None:
    # Nothing is registered on httpx_mock: a request would fail the test.
    with pytest.raises(ValueError):
        with _authed() as client:
            client.create_order(_req("StopMarket"))


def test_unknown_order_type_passes_through_unchecked() -> None:
    # Forward-compatible: a type a newer spec adds is not second-guessed here.
    payload = _req("SomeFutureType", trigger_price=Decimal("5")).to_payload()
    assert payload["order_type"] == "SomeFutureType"
    assert payload["trigger_price"] == "5"
