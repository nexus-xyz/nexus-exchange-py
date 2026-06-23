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
    InvalidRequestError,
    MissingCredentialsError,
    NexusExchangeError,
    TransportError,
)
from .types import Account, Fill, Market, Order, Position, Ticker

__version__ = "0.1.0"

__all__ = [
    "Client",
    "Network",
    "Account",
    "Fill",
    "Market",
    "Order",
    "Position",
    "Ticker",
    "NexusExchangeError",
    "ApiError",
    "InvalidRequestError",
    "TransportError",
    "MissingCredentialsError",
    "DEFAULT_USER_AGENT",
    "__version__",
]
