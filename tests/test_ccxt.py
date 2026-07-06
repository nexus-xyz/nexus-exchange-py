"""Tests for the CCXT-compatible market-data adapter.

These mock the raw exchange REST responses (the shapes documented in the
openapi spec / Rust SDK types) and assert the adapter emits CCXT unified
structures: market dicts, ``[price, amount]`` book levels, ``[ts, o, h, l, c,
v]`` candles, and unified ticker / trade dicts.
"""

from __future__ import annotations

import pytest

from nexus_exchange import Network
from nexus_exchange.ccxt_adapter import NexusExchange


def exchange() -> NexusExchange:
    return NexusExchange(network=Network.LOCAL)


# -- describe -------------------------------------------------------------


def test_describe_advertises_public_only() -> None:
    d = exchange().describe()
    assert d["id"] == "nexus"
    assert d["has"]["fetchOrderBook"] is True
    assert d["has"]["fetchOHLCV"] is True
    # Private/trading not in this increment.
    assert d["has"]["createOrder"] is False
    assert d["has"]["fetchBalance"] is False
    assert set(d["timeframes"]) == {"1s", "1m", "5m", "1h"}


# -- fetch_markets / load_markets -----------------------------------------


def test_fetch_markets_maps_unified_structure(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/markets",
        json=[
            {
                "market_id": "BTC-USDX-PERP",
                "base_asset": "BTC",
                "quote_asset": "USDX",
                "tick_size": "0.5",
                "lot_size": "0.001",
                "min_order_size": "0.001",
                "max_order_size": "100",
                "initial_margin_rate": "0.05",
                "maintenance_margin_rate": "0.03",
                "max_leverage": 20,
            }
        ],
    )
    with exchange() as ex:
        markets = ex.fetch_markets()

    assert len(markets) == 1
    m = markets[0]
    assert m["symbol"] == "BTC-USDX-PERP"
    assert m["base"] == "BTC"
    assert m["quote"] == "USDX"
    assert m["swap"] is True and m["spot"] is False and m["contract"] is True
    assert m["precision"]["price"] == 0.5
    assert m["precision"]["amount"] == 0.001
    assert m["limits"]["amount"]["min"] == 0.001
    assert m["limits"]["amount"]["max"] == 100.0
    assert m["limits"]["leverage"]["max"] == 20.0
    assert m["info"]["market_id"] == "BTC-USDX-PERP"


def test_load_markets_caches_by_symbol(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/markets",
        json=[{"market_id": "ETH-USDX-PERP", "base_asset": "ETH", "quote_asset": "USDX"}],
    )
    with exchange() as ex:
        first = ex.load_markets()
        # A second call must not hit the network (only one mocked response).
        second = ex.load_markets()
    assert first is second
    assert "ETH-USDX-PERP" in ex.markets
    assert ex.symbols == ["ETH-USDX-PERP"]


# -- fetch_ticker ---------------------------------------------------------


def test_fetch_ticker_normalizes(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/ticker",
        json={
            "symbol": "BTC-USDX-PERP",
            "timestamp": 1_776_000_000_000,
            "datetime": "2026-04-18T00:00:00Z",
            "bid": 65000.0,
            "ask": 65001.0,
            "last": 65000.5,
            "high": None,
            "baseVolume": 12.5,
            "markPrice": 65000.25,
            "info": {"raw": 1},
        },
    )
    with exchange() as ex:
        t = ex.fetch_ticker("BTC-USDX-PERP")
    assert t["symbol"] == "BTC-USDX-PERP"
    assert t["bid"] == 65000.0
    assert t["ask"] == 65001.0
    assert t["last"] == 65000.5
    assert t["high"] is None  # null passes through as None
    assert t["baseVolume"] == 12.5
    assert t["markPrice"] == 65000.25
    # CCXT unified keys present even when the API omits them.
    assert "vwap" in t and "previousClose" in t
    assert t["info"] == {"raw": 1}


# -- fetch_order_book -----------------------------------------------------


def test_fetch_order_book_levels_and_limit(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/orderbook",
        json={
            "symbol": "BTC-USDX-PERP",
            "bids": [[65000.0, 1.0], [64999.0, 2.0], [64998.0, 3.0]],
            "asks": [[65001.0, 1.5], [65002.0, 2.5]],
            "timestamp": 1_776_000_000_000,
            "datetime": "2026-04-18T00:00:00Z",
            "nonce": 42,
        },
    )
    with exchange() as ex:
        book = ex.fetch_order_book("BTC-USDX-PERP", limit=2)
    assert book["symbol"] == "BTC-USDX-PERP"
    assert book["bids"] == [[65000.0, 1.0], [64999.0, 2.0]]
    assert book["asks"] == [[65001.0, 1.5], [65002.0, 2.5]]
    assert book["nonce"] == 42
    # CCXT levels are plain [price, amount] float pairs.
    assert all(len(lvl) == 2 for lvl in book["bids"])


