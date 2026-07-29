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
from enum import Enum
from typing import Any

from ._parse import opt_decimal, opt_int, opt_str, to_decimal, to_dict_list, to_int, to_str
from .errors import DecodeError


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


# -- account & trading models ---------------------------------------------


@dataclass(frozen=True)
class Position:
    """An open position with per-position risk detail.

    All money fields are exact decimal strings.

    The enriched risk fields — :attr:`leverage`, :attr:`notional_value`,
    :attr:`roe`, :attr:`margin_used`, :attr:`max_leverage`,
    :attr:`funding_paid` — arrived in API spec v0.7.2 (ENG-6445) and are derived
    strictly from indexer-mirrored state (no engine round-trip, to stay on the
    low-latency read path). When an input is not mirrored the server sends
    ``null`` and puts a machine-readable reason in the companion
    ``<field>_error`` (e.g. ``"mark_price_unavailable"``) rather than a
    fabricated number. Five of the six have such a companion; the spec defines
    no ``funding_paid_error``, so a ``None`` :attr:`funding_paid` carries no
    reason.

    This decode preserves that distinction: each enriched field is ``None``
    both when the server reports it ``null`` *and* when a deployment older than
    v0.7.2 omits it entirely — never a defaulted ``0``, which would read as a
    real "no leverage / no notional / no funding paid". Check the matching
    ``*_error`` to tell a server-reported gap from an old deployment (the error
    is ``None`` in both the populated and the omitted case).

    :attr:`leverage` is currently always ``None`` server-side with
    ``leverage_error == "margin_state_not_mirrored"``. Do not infer it from
    :attr:`margin_used` — that collapses to ``1 / initial_margin_rate``, a
    per-market constant, not the real leverage.

    :attr:`funding_paid` is **paid-positive**: positive means this position has
    paid funding, negative means it has received funding.
    """

    market_id: str
    side: str
    size: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    liquidation_price: Decimal | None
    raw: dict[str, Any]
    # Enriched risk detail (spec v0.7.2). Declared after `raw` with defaults so
    # this stays a backwards-compatible extension of the previous field order.
    # `leverage` is a JSON *number* on the wire (the rest are decimal strings);
    # it decodes through `str` so no float re-rendering creeps in.
    leverage: Decimal | None = None
    leverage_error: str | None = None
    notional_value: Decimal | None = None
    notional_value_error: str | None = None
    roe: Decimal | None = None
    roe_error: str | None = None
    margin_used: Decimal | None = None
    margin_used_error: str | None = None
    max_leverage: int | None = None
    max_leverage_error: str | None = None
    funding_paid: Decimal | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Position:
        return cls(
            market_id=str(d.get("market_id", "")),
            side=str(d.get("side", "")),
            size=to_decimal(d.get("size", 0)),
            entry_price=to_decimal(d.get("entry_price", 0)),
            unrealized_pnl=to_decimal(d.get("unrealized_pnl", 0)),
            realized_pnl=to_decimal(d.get("realized_pnl", 0)),
            # Not `required` in the spec — absent in flat / cross-margin states.
            liquidation_price=opt_decimal(d.get("liquidation_price")),
            raw=d,
            leverage=opt_decimal(d.get("leverage")),
            leverage_error=opt_str(d.get("leverage_error")),
            notional_value=opt_decimal(d.get("notional_value")),
            notional_value_error=opt_str(d.get("notional_value_error")),
            roe=opt_decimal(d.get("roe")),
            roe_error=opt_str(d.get("roe_error")),
            margin_used=opt_decimal(d.get("margin_used")),
            margin_used_error=opt_str(d.get("margin_used_error")),
            max_leverage=opt_int(d.get("max_leverage")),
            max_leverage_error=opt_str(d.get("max_leverage_error")),
            funding_paid=opt_decimal(d.get("funding_paid")),
        )


