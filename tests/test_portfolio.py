"""Unit tests for the portfolio-parity surface (mocked httpx), ENG-6459.

Covers the three new signed reads — ``fetch_account_state``,
``fetch_account_fees``, ``fetch_portfolio_history`` — and the enriched
``Position`` risk fields added in Exchange API spec v0.7.2.

The load-bearing guarantees asserted here:

* every call signs (``x-api-key`` on the captured request) and hits the direct
  ``/api/v1`` surface;
* a figure the server does not report decodes to ``None``, never a defaulted
  ``0`` that would read as a real balance / fee / notional;
* the ``window`` enum reaches the wire as its *value* (``window=day``), not the
  ``str()`` repr of a ``str``-mixin enum member — which would also corrupt the
  signed canonical query;
* bad ``window`` / ``limit`` arguments raise before any request is sent;
* the fail-closed ``502 authoritative_margin_unavailable`` surfaces as an
  ``ApiError`` with its machine-readable code intact.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus_exchange import (
    AccountFees,
    AccountState,
    ApiError,
    Client,
    Network,
    PortfolioHistory,
    PortfolioWindow,
    Position,
)

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_BASE = "http://localhost:9090/api/v1"
_STATE_URL = f"{_BASE}/account/state"
_FEES_URL = f"{_BASE}/account/fees"
_HISTORY_URL = f"{_BASE}/account/portfolio-history"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


# The /positions example payload from the v0.7.2 spec, verbatim.
_ENRICHED_POSITION = {
    "market_id": "BTC-USDX-PERP",
    "side": "Long",
    "size": "0.5",
    "entry_price": "49500.00",
    "unrealized_pnl": "250.50",
    "realized_pnl": "0.00",
    "liquidation_price": "42000.00",
    "notional_value": "25000.50",
    "notional_value_error": None,
    "roe": "0.2004",
    "roe_error": None,
    "margin_used": "1250.03",
    "margin_used_error": None,
    "max_leverage": 20,
    "max_leverage_error": None,
    "funding_paid": "12.50",
    "leverage": None,
    "leverage_error": "margin_state_not_mirrored",
}


# -- enriched Position -------------------------------------------------------


def test_positions_decode_enriched_risk_fields(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/positions", method="GET", json=[_ENRICHED_POSITION])
    with _authed() as client:
        positions = client.fetch_positions()

    assert len(positions) == 1
    pos = positions[0]
    assert isinstance(pos, Position)
    # Pre-existing fields are untouched by the extension.
    assert pos.market_id == "BTC-USDX-PERP"
    assert pos.size == Decimal("0.5")
    assert pos.liquidation_price == Decimal("42000.00")
    # Enriched detail, exact decimals (no float round-trip).
    assert pos.notional_value == Decimal("25000.50")
    assert pos.roe == Decimal("0.2004")
    assert pos.margin_used == Decimal("1250.03")
    assert pos.max_leverage == 20
    assert pos.funding_paid == Decimal("12.50")
    # leverage is null server-side today, with the reason in its companion.
    assert pos.leverage is None
    assert pos.leverage_error == "margin_state_not_mirrored"
    assert pos.notional_value_error is None
    assert pos.roe_error is None
    assert httpx_mock.get_request().headers["x-api-key"] == "nx_test"


def test_position_null_risk_fields_carry_error_reasons() -> None:
    # Mark price unavailable: the server nulls the derived fields and names why.
    pos = Position.from_dict(
        {
            "market_id": "ETH-USDX-PERP",
            "side": "Short",
            "size": "2",
            "entry_price": "3000",
            "unrealized_pnl": "-10",
            "realized_pnl": "0",
            "notional_value": None,
            "notional_value_error": "mark_price_unavailable",
            "roe": None,
            "roe_error": "margin_used_zero",
            "margin_used": None,
            "margin_used_error": "mark_price_unavailable",
            "max_leverage": None,
            "max_leverage_error": "market_params_unavailable",
            "funding_paid": "0",
        }
    )
    assert pos.notional_value is None
    assert pos.notional_value_error == "mark_price_unavailable"
    assert pos.roe_error == "margin_used_zero"
    assert pos.margin_used_error == "mark_price_unavailable"
    assert pos.max_leverage is None
    assert pos.max_leverage_error == "market_params_unavailable"
    # "0" is a real reported value, distinct from "not reported".
    assert pos.funding_paid == Decimal("0")


def test_position_from_pre_v072_payload_leaves_risk_fields_none() -> None:
    # An older deployment omits the enriched block entirely. Nothing defaults to
    # 0 — a fabricated zero notional / funding would read as a real figure.
    pos = Position.from_dict(
        {
            "market_id": "BTC-USDX-PERP",
            "side": "Long",
            "size": "1",
            "entry_price": "50000",
            "unrealized_pnl": "0",
            "realized_pnl": "0",
        }
    )
    assert pos.notional_value is None
    assert pos.roe is None
    assert pos.margin_used is None
    assert pos.max_leverage is None
    assert pos.leverage is None
    assert pos.funding_paid is None
    # No error reason either: absent is not the same as server-reported null.
    assert pos.notional_value_error is None
    assert pos.leverage_error is None


# -- fetch_account_state -----------------------------------------------------


def test_fetch_account_state_signs_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url=_STATE_URL,
        method="GET",
        json={
            "summary": {
                "collateral": "10000.00",
                "total_equity": "10250.50",
                "total_unrealized_pnl": "250.50",
                "total_realized_pnl_24h": "0.00",
                "total_volume_24h": "50000.00",
                "open_positions_count": 1,
                "open_orders_count": 3,
                "margin_used": "1250.03",
                "available_margin": "8500.00",
                "withdrawable": "8500.00",
            },
            "positions": [_ENRICHED_POSITION],
        },
    )
    with _authed() as client:
        state = client.fetch_account_state()

    assert isinstance(state, AccountState)
    assert state.summary.total_equity == Decimal("10250.50")
    assert state.summary.withdrawable == Decimal("8500.00")
    assert state.summary.open_orders_count == 3
    # The endpoint's coherent-read guarantee, as returned.
    assert state.summary.open_positions_count == len(state.positions) == 1
    assert state.positions[0].notional_value == Decimal("25000.50")
    # Gate not active → None, which is not "denied".
    assert state.summary.early_access_allowed is None
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert str(req.url) == _STATE_URL


def test_fetch_account_state_unreported_fields_stay_none(httpx_mock) -> None:
    # A deployment that predates `withdrawable` (or a slimmer payload): every
    # absent figure is None rather than Decimal(0).
    httpx_mock.add_response(
        url=_STATE_URL,
        method="GET",
        json={"summary": {"total_equity": "100.00"}, "positions": []},
    )
    with _authed() as client:
        state = client.fetch_account_state()

    assert state.summary.total_equity == Decimal("100.00")
    assert state.summary.withdrawable is None
    assert state.summary.collateral is None
    assert state.summary.margin_used is None
    assert state.summary.open_positions_count is None
    assert state.positions == []


def test_fetch_account_state_tolerates_missing_summary(httpx_mock) -> None:
    # A re-shaped / empty payload still decodes rather than raising: the summary
    # is all-optional in the spec.
    httpx_mock.add_response(url=_STATE_URL, method="GET", json={})
    with _authed() as client:
        state = client.fetch_account_state()
    assert state.summary.withdrawable is None
    assert state.positions == []


def test_fetch_account_state_zero_withdrawable_is_not_none(httpx_mock) -> None:
    # An underwater account is clamped to "0" server-side. That is a real,
    # authoritative value and must not collapse into None.
    httpx_mock.add_response(
        url=_STATE_URL,
        method="GET",
        json={"summary": {"available_margin": "-25.00", "withdrawable": "0"}, "positions": []},
    )
    with _authed() as client:
        state = client.fetch_account_state()
    assert state.summary.withdrawable == Decimal("0")
    assert state.summary.withdrawable is not None
    # Free margin itself may still be negative; only `withdrawable` is floored.
    assert state.summary.available_margin == Decimal("-25.00")


def test_fetch_account_state_fails_closed_on_502(httpx_mock) -> None:
    # The authoritative margin view is down: the endpoint must surface the error
    # rather than any locally-estimated balance.
    httpx_mock.add_response(
        url=_STATE_URL,
        method="GET",
        status_code=502,
        json={"code": "authoritative_margin_unavailable"},
    )
    with _authed() as client, pytest.raises(ApiError) as excinfo:
        client.fetch_account_state()
    assert excinfo.value.status == 502
    assert excinfo.value.code == "authoritative_margin_unavailable"


# -- fetch_account_fees ------------------------------------------------------


def test_fetch_account_fees_signs_and_parses(httpx_mock) -> None:
    # The spec's own example payload.
    httpx_mock.add_response(
        url=_FEES_URL,
        method="GET",
        json={
            "maker_fee_bps": -2,
            "taker_fee_bps": 5,
            "tier": "base",
            "schedule": "standard",
            "volume_30d": "101005.00",
            "volume_30d_estimated": False,
            "discounts": [],
        },
    )
    with _authed() as client:
        fees = client.fetch_account_fees()

    assert isinstance(fees, AccountFees)
    # A negative maker fee is a rebate paid to the maker — sign preserved.
    assert fees.maker_fee_bps == -2
    assert fees.taker_fee_bps == 5
    assert fees.tier == "base"
    assert fees.schedule == "standard"
    assert fees.volume_30d == Decimal("101005.00")
    assert fees.volume_30d_estimated is False
    assert fees.discounts == []
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert str(req.url) == _FEES_URL


def test_fetch_account_fees_treats_null_estimated_as_true(httpx_mock) -> None:
    # An explicit null is not a claim of full coverage either — only `false` is.
    httpx_mock.add_response(
        url=_FEES_URL,
        method="GET",
        json={
            "maker_fee_bps": -2,
            "taker_fee_bps": 5,
            "tier": None,
            "schedule": None,
            "volume_30d": "1",
            "volume_30d_estimated": None,
        },
    )
    with _authed() as client:
        fees = client.fetch_account_fees()
    assert fees.volume_30d_estimated is True
    # A null open string decodes to "", never the string "None".
    assert fees.tier == ""
    assert fees.schedule == ""


def test_fetch_account_fees_defaults_estimated_to_true(httpx_mock) -> None:
    # Absent flag: assume the 30d figure may undercount rather than claiming a
    # full window the server never asserted.
    httpx_mock.add_response(
        url=_FEES_URL,
        method="GET",
        json={
            "maker_fee_bps": 0,
            "taker_fee_bps": 5,
            "tier": "base",
            "schedule": "standard",
            "volume_30d": "0",
        },
    )
    with _authed() as client:
        fees = client.fetch_account_fees()
    assert fees.volume_30d_estimated is True
    # An explicit 0 bps maker fee is a real rate, not a missing one.
    assert fees.maker_fee_bps == 0
    assert fees.discounts == []


def test_fetch_account_fees_rejects_payload_missing_a_rate(httpx_mock) -> None:
    # Reporting 0 bps for an absent rate would read as "trading is free"; the
    # decode fails loudly instead.
    httpx_mock.add_response(url=_FEES_URL, method="GET", json={"tier": "base"})
    with _authed() as client, pytest.raises(ValueError):
        client.fetch_account_fees()


def test_fetch_account_fees_drops_non_object_discounts(httpx_mock) -> None:
    httpx_mock.add_response(
        url=_FEES_URL,
        method="GET",
        json={
            "maker_fee_bps": -2,
            "taker_fee_bps": 5,
            "tier": "base",
            "schedule": "standard",
            "volume_30d": "1",
            "volume_30d_estimated": False,
            "discounts": [{"kind": "promo"}, "bogus", None],
        },
    )
    with _authed() as client:
        fees = client.fetch_account_fees()
    assert fees.discounts == [{"kind": "promo"}]
    # The untouched payload is still reachable.
    assert fees.raw["discounts"] == [{"kind": "promo"}, "bogus", None]


# -- fetch_portfolio_history -------------------------------------------------


_HISTORY_BODY = {
    "window": "week",
    "cadence_ms": 3600000,
    "points": [
        {"timestamp_ms": 1758000000000, "equity": "10000.00", "pnl": "0.00", "volume": "0.00"},
        {
            "timestamp_ms": 1758003600000,
            "equity": "10250.50",
            "pnl": "250.50",
            "volume": "50000.00",
        },
    ],
}


def test_fetch_portfolio_history_defaults_to_no_query(httpx_mock) -> None:
    # Omitting both arguments must send a bare path — the server applies its own
    # `day` default and echoes it.
    httpx_mock.add_response(
        url=_HISTORY_URL,
        method="GET",
        json={"window": "day", "cadence_ms": 300000, "points": []},
    )
    with _authed() as client:
        history = client.fetch_portfolio_history()

    assert isinstance(history, PortfolioHistory)
    assert history.window == "day"
    assert history.cadence_ms == 300000
    assert history.points == []
    req = httpx_mock.get_request()
    assert str(req.url) == _HISTORY_URL
    assert req.headers["x-api-key"] == "nx_test"


def test_fetch_portfolio_history_sends_enum_value_not_repr(httpx_mock) -> None:
    # `str(PortfolioWindow.WEEK)` is "PortfolioWindow.WEEK"; the wire (and the
    # signed canonical query) must carry "week".
    httpx_mock.add_response(
        url=f"{_HISTORY_URL}?window=week&limit=100", method="GET", json=_HISTORY_BODY
    )
    with _authed() as client:
        history = client.fetch_portfolio_history(PortfolioWindow.WEEK, limit=100)

    req = httpx_mock.get_request()
    assert req.url.query.decode() == "window=week&limit=100"
    assert "PortfolioWindow" not in str(req.url)
    assert history.window == "week"
    assert history.cadence_ms == 3600000
    # Oldest first, exact decimals.
    assert [p.timestamp_ms for p in history.points] == [1758000000000, 1758003600000]
    assert history.points[0].equity == Decimal("10000.00")
    assert history.points[1].pnl == Decimal("250.50")
    assert history.points[1].volume == Decimal("50000.00")


def test_fetch_portfolio_history_accepts_plain_string_window(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_HISTORY_URL}?window=all", method="GET", json=_HISTORY_BODY)
    with _authed() as client:
        client.fetch_portfolio_history("all")
    assert httpx_mock.get_request().url.query.decode() == "window=all"


@pytest.mark.parametrize("window", ["hour", "DAY", "", "1d"])
def test_fetch_portfolio_history_rejects_bad_window_before_sending(httpx_mock, window) -> None:
    with _authed() as client, pytest.raises(ValueError, match="window must be one of"):
        client.fetch_portfolio_history(window)
    # No signed request was spent on a guaranteed 400.
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("limit", [0, -1, 367, True, 1.5])
def test_fetch_portfolio_history_rejects_bad_limit_before_sending(httpx_mock, limit) -> None:
    with _authed() as client, pytest.raises(ValueError, match="limit must be"):
        client.fetch_portfolio_history(limit=limit)
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("limit", [1, 366])
def test_fetch_portfolio_history_accepts_limit_range_bounds(httpx_mock, limit) -> None:
    httpx_mock.add_response(url=f"{_HISTORY_URL}?limit={limit}", method="GET", json=_HISTORY_BODY)
    with _authed() as client:
        client.fetch_portfolio_history(limit=limit)
    assert httpx_mock.get_request().url.query.decode() == f"limit={limit}"


def test_fetch_portfolio_history_null_window_decodes_to_empty_string(httpx_mock) -> None:
    httpx_mock.add_response(
        url=_HISTORY_URL,
        method="GET",
        json={"window": None, "cadence_ms": 300000, "points": []},
    )
    with _authed() as client:
        history = client.fetch_portfolio_history()
    assert history.window == ""


def test_fetch_portfolio_history_skips_malformed_points(httpx_mock) -> None:
    httpx_mock.add_response(
        url=_HISTORY_URL,
        method="GET",
        json={
            "window": "day",
            "cadence_ms": 300000,
            "points": [
                "bogus",
                {"timestamp_ms": 1, "equity": "1", "pnl": "0", "volume": "0"},
            ],
        },
    )
    with _authed() as client:
        history = client.fetch_portfolio_history()
    assert len(history.points) == 1
    assert history.points[0].equity == Decimal("1")


def test_fetch_portfolio_history_rejects_point_missing_a_required_field(httpx_mock) -> None:
    # A point without equity is a malformed payload, not a zero-equity sample.
    httpx_mock.add_response(
        url=_HISTORY_URL,
        method="GET",
        json={"window": "day", "cadence_ms": 300000, "points": [{"timestamp_ms": 1, "pnl": "0"}]},
    )
    with _authed() as client, pytest.raises(ValueError):
        client.fetch_portfolio_history()


def test_fetch_portfolio_history_rejects_missing_cadence(httpx_mock) -> None:
    # A zero cadence would divide by zero in caller arithmetic.
    httpx_mock.add_response(url=_HISTORY_URL, method="GET", json={"window": "day", "points": []})
    with _authed() as client, pytest.raises(ValueError):
        client.fetch_portfolio_history()


def test_fetch_portfolio_history_surfaces_invalid_window_error(httpx_mock) -> None:
    # A window this client does not know about but the server rejects: the
    # machine-readable code survives.
    httpx_mock.add_response(
        url=f"{_HISTORY_URL}?window=day",
        method="GET",
        status_code=400,
        json={"code": "invalid_window"},
    )
    with _authed() as client, pytest.raises(ApiError) as excinfo:
        client.fetch_portfolio_history(PortfolioWindow.DAY)
    assert excinfo.value.status == 400
    assert excinfo.value.code == "invalid_window"


# -- credentials -------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.fetch_account_state(),
        lambda c: c.fetch_account_fees(),
        lambda c: c.fetch_portfolio_history(),
    ],
)
def test_portfolio_reads_require_credentials(httpx_mock, call) -> None:
    from nexus_exchange import MissingCredentialsError

    with Client(Network.LOCAL) as client, pytest.raises(MissingCredentialsError):
        call(client)
    assert httpx_mock.get_requests() == []
