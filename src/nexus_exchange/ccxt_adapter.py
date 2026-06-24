"""A CCXT-compatible adapter over the Nexus Exchange SDK.

[CCXT](https://github.com/ccxt/ccxt) is the de-facto unified API for crypto
exchanges; the Python quant/retail stack (freqtrade, hummingbot, countless
bots) speaks it. This adapter exposes the Nexus Exchange under CCXT's unified
method names and return shapes so that code already written against CCXT can
talk to Nexus with minimal changes.

**First increment (this module):** ``describe()`` plus the public market-data
surface — :meth:`NexusExchange.fetch_markets`, :meth:`fetch_ticker`,
:meth:`fetch_order_book`, :meth:`fetch_ohlcv`, :meth:`fetch_trades`. Private /
trading methods (balances, orders, positions) are a deliberate follow-up.

**Why no ``ccxt`` dependency.** This adapter *follows CCXT's conventions* — the
unified field names, the ``[price, amount]`` order-book levels, the
``[ts, o, h, l, c, v]`` candles, the ``load_markets`` cache — but it does not
subclass ``ccxt.Exchange`` or import ``ccxt``. That keeps the SDK dependency
light and the adapter usable on its own. Returns are plain ``dict`` / ``list``
matching CCXT's structures, so they drop into CCXT-shaped code. Whether to
additionally ship a true ``ccxt.Exchange`` subclass (and take the ``ccxt``
dependency) is a product decision left open.

The exchange's REST API already emits CCXT-shaped market data (the ticker is
documented "CCXT-style", order books are bids-desc/asks-asc ``[price, amount]``
levels, candles are ``[ts, o, h, l, c, v]``), so most of the mapping is a thin,
faithful pass-through with normalization (string symbols, numeric coercion,
unified ``market``/``symbol`` shapes).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from .client import Client, Network

__all__ = ["NexusExchange"]

#: CCXT timeframe string → the exchange's ``timeframe`` query value. The API
#: supports exactly ``1s``/``1m``/``5m``/``1h`` (openapi ``candles.timeframe``
#: enum). Unsupported timeframes raise rather than silently mis-fetching.
TIMEFRAMES: dict[str, str] = {
    "1s": "1s",
    "1m": "1m",
    "5m": "5m",
    "1h": "1h",
}


def _to_float(value: Any) -> float | None:
    """Coerce a JSON value to ``float`` the way CCXT's ``safe_float`` does.

    ``None``/missing/empty-string become ``None``; anything else is parsed and
    returns ``None`` rather than raising on garbage.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class NexusExchange:
    """A CCXT-style facade over :class:`~nexus_exchange.Client`.

    Construct it like a CCXT exchange and call the unified methods::

        from nexus_exchange.ccxt_adapter import NexusExchange

        ex = NexusExchange()
        markets = ex.fetch_markets()
        ticker = ex.fetch_ticker("BTC-USDX-PERP")
        book = ex.fetch_order_book("BTC-USDX-PERP")

    Symbols are the exchange's market ids (e.g. ``BTC-USDX-PERP``) — Nexus uses
    a unified id, so there is no separate CCXT-symbol vs market-id translation
    in this increment.

    Usable as a context manager; ``close()`` releases the underlying HTTP
    client. Pass an existing :class:`~nexus_exchange.Client` to share transport.
    """

    def __init__(
        self,
        *,
        network: Network = Network.STABLE,
        base_url: str | None = None,
        client: Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or Client(network=network, base_url=base_url)
        #: CCXT ``markets`` cache, keyed by symbol; populated by
        #: :meth:`load_markets` / :meth:`fetch_markets`.
        self.markets: dict[str, dict[str, Any]] = {}
        #: CCXT ``symbols`` list; populated alongside :attr:`markets`.
        self.symbols: list[str] = []

    # -- lifecycle --------------------------------------------------------
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> NexusExchange:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- metadata ---------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """CCXT ``describe()`` — capabilities, timeframes, and metadata.

        Mirrors the dict a ``ccxt.Exchange`` subclass returns from
        ``describe()``: ``has`` flags advertise which unified methods work
        today (private/trading methods are ``False`` until the follow-up
        increment), and ``timeframes`` lists the OHLCV intervals the API serves.
        """
        return {
            "id": "nexus",
            "name": "Nexus Exchange",
            "countries": [],
            "urls": {
                "www": "https://exchange.nexus.xyz",
                "doc": "https://github.com/nexus-xyz/nexus-exchange-py",
            },
            "version": "v1",
            "pro": False,
            "has": {
                "publicAPI": True,
                "privateAPI": False,
                "CORS": None,
                "spot": False,
                "swap": True,
                "future": False,
                "fetchMarkets": True,
                "fetchTicker": True,
                "fetchTickers": True,
                "fetchOrderBook": True,
                "fetchOHLCV": True,
                "fetchTrades": True,
                # Private / trading — follow-up increment.
                "fetchBalance": False,
                "fetchPositions": False,
                "createOrder": False,
                "cancelOrder": False,
                "fetchMyTrades": False,
                "fetchOrders": False,
                "fetchOpenOrders": False,
            },
            "timeframes": dict(TIMEFRAMES),
        }

    # -- public market data ----------------------------------------------
    def load_markets(self, reload: bool = False) -> dict[str, dict[str, Any]]:
        """CCXT ``load_markets`` — fetch + cache markets, keyed by symbol.

        Returns the cache on subsequent calls unless ``reload=True``.
        """
        if self.markets and not reload:
            return self.markets
        markets = self.fetch_markets()
        self.markets = {m["symbol"]: m for m in markets}
        self.symbols = sorted(self.markets)
        return self.markets

    def fetch_markets(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """CCXT ``fetch_markets`` — list markets as unified market structures.

        Uses ``GET /markets`` (full market definitions incl. tick/lot sizes and
        margin rates), mapping each into a CCXT market dict.
        """
        rows = self._client._request("GET", "/markets")
        if not isinstance(rows, list):
            rows = rows.get("markets", []) if isinstance(rows, dict) else []
        return [self._parse_market(row) for row in rows]

    def fetch_ticker(self, symbol: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """CCXT ``fetch_ticker`` — latest ticker for one market.

        The API already returns a CCXT-style ticker (``GET
        /markets/{symbol}/ticker``); this normalizes numerics and guarantees the
        unified keys are present.
        """
        data = self._client._request("GET", f"/markets/{quote(symbol, safe='')}/ticker")
        return self._parse_ticker(symbol, data if isinstance(data, dict) else {})

    def fetch_tickers(
        self, symbols: list[str] | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        """CCXT ``fetch_tickers`` — tickers for all (or a subset of) markets.

        ``GET /tickers`` returns an object keyed by market id; this maps each to
        a unified ticker. ``symbols`` filters the result client-side.
        """
        data = self._client._request("GET", "/tickers")
        if not isinstance(data, dict):
            return {}
        wanted = set(symbols) if symbols else None
        out: dict[str, dict[str, Any]] = {}
        for sym, raw in data.items():
            if wanted is not None and sym not in wanted:
                continue
            if isinstance(raw, dict):
                out[sym] = self._parse_ticker(sym, raw)
        return out

    def fetch_order_book(
        self, symbol: str, limit: int | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """CCXT ``fetch_order_book`` — order-book snapshot.

        ``GET /markets/{symbol}/orderbook``. Levels are ``[price, amount]`` with
        bids descending and asks ascending, matching CCXT. ``limit`` truncates
        each side client-side (the endpoint returns a full snapshot).
        """
        data = self._client._request("GET", f"/markets/{quote(symbol, safe='')}/orderbook")
        data = data if isinstance(data, dict) else {}
        bids = self._parse_levels(data.get("bids"))
        asks = self._parse_levels(data.get("asks"))
        if limit is not None:
            bids = bids[:limit]
            asks = asks[:limit]
        return {
            "symbol": data.get("symbol", symbol),
            "bids": bids,
            "asks": asks,
            "timestamp": data.get("timestamp"),
            "datetime": data.get("datetime"),
            "nonce": data.get("nonce"),
        }

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        """CCXT ``fetch_ohlcv`` — candles as ``[ts_ms, o, h, l, c, v]`` rows.

        ``GET /markets/{symbol}/candles?timeframe=&limit=``. ``timeframe`` must
        be a key of :data:`TIMEFRAMES` (``1s``/``1m``/``5m``/``1h``); an
        unsupported value raises rather than silently mis-fetching. ``since`` is
        not a server parameter — when given, rows older than ``since`` are
        filtered client-side, per CCXT semantics.
        """
        tf = TIMEFRAMES.get(timeframe)
        if tf is None:
            raise ValueError(
                f"timeframe {timeframe!r} not supported; choose one of {sorted(TIMEFRAMES)}"
            )
        query: dict[str, Any] = {"timeframe": tf}
        if limit is not None:
            query["limit"] = limit
        rows = self._client._request(
            "GET",
            f"/markets/{quote(symbol, safe='')}/candles",
            query=urlencode(query),
        )
        if not isinstance(rows, list):
            return []
        parsed = (self._parse_ohlcv(row) for row in rows)
        result = [c for c in parsed if c is not None]
        if since is not None:
            result = [c for c in result if c[0] >= since]
        return result

    def fetch_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """CCXT ``fetch_trades`` — recent public trades (newest first).

        ``GET /markets/{symbol}/trades?limit=``. ``since`` is filtered
        client-side, per CCXT semantics.
        """
        query: dict[str, Any] = {}
        if limit is not None:
            query["limit"] = limit
        rows = self._client._request(
            "GET",
            f"/markets/{quote(symbol, safe='')}/trades",
            query=urlencode(query) if query else "",
        )
        if not isinstance(rows, list):
            return []
        trades = [self._parse_trade(symbol, row) for row in rows if isinstance(row, dict)]
        if since is not None:
            trades = [t for t in trades if (t["timestamp"] or 0) >= since]
        return trades

    # -- parsers (raw → CCXT unified) -------------------------------------
    def _parse_market(self, m: dict[str, Any]) -> dict[str, Any]:
        symbol = str(m.get("market_id", ""))
        base = m.get("base_asset")
        quote_asset = m.get("quote_asset")
        return {
            "id": symbol,
            "symbol": symbol,
            "base": base,
            "quote": quote_asset,
            "settle": quote_asset,
            "baseId": base,
            "quoteId": quote_asset,
            "type": "swap",
            "spot": False,
            "swap": True,
            "future": False,
            "option": False,
            "contract": True,
            "linear": True,
            "inverse": False,
            "active": True,
            "precision": {
                "price": _to_float(m.get("tick_size")),
                "amount": _to_float(m.get("lot_size")),
            },
            "limits": {
                "amount": {
                    "min": _to_float(m.get("min_order_size")),
                    "max": _to_float(m.get("max_order_size")),
                },
                "leverage": {"min": 1.0, "max": _to_float(m.get("max_leverage"))},
            },
            "info": m,
        }

    def _parse_ticker(self, symbol: str, t: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": t.get("symbol", symbol),
            "timestamp": t.get("timestamp"),
            "datetime": t.get("datetime"),
            "high": _to_float(t.get("high")),
            "low": _to_float(t.get("low")),
            "bid": _to_float(t.get("bid")),
            "bidVolume": _to_float(t.get("bidVolume")),
            "ask": _to_float(t.get("ask")),
            "askVolume": _to_float(t.get("askVolume")),
            "vwap": None,
            "open": _to_float(t.get("open")),
            "close": _to_float(t.get("close")),
            "last": _to_float(t.get("last")),
            "previousClose": None,
            "change": _to_float(t.get("change")),
            "percentage": _to_float(t.get("percentage")),
            "average": None,
            "baseVolume": _to_float(t.get("baseVolume")),
            "quoteVolume": _to_float(t.get("quoteVolume")),
            "markPrice": _to_float(t.get("markPrice")),
            "indexPrice": _to_float(t.get("indexPrice")),
            "info": t.get("info", t),
        }

    def _parse_levels(self, levels: Any) -> list[list[float]]:
        out: list[list[float]] = []
        if not isinstance(levels, list):
            return out
        for lvl in levels:
            if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                price = _to_float(lvl[0])
                amount = _to_float(lvl[1])
                if price is not None and amount is not None:
                    out.append([price, amount])
        return out

    def _parse_ohlcv(self, row: Any) -> list[float] | None:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            return None
        ts = row[0]
        rest = [_to_float(row[i]) for i in range(1, 6)]
        if ts is None or any(v is None for v in rest):
            return None
        return [int(ts), *[v for v in rest if v is not None]]

    def _parse_trade(self, symbol: str, t: dict[str, Any]) -> dict[str, Any]:
        side = t.get("side")
        side = side.lower() if isinstance(side, str) else side
        return {
            "id": t.get("id"),
            "info": t.get("info", t),
            "timestamp": t.get("timestamp"),
            "datetime": t.get("datetime"),
            "symbol": t.get("symbol", symbol),
            "order": None,
            "type": None,
            "side": side,
            "takerOrMaker": t.get("takerOrMaker"),
            "price": _to_float(t.get("price")),
            "amount": _to_float(t.get("amount")),
            "cost": _to_float(t.get("cost")),
            "fee": None,
        }
