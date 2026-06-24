"""Typed models for Nexus Exchange responses.

Mirrors the Rust SDK's wire types. Money is :class:`decimal.Decimal` (see
:mod:`nexus_exchange._parse` for how string- vs number-typed money is handled).
Models keep the full decoded payload on ``raw`` (or ``info`` for the CCXT-shaped
market-data types), so a field not yet surfaced as a typed attribute is still
reachable. Optional/nullable fields decode to ``None`` rather than failing, so a
slimmer or re-shaped payload still parses (forward-compatible).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ._parse import opt_decimal, opt_int, opt_str, to_decimal


@dataclass(frozen=True)
class Market:
    """A tradable market and its trading rules (``GET /markets``).

    ``raw`` holds the full entry. Trading-rule fields are exact decimal strings.
    """

    market_id: str
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    lot_size: Decimal
    min_order_size: Decimal
    max_order_size: Decimal
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    max_leverage: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Market:
        return cls(
            market_id=str(d.get("market_id", "")),
            base_asset=str(d.get("base_asset", "")),
            quote_asset=str(d.get("quote_asset", "")),
            tick_size=to_decimal(d.get("tick_size")),
            lot_size=to_decimal(d.get("lot_size")),
            min_order_size=to_decimal(d.get("min_order_size")),
            max_order_size=to_decimal(d.get("max_order_size")),
            initial_margin_rate=to_decimal(d.get("initial_margin_rate")),
            maintenance_margin_rate=to_decimal(d.get("maintenance_margin_rate")),
            max_leverage=int(d.get("max_leverage", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class MarketSummary:
    """Per-market summary with 24h volume and halt state (``GET /markets/summary``).

    ``last_trade_price`` and ``volume_24h`` arrive as JSON numbers (display
    values); ``last_trade_price`` is ``None`` for a halted market with no recent
    trade. As of API spec v0.4.0 the field is ``last_trade_price`` (the last
    trade price, not the engine-derived mark).
    """

    market_id: str
    last_trade_price: Decimal | None
    volume_24h: Decimal
    trade_count: int
    status: str
    halt_reason: str | None
    halted_at: int | None
    adl_event_count: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarketSummary:
        return cls(
            market_id=str(d.get("market_id", "")),
            last_trade_price=opt_decimal(d.get("last_trade_price")),
            volume_24h=to_decimal(d.get("volume_24h")),
            trade_count=int(d.get("trade_count", 0)),
            status=str(d.get("status", "")),
            halt_reason=d.get("halt_reason"),
            halted_at=d.get("halted_at"),
            adl_event_count=int(d.get("adl_event_count", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class MarketStatus:
    """Market lifecycle / halt status (``GET /markets/{id}/status``)."""

    market_id: str
    status: str
    halt_reason: str | None
    halted_at: int | None
    adl_event_count: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarketStatus:
        return cls(
            market_id=str(d.get("market_id", "")),
            status=str(d.get("status", "")),
            halt_reason=d.get("halt_reason"),
            halted_at=d.get("halted_at"),
            adl_event_count=int(d.get("adl_event_count", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class Ticker:
    """CCXT-style ticker for a market (``GET /markets/{id}/ticker``).

    Price/volume fields arrive as JSON numbers and are ``None`` when the API
    sends ``null`` (e.g. no trades yet). ``timestamp``/``datetime`` are likewise
    ``None`` when the venue omits them (matching CCXT, which leaves them unset
    on markets with no trades) rather than defaulting to ``0``/``""``. The full
    payload is kept on ``info``.
    """

    symbol: str
    timestamp: int | None
    datetime: str | None
    high: Decimal | None
    low: Decimal | None
    bid: Decimal | None
    bid_volume: Decimal | None
    ask: Decimal | None
    ask_volume: Decimal | None
    open: Decimal | None
    close: Decimal | None
    last: Decimal | None
    change: Decimal | None
    percentage: Decimal | None
    base_volume: Decimal | None
    quote_volume: Decimal | None
    mark_price: Decimal | None
    index_price: Decimal | None
    info: dict[str, Any]

    @property
    def market_id(self) -> str:
        """Alias for :attr:`symbol` — the market this ticker describes."""
        return self.symbol

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ticker:
        return cls(
            symbol=str(d.get("symbol", "")),
            timestamp=opt_int(d.get("timestamp")),
            datetime=opt_str(d.get("datetime")),
            high=opt_decimal(d.get("high")),
            low=opt_decimal(d.get("low")),
            bid=opt_decimal(d.get("bid")),
            bid_volume=opt_decimal(d.get("bidVolume")),
            ask=opt_decimal(d.get("ask")),
            ask_volume=opt_decimal(d.get("askVolume")),
            open=opt_decimal(d.get("open")),
            close=opt_decimal(d.get("close")),
            last=opt_decimal(d.get("last")),
            change=opt_decimal(d.get("change")),
            percentage=opt_decimal(d.get("percentage")),
            base_volume=opt_decimal(d.get("baseVolume")),
            quote_volume=opt_decimal(d.get("quoteVolume")),
            mark_price=opt_decimal(d.get("markPrice")),
            index_price=opt_decimal(d.get("indexPrice")),
            info=d,
        )


@dataclass(frozen=True)
class PriceLevel:
    """A single order-book level, ``[price, amount]`` (CCXT format)."""

    price: Decimal
    amount: Decimal

    @classmethod
    def from_pair(cls, pair: list[Any]) -> PriceLevel:
        return cls(price=to_decimal(pair[0]), amount=to_decimal(pair[1]))


@dataclass(frozen=True)
class OrderBook:
    """Order book snapshot. Bids descending, asks ascending (CCXT convention)."""

    symbol: str
    bids: list[PriceLevel]
    asks: list[PriceLevel]
    timestamp: int
    datetime: str
    nonce: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrderBook:
        return cls(
            symbol=str(d.get("symbol", "")),
            bids=[PriceLevel.from_pair(lvl) for lvl in d.get("bids", [])],
            asks=[PriceLevel.from_pair(lvl) for lvl in d.get("asks", [])],
            timestamp=int(d.get("timestamp", 0)),
            datetime=str(d.get("datetime", "")),
            nonce=int(d.get("nonce", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class Trade:
    """A public trade print (``GET /markets/{id}/trades``).

    ``price``/``amount``/``cost`` are JSON-number display values; for the exact
    record of your own executions use :class:`Fill`.
    """

    id: str
    symbol: str
    price: Decimal
    amount: Decimal
    cost: Decimal
    side: str
    timestamp: int
    datetime: str
    taker_or_maker: str | None
    is_liquidation: bool
    info: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Trade:
        return cls(
            id=str(d.get("id", "")),
            symbol=str(d.get("symbol", "")),
            price=to_decimal(d.get("price")),
            amount=to_decimal(d.get("amount")),
            cost=to_decimal(d.get("cost")),
            side=str(d.get("side", "")),
            timestamp=int(d.get("timestamp", 0)),
            datetime=str(d.get("datetime", "")),
            taker_or_maker=d.get("takerOrMaker"),
            is_liquidation=bool(d.get("is_liquidation", False)),
            info=d,
        )


@dataclass(frozen=True)
class Ohlcv:
    """An OHLCV candle, ``[timestamp_ms, open, high, low, close, volume]`` (CCXT)."""

    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def from_row(cls, row: list[Any]) -> Ohlcv:
        return cls(
            timestamp=int(row[0]),
            open=to_decimal(row[1]),
            high=to_decimal(row[2]),
            low=to_decimal(row[3]),
            close=to_decimal(row[4]),
            volume=to_decimal(row[5]),
        )


@dataclass(frozen=True)
class FundingSample:
    """One intra-hour funding-rate sample (``GET /markets/{id}/funding``).

    All fields are exact decimal strings.
    """

    timestamp: int
    funding_rate: Decimal
    premium_index: Decimal
    mark_price: Decimal
    oracle_price: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FundingSample:
        return cls(
            timestamp=int(d.get("timestamp", 0)),
            funding_rate=to_decimal(d.get("funding_rate")),
            premium_index=to_decimal(d.get("premium_index")),
            mark_price=to_decimal(d.get("mark_price")),
            oracle_price=to_decimal(d.get("oracle_price")),
            raw=d,
        )


@dataclass(frozen=True)
class MarkPrice:
    """Current mark price for a market (``GET /markets/{id}/mark-price``)."""

    market_id: str
    mark_price: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarkPrice:
        return cls(
            market_id=str(d.get("market_id", "")),
            mark_price=to_decimal(d.get("mark_price")),
            raw=d,
        )


@dataclass(frozen=True)
class AdlClosure:
    """One counterparty's forced closure within an ADL settlement."""

    account_id: str
    position_closed: Decimal
    settlement_amount: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AdlClosure:
        return cls(
            account_id=str(d.get("account_id", "")),
            position_closed=to_decimal(d.get("position_closed")),
            settlement_amount=to_decimal(d.get("settlement_amount")),
            raw=d,
        )


