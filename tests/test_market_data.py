"""Unit tests for the public market-data endpoints (mocked httpx).

Mirrors the Rust SDK's ``tests/public.rs``: pins the wire shapes the typed
models decode (string vs number decimals, camelCase ticker keys, null handling,
the market-keyed ``/tickers`` map, array-shaped candles/levels) and the query
params the list endpoints send.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus_exchange import Client, MarkPrice, Network, Ticker


def test_fetch_market_summaries_handles_numbers_and_halted_null(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/summary",
        json=[
            {
                "market_id": "BTC-USDX-PERP",
                "last_trade_price": 50011.6,
                "volume_24h": 1350000.0,
                "trade_count": 982,
                "status": "active",
                "halt_reason": None,
                "halted_at": None,
                "adl_event_count": 0,
            },
            {
                "market_id": "DOGE-USDX-PERP",
                "last_trade_price": None,
                "volume_24h": 0.0,
                "trade_count": 0,
                "status": "halted",
                "halt_reason": "adl_pool_exhausted",
                "halted_at": 1776033900000,
                "adl_event_count": 3,
            },
        ],
    )
    with Client(Network.LOCAL) as client:
        summaries = client.fetch_market_summaries()
    assert summaries[0].last_trade_price is not None
    assert str(summaries[0].last_trade_price) == "50011.6"
    # A halted market sends a null price — must decode to None, not fail.
    assert summaries[1].last_trade_price is None
    assert summaries[1].status == "halted"
    assert summaries[1].halt_reason == "adl_pool_exhausted"
    assert summaries[1].halted_at == 1776033900000


def test_fetch_tickers_parses_market_keyed_map(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/tickers",
        json={
            "BTC-USDX-PERP": {
                "symbol": "BTC-USDX-PERP",
                "timestamp": 1776033900000,
                "datetime": "2026-04-13T00:00:00Z",
                "last": 51903.0,
                "markPrice": 50011.6,
            },
            "ETH-USDX-PERP": {
                "symbol": "ETH-USDX-PERP",
                "timestamp": 1776033900000,
                "datetime": "2026-04-13T00:00:00Z",
                "last": 3120.5,
            },
        },
    )
    with Client(Network.LOCAL) as client:
        tickers = client.fetch_tickers()
    assert set(tickers) == {"BTC-USDX-PERP", "ETH-USDX-PERP"}
    btc = tickers["BTC-USDX-PERP"]
    assert btc.last == Decimal("51903")
    assert btc.mark_price == Decimal("50011.6")


def test_fetch_tickers_empty_is_empty_map(httpx_mock) -> None:
    httpx_mock.add_response(url="http://localhost:9090/api/v1/tickers", json={})
    with Client(Network.LOCAL) as client:
        assert client.fetch_tickers() == {}


def test_fetch_ticker_parses_numbers_and_nulls(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/ticker",
        json={
            "symbol": "BTC-USDX-PERP",
            "timestamp": 1776033900000,
            "datetime": "2026-04-13T00:00:00Z",
            "bid": None,
            "ask": 50012.5,
            "last": 1.1,
            "info": {},
        },
    )
    with Client(Network.LOCAL) as client:
        ticker = client.fetch_ticker("BTC-USDX-PERP")
    assert ticker.bid is None
    assert ticker.ask is not None and str(ticker.ask) == "50012.5"
    # A non-f64-exact value (1.1) must round-trip exactly through Decimal(str()).
    assert ticker.last is not None and str(ticker.last) == "1.1"
    # Omitted fields default to None rather than failing the decode.
    assert ticker.high is None


def test_ticker_timestamp_and_datetime_are_nullable_when_omitted() -> None:
    # A market with no trades may omit timestamp/datetime; these must decode to
    # None rather than silently defaulting to 0/"" (so callers can tell the
    # difference between "no timestamp" and the epoch).
    ticker = Ticker.from_dict({"symbol": "BTC-USDX-PERP", "last": 1.1})
    assert ticker.timestamp is None
    assert ticker.datetime is None
    # An explicit null decodes to None too.
    ticker2 = Ticker.from_dict({"symbol": "BTC-USDX-PERP", "timestamp": None, "datetime": None})
    assert ticker2.timestamp is None
    assert ticker2.datetime is None
    # A present value still decodes normally.
    ticker3 = Ticker.from_dict(
        {"symbol": "BTC-USDX-PERP", "timestamp": 123, "datetime": "2026-04-13T00:00:00Z"}
    )
    assert ticker3.timestamp == 123
    assert ticker3.datetime == "2026-04-13T00:00:00Z"


def test_required_decimal_field_missing_raises() -> None:
    # A required money field (mark_price) absent from the payload must raise
    # rather than silently decoding to Decimal(0), which would mask a malformed
    # response.
    with pytest.raises(ValueError):
        MarkPrice.from_dict({"market_id": "BTC-USDX-PERP"})


def test_fetch_order_book_parses_levels(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/orderbook",
        json={
            "symbol": "BTC-USDX-PERP",
            "bids": [[50010.5, 1.2], [50010.0, 3.4]],
            "asks": [[50011.0, 0.5]],
            "timestamp": 1776033900000,
            "datetime": "2026-04-13T00:00:00Z",
            "nonce": 42,
        },
    )
    with Client(Network.LOCAL) as client:
        ob = client.fetch_order_book("BTC-USDX-PERP")
    assert len(ob.bids) == 2
    assert str(ob.bids[0].price) == "50010.5"
    assert str(ob.bids[0].amount) == "1.2"
    assert ob.nonce == 42


def test_fetch_trades_sends_limit_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/trades?limit=1",
        json=[
            {
                "id": "t1",
                "symbol": "BTC-USDX-PERP",
                "price": 50010.5,
                "amount": 0.1,
                "cost": 5001.05,
                "side": "buy",
                "timestamp": 1776033900000,
                "datetime": "2026-04-13T00:00:00Z",
                "takerOrMaker": "taker",
                "is_liquidation": False,
                "info": {},
            }
        ],
    )
    with Client(Network.LOCAL) as client:
        trades = client.fetch_trades("BTC-USDX-PERP", limit=1)
    assert len(trades) == 1
    assert trades[0].side == "buy"
    assert trades[0].taker_or_maker == "taker"


def test_fetch_ohlcv_parses_array_candles(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/candles?timeframe=1m&limit=1",
        json=[[1776033900000, 48062.0, 51903.0, 44992.0, 51903.0, 27.123]],
    )
    with Client(Network.LOCAL) as client:
        candles = client.fetch_ohlcv("BTC-USDX-PERP", timeframe="1m", limit=1)
    assert candles[0].timestamp == 1776033900000
    assert candles[0].close == Decimal("51903")
    assert candles[0].volume == Decimal("27.123")


def test_fetch_funding_rate_history_parses_string_decimals(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/funding",
        json=[
            {
                "timestamp": 1776033900000,
                "funding_rate": "0.0001",
                "premium_index": "0.00005",
                "mark_price": "50011.60",
                "oracle_price": "50010.00",
            }
        ],
    )
    with Client(Network.LOCAL) as client:
        samples = client.fetch_funding_rate_history("BTC-USDX-PERP")
    assert str(samples[0].funding_rate) == "0.0001"
    assert str(samples[0].mark_price) == "50011.60"


def test_fetch_mark_price_parses_string_decimal(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/mark-price",
        json={"market_id": "BTC-USDX-PERP", "mark_price": "50011.60"},
    )
    with Client(Network.LOCAL) as client:
        mp = client.fetch_mark_price("BTC-USDX-PERP")
    assert mp.market_id == "BTC-USDX-PERP"
    assert str(mp.mark_price) == "50011.60"


def test_fetch_market_status_parses_halt_fields(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/status",
        json={
            "market_id": "BTC-USDX-PERP",
            "status": "halted",
            "halt_reason": "adl_pool_exhausted",
            "halted_at": 1776033900000,
            "adl_event_count": 3,
        },
    )
    with Client(Network.LOCAL) as client:
        st = client.fetch_market_status("BTC-USDX-PERP")
    assert st.status == "halted"
    assert st.halt_reason == "adl_pool_exhausted"
    assert st.adl_event_count == 3


def test_fetch_market_adl_events_parses_nested_closures(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/markets/BTC-USDX-PERP/adl-events",
        json=[
            {
                "market_id": "BTC-USDX-PERP",
                "target_account": "0xabc",
                "bankruptcy_price": "49000.0",
                "bad_debt_absorbed_by_fund": "1200.5",
                "counterparty_closures": [
                    {
                        "account_id": "0xdef",
                        "position_closed": "0.5",
                        "settlement_amount": "300.25",
                    }
                ],
                "sequence": 42,
                "timestamp": 1776033960368,
            }
        ],
    )
    with Client(Network.LOCAL) as client:
        events = client.fetch_market_adl_events("BTC-USDX-PERP")
    assert events[0].sequence == 42
    assert len(events[0].counterparty_closures) == 1
    assert str(events[0].counterparty_closures[0].settlement_amount) == "300.25"


def test_fetch_account_adl_history_sends_limit(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/account/0xabc/adl-history?limit=5",
        json=[],
    )
    with Client(Network.LOCAL) as client:
        assert client.fetch_account_adl_history("0xabc", limit=5) == []


def test_health_check_parses_status(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/health",
        json={
            "events_received": 12345,
            "fills_total": 678,
            "uptime_seconds": 4242,
            "connected": True,
            "health": "healthy",
        },
    )
    with Client(Network.LOCAL) as client:
        health = client.health_check()
    assert health.connected is True
    assert health.events_received == 12345
    assert health.health == "healthy"
