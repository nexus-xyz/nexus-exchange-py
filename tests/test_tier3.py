"""Unit tests for the Tier-3 trading methods (mocked httpx).

Mirrors the Rust SDK's coverage of ``amend_order`` / ``adjust_margin`` /
``set_leverage`` (ENG-5296): every method signs (asserted via the ``x-api-key``
header on the captured request), the body/query serialize exactly what the API
expects, money decodes as exact ``Decimal`` strings, and client-side validation
rejects empty/invalid input before any request is made.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from nexus_exchange import (
    AmendOrder,
    Client,
    LeverageUpdate,
    MarginAdjustment,
    Network,
)

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


# -- amend_order -------------------------------------------------------------


def test_amend_order_signs_sends_query_and_body(httpx_mock) -> None:
    # market_id rides as a signed query param (direct /api/v1 route).
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders/o1?market_id=BTC-USDX-PERP",
        method="PATCH",
        json={"order": {"id": "o1", "status": "open"}, "fills": []},
    )
    with _authed() as client:
        resp = client.amend_order(
            "o1", "BTC-USDX-PERP", AmendOrder(price=Decimal("51000"), size=Decimal("0.5"))
        )
    assert resp.order.id == "o1"
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert req.method == "PATCH"
    assert json.loads(req.content) == {"price": "51000", "size": "0.5"}


def test_amend_order_omits_unset_fields(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders/o1?market_id=BTC-USDX-PERP",
        method="PATCH",
        json={"order": {"id": "o1"}, "fills": []},
    )
    with _authed() as client:
        client.amend_order("o1", "BTC-USDX-PERP", AmendOrder(price=Decimal("51000")))
    assert json.loads(httpx_mock.get_request().content) == {"price": "51000"}


def test_amend_order_rejects_empty_amend() -> None:
    with _authed() as client:
        with pytest.raises(ValueError, match="at least one field"):
            client.amend_order("o1", "BTC-USDX-PERP", AmendOrder())


def test_amend_order_requires_market_id() -> None:
    with _authed() as client:
        with pytest.raises(ValueError, match="market_id"):
            client.amend_order("o1", "", AmendOrder(price=Decimal("1")))


# -- adjust_margin -----------------------------------------------------------


def test_adjust_margin_signs_and_serializes(httpx_mock) -> None:
    # Legacy gateway route (no /api/v1 prefix).
    httpx_mock.add_response(
        url="http://localhost:9090/account/margin",
        method="POST",
        json={
            "market_id": "BTC-USDX-PERP",
            "allocated_margin": "150.00",
            "collateral": "850.00",
        },
    )
    with _authed() as client:
        adj = client.adjust_margin("BTC-USDX-PERP", "add", Decimal("100"))
    assert isinstance(adj, MarginAdjustment)
    assert adj.allocated_margin == Decimal("150.00")
    assert adj.collateral == Decimal("850.00")
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert json.loads(req.content) == {
        "market_id": "BTC-USDX-PERP",
        "direction": "add",
        "amount": "100",
    }


def test_adjust_margin_rejects_bad_direction() -> None:
    with _authed() as client:
        with pytest.raises(ValueError, match="add.*remove"):
            client.adjust_margin("BTC-USDX-PERP", "sideways", Decimal("100"))


def test_adjust_margin_rejects_nonpositive_amount() -> None:
    with _authed() as client:
        with pytest.raises(ValueError, match="positive"):
            client.adjust_margin("BTC-USDX-PERP", "add", Decimal("0"))


# -- set_leverage ------------------------------------------------------------


def test_set_leverage_signs_and_serializes(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/account/leverage",
        method="POST",
        json={"market_id": "BTC-USDX-PERP", "leverage": 10},
    )
    with _authed() as client:
        upd = client.set_leverage("BTC-USDX-PERP", 10)
    assert isinstance(upd, LeverageUpdate)
    assert upd.leverage == 10
    assert upd.market_id == "BTC-USDX-PERP"
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert json.loads(req.content) == {"market_id": "BTC-USDX-PERP", "leverage": 10}


def test_set_leverage_rejects_below_one() -> None:
    with _authed() as client:
        with pytest.raises(ValueError, match="at least 1"):
            client.set_leverage("BTC-USDX-PERP", 0)
