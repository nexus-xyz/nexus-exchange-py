"""Unit tests for TrailingLimit order placement (mocked httpx).

Covers the request side of the ``TrailingLimit`` order type (ENG-6131): a
``trailing_limit`` request serializes exactly the expected body — with the two
basis-point offsets as JSON integers and no ``price`` — the request is signed
(asserted via the ``x-api-key`` header), client-side validation rejects
missing/zero/negative offsets before any request, and the ``Order`` response
round-trips the nullable ``limit_offset_bps`` integer.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from nexus_exchange import (
    Client,
    Network,
    Order,
    OrderRequest,
)

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


# -- create_order (TrailingLimit) --------------------------------------------


def test_create_trailing_limit_order_signs_and_serializes(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders",
        json={
            "order": {
                "id": "o1",
                "market_id": "BTC-USDX-PERP",
                "side": "Buy",
                "order_type": "TrailingLimit",
                "price": None,
                "quantity": "0.5",
                "filled_qty": "0",
                "status": "Open",
                "time_in_force": "GTC",
                "created_at": 1776033900000,
                "updated_at": 1776033900000,
                "limit_offset_bps": 25,
            },
            "fills": [],
        },
    )
    order = OrderRequest.trailing_limit("BTC-USDX-PERP", "Buy", Decimal("0.5"), 100, 25)
    with _authed() as client:
        resp = client.create_order(order)
    assert resp.order.id == "o1"
    assert resp.order.order_type == "TrailingLimit"

    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    body = json.loads(req.content)
    assert body == {
        "market_id": "BTC-USDX-PERP",
        "side": "Buy",
        "order_type": "TrailingLimit",
        "quantity": "0.5",
        "time_in_force": "GTC",
        "trailing_offset_bps": 100,
        "limit_offset_bps": 25,
    }
    # The bps offsets ride the wire as JSON integers, not stringified.
    assert isinstance(body["trailing_offset_bps"], int)
    assert isinstance(body["limit_offset_bps"], int)
    # No price: the limit price is computed server-side at fire time.
    assert "price" not in body


def test_trailing_limit_payload_omits_price_and_reduce_only() -> None:
    payload = OrderRequest.trailing_limit(
        "BTC-USDX-PERP", "Sell", Decimal("1"), 50, 10
    ).to_payload()
    assert payload["order_type"] == "TrailingLimit"
    assert "price" not in payload
    assert "reduce_only" not in payload
    assert payload["trailing_offset_bps"] == 50
    assert payload["limit_offset_bps"] == 10


def test_trailing_limit_includes_reduce_only_when_set() -> None:
    payload = OrderRequest.trailing_limit(
        "BTC-USDX-PERP", "Sell", Decimal("1"), 50, 10, reduce_only=True
    ).to_payload()
    assert payload["reduce_only"] is True


# -- client-side validation --------------------------------------------------


@pytest.mark.parametrize("trailing_offset_bps", [0, -1])
def test_trailing_limit_rejects_bad_trailing_offset(trailing_offset_bps) -> None:
    with pytest.raises(ValueError, match="trailing_offset_bps must be a positive integer"):
        OrderRequest.trailing_limit("BTC-USDX-PERP", "Buy", Decimal("1"), trailing_offset_bps, 25)


@pytest.mark.parametrize("limit_offset_bps", [0, -5])
def test_trailing_limit_rejects_bad_limit_offset(limit_offset_bps) -> None:
    with pytest.raises(ValueError, match="limit_offset_bps must be a positive integer"):
        OrderRequest.trailing_limit("BTC-USDX-PERP", "Buy", Decimal("1"), 100, limit_offset_bps)


def test_trailing_limit_rejects_non_integer_offsets() -> None:
    with pytest.raises(ValueError, match="trailing_offset_bps must be a positive integer"):
        OrderRequest.trailing_limit("BTC-USDX-PERP", "Buy", Decimal("1"), 1.5, 25)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="limit_offset_bps must be a positive integer"):
        OrderRequest.trailing_limit("BTC-USDX-PERP", "Buy", Decimal("1"), 100, "25")  # type: ignore[arg-type]


def test_trailing_limit_rejects_bool_offsets() -> None:
    # bool is an int subclass, so True/False would otherwise slip past the
    # `isinstance(x, int)` / `x > 0` checks and serialize as a JSON boolean.
    with pytest.raises(ValueError, match="trailing_offset_bps must be a positive integer"):
        OrderRequest.trailing_limit("BTC-USDX-PERP", "Buy", Decimal("1"), True, 25)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="limit_offset_bps must be a positive integer"):
        OrderRequest.trailing_limit("BTC-USDX-PERP", "Buy", Decimal("1"), 100, False)  # type: ignore[arg-type]


# -- Order round-trip --------------------------------------------------------


def test_order_round_trips_limit_offset_bps() -> None:
    order = Order.from_dict(
        {
            "id": "o1",
            "market_id": "BTC-USDX-PERP",
            "side": "Buy",
            "order_type": "TrailingLimit",
            "quantity": "0.5",
            "time_in_force": "GTC",
            "limit_offset_bps": 25,
        }
    )
    assert order.limit_offset_bps == 25


def test_order_limit_offset_bps_defaults_none_for_other_types() -> None:
    order = Order.from_dict(
        {
            "id": "o2",
            "market_id": "BTC-USDX-PERP",
            "side": "Buy",
            "order_type": "Limit",
            "price": "50000",
            "quantity": "0.5",
        }
    )
    assert order.limit_offset_bps is None