# -- fetch_ohlcv ----------------------------------------------------------


def test_fetch_ohlcv_rows_and_query(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/candles?timeframe=5m&limit=2",
        json=[
            [1_776_000_000_000, 100.0, 110.0, 95.0, 105.0, 1000.0],
            [1_776_000_300_000, 105.0, 115.0, 100.0, 112.0, 1200.0],
        ],
    )
    with exchange() as ex:
        candles = ex.fetch_ohlcv("BTC-USDX-PERP", timeframe="5m", limit=2)
    assert candles == [
        [1_776_000_000_000, 100.0, 110.0, 95.0, 105.0, 1000.0],
        [1_776_000_300_000, 105.0, 115.0, 100.0, 112.0, 1200.0],
    ]
    assert candles[0][0] == 1_776_000_000_000  # ts stays an int


def test_fetch_ohlcv_since_filters_client_side(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/candles?timeframe=1m",
        json=[
            [1_000, 1.0, 1.0, 1.0, 1.0, 1.0],
            [2_000, 2.0, 2.0, 2.0, 2.0, 2.0],
        ],
    )
    with exchange() as ex:
        candles = ex.fetch_ohlcv("BTC-USDX-PERP", since=1_500)
    assert [c[0] for c in candles] == [2_000]


def test_fetch_ohlcv_rejects_unknown_timeframe() -> None:
    with exchange() as ex:
        with pytest.raises(ValueError, match="not supported"):
            ex.fetch_ohlcv("BTC-USDX-PERP", timeframe="3d")


# -- fetch_trades ---------------------------------------------------------


def test_fetch_trades_maps_unified(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/trades?limit=2",
        json=[
            {
                "id": "t2",
                "symbol": "BTC-USDX-PERP",
                "price": 65000.0,
                "amount": 0.5,
                "cost": 32500.0,
                "side": "Buy",
                "timestamp": 2_000,
                "datetime": "2026-04-18T00:00:02Z",
                "takerOrMaker": "taker",
                "is_liquidation": False,
            },
            {
                "id": "t1",
                "symbol": "BTC-USDX-PERP",
                "price": 64999.0,
                "amount": 0.25,
                "cost": 16249.75,
                "side": "Sell",
                "timestamp": 1_000,
                "datetime": "2026-04-18T00:00:01Z",
                "takerOrMaker": "maker",
                "is_liquidation": False,
            },
        ],
    )
    with exchange() as ex:
        trades = ex.fetch_trades("BTC-USDX-PERP", limit=2)
    assert len(trades) == 2
    assert trades[0]["id"] == "t2"
    assert trades[0]["side"] == "buy"  # normalized to lowercase
    assert trades[1]["side"] == "sell"
    assert trades[0]["price"] == 65000.0
    assert trades[0]["amount"] == 0.5
    assert trades[0]["cost"] == 32500.0
    assert trades[0]["takerOrMaker"] == "taker"


def test_fetch_trades_since_filters(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/markets/BTC-USDX-PERP/trades",
        json=[
            {"id": "a", "side": "buy", "price": 1, "amount": 1, "cost": 1, "timestamp": 500},
            {"id": "b", "side": "buy", "price": 1, "amount": 1, "cost": 1, "timestamp": 1500},
        ],
    )
    with exchange() as ex:
        trades = ex.fetch_trades("BTC-USDX-PERP", since=1000)
    assert [t["id"] for t in trades] == ["b"]


# -- fetch_tickers --------------------------------------------------------


def test_fetch_tickers_keyed_and_filtered(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/tickers",
        json={
            "BTC-USDX-PERP": {"symbol": "BTC-USDX-PERP", "last": 65000.0},
            "ETH-USDX-PERP": {"symbol": "ETH-USDX-PERP", "last": 3200.0},
        },
    )
    with exchange() as ex:
        tickers = ex.fetch_tickers(symbols=["ETH-USDX-PERP"])
    assert list(tickers) == ["ETH-USDX-PERP"]
    assert tickers["ETH-USDX-PERP"]["last"] == 3200.0
