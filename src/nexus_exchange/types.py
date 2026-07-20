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


# -- account & trading models ---------------------------------------------


@dataclass(frozen=True)
class Position:
    """An open position. All money fields are exact decimal strings."""

    market_id: str
    side: str
    size: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    liquidation_price: Decimal | None
    raw: dict[str, Any]

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
        )


@dataclass(frozen=True)
class AccountSummary:
    """Account balance and collateral summary (``GET /account``)."""

    balance: Decimal
    collateral: Decimal
    equity: Decimal
    available_margin: Decimal
    positions: list[Position]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AccountSummary:
        return cls(
            balance=to_decimal(d.get("balance", 0)),
            collateral=to_decimal(d.get("collateral", 0)),
            equity=to_decimal(d.get("equity", 0)),
            available_margin=to_decimal(d.get("available_margin", 0)),
            positions=[Position.from_dict(p) for p in d.get("positions", [])],
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
