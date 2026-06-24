"""Official Python SDK for the Nexus Exchange API (experimental).

See the README for the current support table. Quick start::

    from nexus_exchange import Client

    with Client() as client:
        for market in client.fetch_markets():
            print(market.market_id)
"""

from __future__ import annotations

from .client import DEFAULT_USER_AGENT, Client, Network
from .errors import (
    ApiError,
    MissingCredentialsError,
    NexusExchangeError,
    TransportError,
)
from .types import (
    AdlClosure,
    AdlEvent,
    FundingSample,
    HealthStatus,
    Market,
    MarketStatus,
    MarketSummary,
    MarkPrice,
    Ohlcv,
    OrderBook,
    PriceLevel,
    Ticker,
    Trade,
)

__version__ = "0.1.0"

__all__ = [
    "Client",
    "Network",
    "Market",
    "MarketSummary",
    "MarketStatus",
    "Ticker",
    "OrderBook",
    "PriceLevel",
    "Trade",
    "Ohlcv",
    "FundingSample",
    "MarkPrice",
    "AdlEvent",
    "AdlClosure",
    "HealthStatus",
    "NexusExchangeError",
    "ApiError",
    "TransportError",
    "MissingCredentialsError",
    "DEFAULT_USER_AGENT",
    "__version__",
]