@dataclass(frozen=True)
class ClosedPosition:
    """A position that has been closed (``GET /positions/closed``, spec v0.7.2).

    The realized counterpart of :class:`Position`: the size, entry and exit
    prices at close, plus the PnL the close realized. All money fields are exact
    decimal strings.

    :attr:`side` is the side the position held **before** it closed (``"Long"`` /
    ``"Short"`` — note the capitalization differs from the ``"buy"`` / ``"sell"``
    used on orders and fills), and :attr:`size` is its absolute size at close.

    The spec marks no field of this schema ``required``, so each decodes
    leniently, matching :class:`Position` and :class:`Fill`. The full payload
    stays on :attr:`raw`, which is how to tell an absent field from a real zero.
    """

    market_id: str
    side: str
    size: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    closed_at_ms: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClosedPosition:
        return cls(
            market_id=str(d.get("market_id", "")),
            side=str(d.get("side", "")),
            size=to_decimal(d.get("size", 0)),
            entry_price=to_decimal(d.get("entry_price", 0)),
            exit_price=to_decimal(d.get("exit_price", 0)),
            realized_pnl=to_decimal(d.get("realized_pnl", 0)),
            closed_at_ms=int(d.get("closed_at_ms", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class AccountSummary:
    """Account balance and collateral summary (``GET /account``).

    Distinct from :class:`AccountPortfolioSummary`, which is the aggregate
    portfolio view (equity, PnL, volume, ``withdrawable``) served by
    ``/account/summary`` and embedded in ``/account/state``.
    """

    balance: Decimal
    collateral: Decimal
    equity: Decimal
    available_margin: Decimal
    positions: list[Position]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AccountSummary:
        return cls(
            balance=to_decimal(d.get("balance", 0), "balance"),
            collateral=to_decimal(d.get("collateral", 0), "collateral"),
            equity=to_decimal(d.get("equity", 0), "equity"),
            available_margin=to_decimal(d.get("available_margin", 0), "available_margin"),
            # Absent stays tolerated here (this endpoint predates the strict
            # rule), but a malformed *element* raises rather than vanishing.
            positions=[
                Position.from_dict(p)
                for p in to_dict_list(d.get("positions"), "positions", required=False)
            ],
            raw=d,
        )


@dataclass(frozen=True)
class AccountPortfolioSummary:
    """Aggregate portfolio view for the authenticated account (spec v0.7.2).

    The ``summary`` half of ``GET /account/state`` — identical to the standalone
    ``/account/summary`` response. Distinct from :class:`AccountSummary`, the
    balance/collateral view of ``GET /account``.

    The spec marks *every* field optional, and a deployment older than v0.7.2
    does not report :attr:`withdrawable` at all, so each figure decodes to
    ``None`` when absent rather than to ``Decimal(0)``: a fabricated zero
    equity or zero withdrawable balance reads as a real (and materially wrong)
    number, while ``None`` says plainly "this deployment did not report it".

    :attr:`withdrawable` is the wallet-withdrawable balance — engine-authoritative
    free margin floored at zero, already net of each position's initial margin
    and pre-trade order reservations. It is never negative when present: an
    underwater account is clamped to ``0``. The endpoints serving it fail closed
    with HTTP 502 (``authoritative_margin_unavailable``, raised as
    :class:`~nexus_exchange.ApiError`) rather than reporting a local estimate,
    so a populated value is authoritative.

    :attr:`early_access_allowed` is present only while the early-access gate is
    active; ``None`` means the gate is not in play, not "denied".
    """

    collateral: Decimal | None
    total_equity: Decimal | None
    total_unrealized_pnl: Decimal | None
    total_realized_pnl_24h: Decimal | None
    total_volume_24h: Decimal | None
    open_positions_count: int | None
    open_orders_count: int | None
    margin_used: Decimal | None
    available_margin: Decimal | None
    withdrawable: Decimal | None
    early_access_allowed: bool | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AccountPortfolioSummary:
        early_access = d.get("early_access_allowed")
        return cls(
            collateral=opt_decimal(d.get("collateral")),
            total_equity=opt_decimal(d.get("total_equity")),
            total_unrealized_pnl=opt_decimal(d.get("total_unrealized_pnl")),
            total_realized_pnl_24h=opt_decimal(d.get("total_realized_pnl_24h")),
            total_volume_24h=opt_decimal(d.get("total_volume_24h")),
            open_positions_count=opt_int(d.get("open_positions_count")),
            open_orders_count=opt_int(d.get("open_orders_count")),
            margin_used=opt_decimal(d.get("margin_used")),
            available_margin=opt_decimal(d.get("available_margin")),
            withdrawable=opt_decimal(d.get("withdrawable")),
            early_access_allowed=None if early_access is None else bool(early_access),
            raw=d,
        )


@dataclass(frozen=True)
class AccountState:
    """Consolidated account snapshot (``GET /account/state``, spec v0.7.2).

    One call for what used to take two (``/account/summary`` + ``/positions``),
    matching Hyperliquid ``clearinghouseState`` ergonomics. Both halves come
    from a single coherent read, so the server guarantees
    ``summary.open_positions_count == len(positions)``. That invariant is
    documented, not enforced here — a mismatch would mean a server bug, and
    both halves are returned as received so a caller can see it rather than
    have the SDK raise on live data.

    Both halves are spec-``required`` and decode strictly: a payload missing
    ``summary`` or ``positions``, or carrying a non-object in the position list,
    raises :class:`~nexus_exchange.DecodeError` rather than yielding an empty
    risk snapshot. Silently reporting "no open positions" understates exposure
    just as badly as a fabricated zero — and unlike the enriched
    :class:`Position` fields there is no old-deployment case to tolerate, since a
    deployment without this endpoint answers ``404``. The summary's individual
    *fields* stay all-optional, per the spec.
    """

    summary: AccountPortfolioSummary
    positions: list[Position]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AccountState:
        summary = d.get("summary")
        if not isinstance(summary, dict):
            raise DecodeError(
                f"required object field 'summary' is missing or not an object: {summary!r}"
            )
        return cls(
            summary=AccountPortfolioSummary.from_dict(summary),
            positions=[
                Position.from_dict(p) for p in to_dict_list(d.get("positions"), "positions")
            ],
            raw=d,
        )


class PortfolioWindow(str, Enum):
    """Window selector for :meth:`~nexus_exchange.Client.fetch_portfolio_history`.

    Also selects the server-side downsample cadence and point capacity:

    ==========  =======  ==========  ======
    window      cadence  max points  span
    ==========  =======  ==========  ======
    ``day``     5 min    288         24 h
    ``week``    1 h      168         7 d
    ``month``   6 h      120         30 d
    ``all``     1 d      366         ~1 y
    ==========  =======  ==========  ======

    A value outside this set is rejected by the server with ``400``
    (``invalid_window``); the client rejects it before sending. Pass a member or
    its plain string value — note ``str()`` on a ``str``-mixin enum member
    renders ``"PortfolioWindow.DAY"``, so the wire value is always taken from
    ``.value``.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    ALL = "all"


@dataclass(frozen=True)
class PortfolioPoint:
    """One downsampled portfolio sample (spec v0.7.2).

    Every field is spec-``required``, so a sample missing one raises
    :class:`~nexus_exchange.DecodeError` rather than decoding to a nonsense
    zero — an absent equity or timestamp means a malformed payload, not a real
    value.

    :attr:`equity` is collateral + Σ unrealized PnL. :attr:`pnl` is *cumulative
    trading* PnL (Σ realized on close, including liquidation and ADL closes, +
    Σ signed funding + current unrealized) and is deposit-neutral: wallet
    deposits and withdrawals never move it. :attr:`volume` is cumulative traded
    notional and is monotonically non-decreasing.
    """

    timestamp_ms: int
    equity: Decimal
    pnl: Decimal
    volume: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PortfolioPoint:
        return cls(
            timestamp_ms=to_int(d.get("timestamp_ms"), "timestamp_ms"),
            equity=to_decimal(d.get("equity"), "equity"),
            pnl=to_decimal(d.get("pnl"), "pnl"),
            volume=to_decimal(d.get("volume"), "volume"),
            raw=d,
        )


@dataclass(frozen=True)
class PortfolioHistory:
    """Portfolio time-series for the account (``GET /account/portfolio-history``).

    Equity, cumulative trading PnL, and cumulative traded volume over the
    requested window, downsampled at a fixed per-window cadence,
    :attr:`points` **oldest first**.

    All three fields are spec-``required``, and all three decode strictly: an
    absent or ``null`` :attr:`window`, :attr:`cadence_ms` or :attr:`points`
    raises :class:`~nexus_exchange.DecodeError`, as does a non-object element in
    :attr:`points`. A missing cadence must not yield ``0`` (it would divide by
    zero in caller arithmetic), and a dropped sample must not shorten the series
    — the cadence between adjacent points is part of the contract, so a hole
    silently bends every curve and delta derived from it.

    :attr:`window` echoes the served window (the ``window`` query parameter or
    its ``day`` default). It is typed as a plain ``str``, not
    :class:`PortfolioWindow`, so a window added upstream later still decodes
    instead of failing the whole response; compare against
    :class:`PortfolioWindow` values, e.g.
    ``history.window == PortfolioWindow.WEEK.value``.
    """

    window: str
    cadence_ms: int
    points: list[PortfolioPoint]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PortfolioHistory:
        return cls(
            window=to_str(d.get("window"), "window"),
            cadence_ms=to_int(d.get("cadence_ms"), "cadence_ms"),
            points=[PortfolioPoint.from_dict(p) for p in to_dict_list(d.get("points"), "points")],
            raw=d,
        )


@dataclass(frozen=True)
class EquityPoint:
    """One equity sample (``GET /account/equity-history``, spec v0.7.2).

    A 5s-cadence, ~1h window of account equity, **oldest first** — the
    high-resolution recent view, where :class:`PortfolioHistory` is the
    downsampled long-window one.

    Note the wire types differ between the two: :attr:`equity` here is a JSON
    *number*, while :attr:`PortfolioPoint.equity` is a decimal *string*. It is
    decoded through ``str`` so the value matches the JSON text that arrived
    rather than a float re-rendering, but it is still a number field on the wire
    — prefer the string-typed sources for anything authoritative.

    The spec marks neither field ``required``, so both decode leniently (matching
    :class:`Fill` and :class:`Position`, and unlike the strict
    :class:`PortfolioPoint`, whose fields the spec does mark required). The full
    payload stays on :attr:`raw` to tell an absent sample field from a real zero.
    """

    timestamp_ms: int
    equity: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EquityPoint:
        return cls(
            timestamp_ms=int(d.get("timestamp_ms", 0)),
            equity=to_decimal(d.get("equity", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class AccountFees:
    """The account's effective fee schedule (``GET /account/fees``, spec v0.7.2).

    The forward-looking *schedule* rate scoped by :attr:`schedule`, not a
    realized per-fill average. Rates are spec-``required`` and decode strictly:
    a payload missing one raises :class:`~nexus_exchange.DecodeError` rather
    than reporting a fabricated ``0`` bps, which would read as "trading is
    free".

    :attr:`maker_fee_bps` may be **negative** — that is a rebate *paid to* the
    maker (``-2`` is a 0.02% rebate), and the sign is preserved as sent.

    :attr:`tier` (currently always ``"base"``) and :attr:`schedule` (currently
    always ``"standard"``) are open strings — typed ``str`` rather than an enum
    so a value added as the fee model lands still decodes — so branch on them
    defensively. Both are spec-``required`` and decode strictly: an absent one
    raises rather than becoming ``""``, which is a value no defensive branch
    will match and which cannot be told from a server that really sent ``""``.
    :attr:`schedule` scopes the rate — the venue charges per-market schedules but
    this endpoint takes no market parameter, so the reported rate is not a
    venue-wide guarantee.

    :attr:`volume_30d_estimated` is ``True`` when :attr:`volume_30d` may
    undercount (the source fill buffer was at capacity, so older in-window fills
    may have been evicted). It is the one deliberate exception to the strict
    decode above: spec-``required``, but it defaults to ``True`` when absent
    rather than raising, because the safe direction is to assume the figure may
    undercount. Asserting full 30-day coverage the server never claimed is the
    harmful reading, and there is no third state for a ``bool``.

    :attr:`discounts` is currently always empty and its per-entry shape is
    provisional in the spec (``additionalProperties``), so entries stay raw
    dicts rather than a typed model that would have to break to grow. Non-object
    entries are skipped rather than raising — the shape is not yet pinned down,
    and unlike a time series or a position list a dropped discount cannot
    silently distort a figure the caller computes. An absent, ``null`` or
    non-array ``discounts`` decodes to ``[]`` for the same reason.
    """

    maker_fee_bps: int
    taker_fee_bps: int
    tier: str
    schedule: str
    volume_30d: Decimal
    volume_30d_estimated: bool
    discounts: list[dict[str, Any]]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AccountFees:
        estimated = d.get("volume_30d_estimated")
        # Not `d.get("discounts", [])`: a `null` array would then reach the
        # comprehension and raise `TypeError` from library internals — outside
        # the error taxonomy, and the one failure mode this model is explicitly
        # lenient about.
        raw_discounts = d.get("discounts")
        return cls(
            maker_fee_bps=to_int(d.get("maker_fee_bps"), "maker_fee_bps"),
            taker_fee_bps=to_int(d.get("taker_fee_bps"), "taker_fee_bps"),
            tier=to_str(d.get("tier"), "tier"),
            schedule=to_str(d.get("schedule"), "schedule"),
            volume_30d=to_decimal(d.get("volume_30d"), "volume_30d"),
            # Absent *or* null → assume the figure may undercount (see the class
            # docstring); only an explicit false claims full 30-day coverage.
            volume_30d_estimated=True if estimated is None else bool(estimated),
            discounts=[x for x in raw_discounts if isinstance(x, dict)]
            if isinstance(raw_discounts, list)
            else [],
            raw=d,
        )


@dataclass(frozen=True)
class Fill:
    """A fill (private trade execution) for the authenticated account.

    Figures are exact decimal strings — the authoritative record of your own
    executions, unlike the JSON-number :class:`Trade`.
    """

    id: str
    order_id: str
    market_id: str
    side: str
    price: Decimal
    size: Decimal
    fee: Decimal
    taker_or_maker: str | None
    timestamp: int
    is_liquidation: bool
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fill:
        return cls(
            id=str(d.get("id", "")),
            order_id=str(d.get("order_id", "")),
            market_id=str(d.get("market_id", "")),
            side=str(d.get("side", "")),
            price=to_decimal(d.get("price", 0)),
            size=to_decimal(d.get("size", 0)),
            fee=to_decimal(d.get("fee", 0)),
            taker_or_maker=d.get("taker_or_maker"),
            timestamp=int(d.get("timestamp", 0)),
            is_liquidation=bool(d.get("is_liquidation", False)),
            raw=d,
        )


@dataclass(frozen=True)
class Order:
    """An order record. The spec marks every non-identity field optional, so
    those default rather than fail the decode when omitted."""

    id: str
    market_id: str
    account_id: str
    side: str
    order_type: str
    price: Decimal | None
    quantity: Decimal
    filled_qty: Decimal
    status: str
    time_in_force: str
    created_at: int
    updated_at: int
    raw: dict[str, Any]
    limit_offset_bps: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Order:
        return cls(
            id=str(d.get("id", "")),
            market_id=str(d.get("market_id", "")),
            account_id=str(d.get("account_id", "")),
            side=str(d.get("side", "")),
            order_type=str(d.get("order_type", "")),
            price=opt_decimal(d.get("price")),
            quantity=to_decimal(d.get("quantity", 0)),
            filled_qty=to_decimal(d.get("filled_qty", 0)),
            status=str(d.get("status", "")),
            time_in_force=str(d.get("time_in_force", "")),
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
            raw=d,
            limit_offset_bps=opt_int(d.get("limit_offset_bps")),
        )


@dataclass(frozen=True)
class OrderHistoryEntry:
    """A terminal-status order (``GET /orders/history``, spec v0.7.2).

    Orders that have reached ``Filled`` / ``Cancelled`` / ``Rejected`` /
    ``Expired``, newest first. Distinct from :class:`Order` (``GET /orders``,
    which lists *open* orders): the history entry drops the live bookkeeping
    fields and adds :attr:`completed_at_ms` and :attr:`cancellation_reason`.

    :attr:`price` is ``None`` for market orders — the spec types it nullable, so
    it decodes to ``None`` rather than a fabricated ``0`` that would read as a
    real price of zero. :attr:`size` is the *original* quantity, not the
    remaining one; compare it against :attr:`filled_qty` to see how much of a
    cancelled order had executed.

    :attr:`status` is typed as a plain ``str`` rather than an enum so a status
    added upstream later still decodes.

    The spec marks no field of this schema ``required``, so the rest decode
    leniently, matching :class:`Order`. The full payload stays on :attr:`raw`.
    """

    id: str
    market_id: str
    side: str
    order_type: str
    price: Decimal | None
    size: Decimal
    filled_qty: Decimal
    status: str
    cancellation_reason: str | None
    created_at_ms: int
    completed_at_ms: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrderHistoryEntry:
        return cls(
            id=str(d.get("id", "")),
            market_id=str(d.get("market_id", "")),
            side=str(d.get("side", "")),
            order_type=str(d.get("order_type", "")),
            # Nullable in the spec: market orders carry no limit price.
            price=opt_decimal(d.get("price")),
            size=to_decimal(d.get("size", 0)),
            filled_qty=to_decimal(d.get("filled_qty", 0)),
            status=str(d.get("status", "")),
            cancellation_reason=opt_str(d.get("cancellation_reason")),
            created_at_ms=int(d.get("created_at_ms", 0)),
            completed_at_ms=int(d.get("completed_at_ms", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class OrderResponse:
    """Response to ``POST /orders``: the resulting order plus immediate fills.

    ``fills`` is typed as :class:`Fill` (the spec types the fill shape as of
    v0.5.0); the full decoded response stays on :attr:`raw`.
    """

    order: Order
    fills: list[Fill]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrderResponse:
        return cls(
            order=Order.from_dict(d.get("order", {})),
            fills=[Fill.from_dict(f) for f in d.get("fills", []) if isinstance(f, dict)],
            raw=d,
        )


@dataclass(frozen=True)
class OrderRequest:
    """A new-order request (``POST /orders``).

    Build with :meth:`limit`, :meth:`market`, or :meth:`trailing_limit`.
    ``price`` / ``reduce_only`` / ``trailing_offset_bps`` / ``limit_offset_bps``
    are omitted from the wire payload when ``None``.

    ``time_in_force`` is sent verbatim and the engine is case-sensitive:
    ``"GTC"``, ``"IOC"``, ``"FOK"`` (uppercase) or ``"PostOnly"`` (PascalCase —
    ``"POSTONLY"`` is rejected). A post-only (add-liquidity-only) order is
    rejected if it would take liquidity (cross the book) on entry, guaranteeing
    it rests as a maker; a crossing post-only order is rejected server-side
    with the ``WouldTakeLiquidity`` error code.
    """

    market_id: str
    side: str
    order_type: str
    quantity: Decimal
    time_in_force: str
    price: Decimal | None = None
    reduce_only: bool | None = None
    trailing_offset_bps: int | None = None
    limit_offset_bps: int | None = None

    @classmethod
    def limit(
        cls,
        market_id: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
        time_in_force: str = "GTC",
        *,
        reduce_only: bool | None = None,
    ) -> OrderRequest:
        """A limit order. ``time_in_force`` accepts ``"GTC"`` (default),
        ``"IOC"``, ``"FOK"``, or ``"PostOnly"`` — see the class docstring for
        the exact wire values and post-only semantics."""
        return cls(
            market_id=market_id,
            side=side,
            order_type="Limit",
            quantity=quantity,
            time_in_force=time_in_force,
            price=price,
            reduce_only=reduce_only,
        )

    @classmethod
    def market(
        cls,
        market_id: str,
        side: str,
        quantity: Decimal,
        *,
        reduce_only: bool | None = None,
    ) -> OrderRequest:
        return cls(
            market_id=market_id,
            side=side,
            order_type="Market",
            quantity=quantity,
            time_in_force="IOC",
            price=None,
            reduce_only=reduce_only,
        )

    @classmethod
    def trailing_limit(
        cls,
        market_id: str,
        side: str,
        quantity: Decimal,
        trailing_offset_bps: int,
        limit_offset_bps: int,
        time_in_force: str = "GTC",
        *,
        reduce_only: bool | None = None,
    ) -> OrderRequest:
        """A trailing-limit order. Carries no ``price``: the limit price is
        computed server-side at fire time.

        ``trailing_offset_bps`` is the trailing trigger distance and
        ``limit_offset_bps`` the fire-time limit offset, both in basis points
        (integers; 1 bp = 0.01%). Both must be integers > 0.
        """

        def _positive_bps(value: int, name: str) -> None:
            # bool is an int subclass; reject it so we never serialize a JSON
            # boolean (`true`) where the wire expects an integer offset.
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer (basis points)")

        _positive_bps(trailing_offset_bps, "trailing_offset_bps")
        _positive_bps(limit_offset_bps, "limit_offset_bps")
        return cls(
            market_id=market_id,
            side=side,
            order_type="TrailingLimit",
            quantity=quantity,
            time_in_force=time_in_force,
            price=None,
            reduce_only=reduce_only,
            trailing_offset_bps=trailing_offset_bps,
            limit_offset_bps=limit_offset_bps,
        )

    def to_payload(self) -> dict[str, Any]:
        """Serialize to the JSON body the API expects. Money is sent as strings
        and basis-point offsets as integers; ``price`` / ``reduce_only`` /
        ``trailing_offset_bps`` / ``limit_offset_bps`` are omitted when ``None``."""
        body: dict[str, Any] = {
            "market_id": self.market_id,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": str(self.quantity),
            "time_in_force": self.time_in_force,
        }
        if self.price is not None:
            body["price"] = str(self.price)
        if self.reduce_only is not None:
            body["reduce_only"] = self.reduce_only
        if self.trailing_offset_bps is not None:
            body["trailing_offset_bps"] = self.trailing_offset_bps
        if self.limit_offset_bps is not None:
            body["limit_offset_bps"] = self.limit_offset_bps
        return body


@dataclass(frozen=True)
class AmendOrder:
    """A resting order amendment (``PATCH /orders/{order_id}``).

    Mirrors the Rust SDK's ``AmendOrder``. Set only the fields you want to
    change; ``None`` fields are omitted from the wire payload, so an amend never
    accidentally resets a field. At least one of ``price`` / ``size`` must be
    set — :meth:`has_changes` reports whether that holds. Money is sent as
    decimal strings.
    """

    price: Decimal | None = None
    size: Decimal | None = None

    def has_changes(self) -> bool:
        """True when at least one field is set (i.e. the amend is non-empty)."""
        return self.price is not None or self.size is not None

    def to_payload(self) -> dict[str, Any]:
        """Serialize to the JSON body; unset fields are omitted."""
        body: dict[str, Any] = {}
        if self.price is not None:
            body["price"] = str(self.price)
        if self.size is not None:
            body["size"] = str(self.size)
        return body


@dataclass(frozen=True)
class MarginAdjustment:
    """Result of adding/removing isolated margin (``POST /account/margin``).

    Mirrors the Rust SDK's ``MarginAdjustment``.
    """

    market_id: str
    allocated_margin: Decimal
    collateral: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarginAdjustment:
        return cls(
            market_id=str(d.get("market_id", "")),
            allocated_margin=to_decimal(d.get("allocated_margin", 0)),
            collateral=to_decimal(d.get("collateral", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class LeverageUpdate:
    """Result of setting a market's leverage (``POST /account/leverage``).

    Mirrors the Rust SDK's ``LeverageUpdate``.
    """

    market_id: str
    leverage: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LeverageUpdate:
        return cls(
            market_id=str(d.get("market_id", "")),
            leverage=int(d.get("leverage", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class CancelOnDisconnectStatus:
    """Account cancel-on-disconnect (COD) state (``/account/cancel-on-disconnect``).

    :attr:`enabled` is the account's own opt-in. :attr:`active` is whether COD
    will actually fire — the account opt-in *and* the exchange-side feature
    switch: if :attr:`enabled` is true but :attr:`active` is false, the exchange
    has the feature switched off. :attr:`grace_secs` is how long the exchange
    waits after the last ``/ws`` disconnect before cancelling (a reconnect
    within the window disarms it); ``None`` when the feature is unavailable on
    this deployment.
    """

    enabled: bool
    active: bool
    grace_secs: int | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CancelOnDisconnectStatus:
        return cls(
            enabled=bool(d.get("enabled", False)),
            active=bool(d.get("active", False)),
            grace_secs=opt_int(d.get("grace_secs")),
            raw=d,
        )


@dataclass(frozen=True)
class BatchOrderResult:
    """One entry in the array returned by ``POST /orders/batch``.

    The batch is processed sequentially and non-atomically, so each entry
    independently reports either a placed order or a per-order rejection, in
    request order. The spec models this as a union tagged by ``outcome``:

    * ``outcome == "ok"`` carries the same ``{ order, fills }`` shape as
      ``POST /orders`` — :attr:`order` is set (and :attr:`fills` populated),
      while :attr:`error` / :attr:`message` are ``None``.
    * ``outcome == "err"`` mirrors the global error envelope —
      :attr:`error` and :attr:`message` are set while :attr:`order` is ``None``.

    Use :attr:`is_ok` / :attr:`is_err` to branch. Unknown/absent fields decode to
    ``None`` rather than failing, and the full entry stays on :attr:`raw`.
    """

    outcome: str
    order: Order | None
    fills: list[Fill]
    error: str | None
    message: str | None
    raw: dict[str, Any]

    @property
    def is_ok(self) -> bool:
        """True when this entry placed an order (``outcome == "ok"``)."""
        return self.outcome == "ok"

    @property
    def is_err(self) -> bool:
        """True when this entry was rejected (``outcome == "err"``)."""
        return self.outcome == "err"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BatchOrderResult:
        order_raw = d.get("order")
        return cls(
            outcome=str(d.get("outcome", "")),
            order=Order.from_dict(order_raw) if isinstance(order_raw, dict) else None,
            fills=[Fill.from_dict(f) for f in d.get("fills", []) if isinstance(f, dict)],
            error=opt_str(d.get("error")),
            message=opt_str(d.get("message")),
            raw=d,
        )

    @classmethod
    def malformed(cls, value: Any) -> BatchOrderResult:
        """Error-shaped placeholder for a response entry that is not an object.

        ``create_orders`` promises one result per submitted order, in request
        order. A malformed element therefore decodes to an ``err``-shaped entry
        (``error == "malformed_result"``) instead of being dropped, so callers
        zipping results back to their requests never silently misalign. The
        offending value is preserved on ``raw["value"]``.
        """
        return cls(
            outcome="err",
            order=None,
            fills=[],
            error="malformed_result",
            message=f"malformed batch result entry: expected an object, got {type(value).__name__}",
            raw={"value": value},
        )


@dataclass(frozen=True)
class DepositResult:
    """Result of a deposit (``POST /account/deposit``)."""

    balance: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DepositResult:
        return cls(balance=to_decimal(d.get("balance", 0)), raw=d)


@dataclass(frozen=True)
class CreditResult:
    """Result of claiming synthetic USDX credit (``POST /account/credit``)."""

    amount: Decimal
    credited_today: Decimal
    daily_limit: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CreditResult:
        return cls(
            amount=to_decimal(d.get("amount", 0)),
            credited_today=to_decimal(d.get("credited_today", 0)),
            daily_limit=to_decimal(d.get("daily_limit", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class Withdrawal:
    """A withdrawal record (``GET /withdrawals``)."""

    id: str
    amount: Decimal
    timestamp: int
    status: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Withdrawal:
        return cls(
            id=str(d.get("id", "")),
            amount=to_decimal(d.get("amount", 0)),
            timestamp=int(d.get("timestamp", 0)),
            status=str(d.get("status", "")),
            raw=d,
        )


@dataclass(frozen=True)
class RateLimitStatus:
    """The caller's rate-limit status (``GET /account/rate-limit``).

    A token bucket: ``limit`` is the per-second ceiling / burst capacity,
    ``remaining`` the tokens available now, ``reset_at_ms`` when it refills
    (``0`` when full). All three are ``None`` for the unlimited tier.
    """

    tier: str
    limit: int | None
    remaining: int | None
    reset_at_ms: int | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RateLimitStatus:
        return cls(
            tier=str(d.get("tier", "")),
            limit=d.get("limit"),
            remaining=d.get("remaining"),
            reset_at_ms=d.get("reset_at_ms"),
            raw=d,
        )


@dataclass(frozen=True)
class ApiKeyInfo:
    """An API key associated with the authenticated session (``GET /keys``)."""

    key_id: str
    tier: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ApiKeyInfo:
        return cls(key_id=str(d.get("key_id", "")), tier=str(d.get("tier", "")), raw=d)


@dataclass(frozen=True)
class AgentInfo:
    """A registered agent key for the authenticated wallet (``GET /agents``).

    The wire sends camelCase; optional fields default rather than fail.
    """

    address: str
    expires_at: int
    registered_at: int
    label: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentInfo:
        return cls(
            address=str(d.get("address", "")),
            expires_at=int(d.get("expiresAt", 0)),
            registered_at=int(d.get("registeredAt", 0)),
            label=d.get("label"),
            raw=d,
        )


@dataclass(frozen=True)
class TierOverride:
    """An account rate-limit tier override (``/admin/tiers``)."""

    address: str
    tier: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TierOverride:
        return cls(address=str(d.get("address", "")), tier=str(d.get("tier", "")), raw=d)


@dataclass(frozen=True)
class WsToken:
    """A freshly minted, single-use WebSocket token (``POST /ws-tokens``)."""

    token: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WsToken:
        return cls(token=str(d.get("token", "")), raw=d)


@dataclass(frozen=True)
class BridgeAsset:
    """A bridgeable asset on a specific chain (``/bridge/assets``)."""

    symbol: str
    decimals: int
    min_amount: Decimal
    confirmations: int
    fee: Decimal | None
    contract_address: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BridgeAsset:
        return cls(
            symbol=str(d.get("symbol", "")),
            decimals=int(d.get("decimals", 0)),
            min_amount=to_decimal(d.get("min_amount", 0)),
            confirmations=int(d.get("confirmations", 0)),
            fee=opt_decimal(d.get("fee")),
            contract_address=opt_str(d.get("contract_address")),
            raw=d,
        )


@dataclass(frozen=True)
class BridgeChainAssets:
    """Bridgeable assets for one chain."""

    chain: str
    chain_id: int | None
    deposit_assets: list[BridgeAsset]
    withdraw_assets: list[BridgeAsset]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BridgeChainAssets:
        return cls(
            chain=str(d.get("chain", "")),
            chain_id=opt_int(d.get("chain_id")),
            deposit_assets=[
                BridgeAsset.from_dict(a) for a in d.get("deposit_assets", []) if isinstance(a, dict)
            ],
            withdraw_assets=[
                BridgeAsset.from_dict(a)
                for a in d.get("withdraw_assets", [])
                if isinstance(a, dict)
            ],
            raw=d,
        )


@dataclass(frozen=True)
class BridgeAssetsResponse:
    """Supported bridge chains and their assets (``GET /bridge/assets``)."""

    chains: list[BridgeChainAssets]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BridgeAssetsResponse:
        return cls(
            chains=[
                BridgeChainAssets.from_dict(c) for c in d.get("chains", []) if isinstance(c, dict)
            ],
            raw=d,
        )


@dataclass(frozen=True)
class BridgeDepositAddress:
    """A per-account deposit address on a specific chain."""

    address: str
    chain: str
    accepts: list[str]
    account_id: str
    created_at: int
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BridgeDepositAddress:
        return cls(
            address=str(d.get("address", "")),
            chain=str(d.get("chain", "")),
            accepts=[str(a) for a in d.get("accepts", [])],
            account_id=str(d.get("account_id", "")),
            created_at=int(d.get("created_at", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class BridgeDeposit:
    """A cross-chain deposit tracked by the watcher (read model).

    ``status`` moves ``detected`` -> ``confirming`` -> ``credited`` | ``failed``.
    """

    id: str
    account_id: str
    chain: str
    asset: str
    amount: Decimal
    address: str
    status: str
    confirmations: int | None
    required_confirmations: int | None
    tx_hash: str | None
    credited_at: int | None
    created_at: int
    updated_at: int | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BridgeDeposit:
        return cls(
            id=str(d.get("id", "")),
            account_id=str(d.get("account_id", "")),
            chain=str(d.get("chain", "")),
            asset=str(d.get("asset", "")),
            amount=to_decimal(d.get("amount", 0)),
            address=str(d.get("address", "")),
            status=str(d.get("status", "")),
            confirmations=opt_int(d.get("confirmations")),
            required_confirmations=opt_int(d.get("required_confirmations")),
            tx_hash=opt_str(d.get("tx_hash")),
            credited_at=opt_int(d.get("credited_at")),
            created_at=int(d.get("created_at", 0)),
            updated_at=opt_int(d.get("updated_at")),
            raw=d,
        )