@dataclass(frozen=True)
class AdlEvent:
    """A single auto-deleveraging settlement event (v0.21).

    Returned by the market and account ADL history endpoints.
    """

    market_id: str
    target_account: str
    bankruptcy_price: Decimal
    bad_debt_absorbed_by_fund: Decimal
    counterparty_closures: list[AdlClosure]
    sequence: int
    timestamp: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AdlEvent:
        return cls(
            market_id=str(d.get("market_id", "")),
            target_account=str(d.get("target_account", "")),
            bankruptcy_price=to_decimal(d.get("bankruptcy_price")),
            bad_debt_absorbed_by_fund=to_decimal(d.get("bad_debt_absorbed_by_fund")),
            counterparty_closures=[
                AdlClosure.from_dict(c) for c in d.get("counterparty_closures", [])
            ],
            sequence=int(d.get("sequence", 0)),
            timestamp=int(d.get("timestamp", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class HealthStatus:
    """Indexer health/status snapshot (``GET /health``). Unauthenticated.

    Unknown fields are ignored and kept on ``raw``, so this stays
    forward-compatible as the snapshot grows.
    """

    events_received: int
    fills_total: int
    uptime_seconds: int
    connected: bool
    health: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HealthStatus:
        return cls(
            events_received=int(d.get("events_received", 0)),
            fills_total=int(d.get("fills_total", 0)),
            uptime_seconds=int(d.get("uptime_seconds", 0)),
            connected=bool(d.get("connected", False)),
            health=d.get("health"),
            raw=d,
        )
