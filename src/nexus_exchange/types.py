"""Typed models for Nexus Exchange responses.

Experimental: the skeleton resolves only the identity field and keeps the full
payload on ``.raw``. Richer typed fields (Decimal prices, sizes, timestamps)
land as the surface stabilizes — until then, read everything else off ``.raw``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Market:
    """A tradable market. ``raw`` holds the full ``/markets/summary`` entry."""

    market_id: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Market:
        market_id = d.get("market_id") or d.get("symbol") or d.get("id") or ""
        return cls(market_id=str(market_id), raw=d)


@dataclass(frozen=True)
class Ticker:
    """Latest ticker for a market. ``raw`` holds the full ticker payload."""

    market_id: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, market_id: str, d: dict[str, Any]) -> Ticker:
        return cls(market_id=market_id, raw=d)


@dataclass(frozen=True)
class Account:
    """Account balance / collateral summary. ``raw`` holds ``GET /account``."""

    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Account:
        return cls(raw=d)


@dataclass(frozen=True)
class Position:
    """An open position. ``raw`` holds the full ``/positions`` entry."""

    market_id: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Position:
        return cls(market_id=str(d.get("market_id") or ""), raw=d)


@dataclass(frozen=True)
class Order:
    """An order record. ``raw`` holds the full ``/orders`` entry."""

    id: str
    market_id: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Order:
        order_id = d.get("id") or d.get("order_id") or ""
        return cls(id=str(order_id), market_id=str(d.get("market_id") or ""), raw=d)


@dataclass(frozen=True)
class Fill:
    """A private trade execution. ``raw`` holds the full ``/fills`` entry."""

    id: str
    order_id: str
    market_id: str
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fill:
        return cls(
            id=str(d.get("id") or ""),
            order_id=str(d.get("order_id") or ""),
            market_id=str(d.get("market_id") or ""),
            raw=d,
        )
