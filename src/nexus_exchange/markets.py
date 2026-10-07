"""Snap a price to a market's tick and a size to its lot before submitting.

The exchange rejects an order whose price is not a multiple of ``tick_size`` or
whose size is not a multiple of ``lot_size``. These mirror ``round_price`` /
``round_size`` in the Rust SDK's ``src/markets.rs``, in exact
:class:`~decimal.Decimal` arithmetic (never float division, ENG-19697).
"""

from __future__ import annotations

from decimal import MAX_PREC, Decimal, localcontext

from .types import Market

__all__ = ["round_price", "round_size"]


def round_price(market: Market, price: Decimal, side: str) -> Decimal:
    """Round ``price`` onto ``market.tick_size``, on the side that never crosses.

    A buy rounds toward zero and a sell away from zero (ENG-18543), i.e. the Rust
    SDK's ``Rounding::Down`` and ``Rounding::Up``. ``side`` is ``"Buy"`` or
    ``"Sell"`` (any case); anything else raises :class:`ValueError`. A zero tick
    returns ``price`` unchanged.
    """
    s = side.lower()
    if s not in ("buy", "sell"):
        raise ValueError(f"side must be 'Buy' or 'Sell', got {side!r}")
    return _round_to_increment(price, market.tick_size, away_from_zero=s == "sell")


def round_size(market: Market, size: Decimal) -> Decimal:
    """Round ``size`` onto ``market.lot_size`` toward zero, never up into more risk.

    The Rust SDK's ``round_size`` with its default, ``Rounding::Down``. A zero
    lot returns ``size`` unchanged.
    """
    return _round_to_increment(size, market.lot_size, away_from_zero=False)


def _round_to_increment(value: Decimal, increment: Decimal, *, away_from_zero: bool) -> Decimal:
    if not increment:
        return value
    # `divmod` and `*` here are exact, so the precision only has to be big enough
    # never to round them; no inexact division runs under it.
    with localcontext() as ctx:
        ctx.prec = MAX_PREC
        steps, rem = divmod(value, increment)  # Decimal `//` truncates toward zero
        if away_from_zero and rem:
            steps += 1 if (value > 0) == (increment > 0) else -1
        out = steps * increment
        if not out:
            return Decimal(0)  # no "-0", as rust_decimal's normalize()
        # Drop trailing zeros ("50001.0" -> "50001") without `normalize()`'s
        # exponent form ("5E+4"), which would reach the wire as-is.
        return out.quantize(Decimal(1)) if out == out.to_integral_value() else out.normalize()
