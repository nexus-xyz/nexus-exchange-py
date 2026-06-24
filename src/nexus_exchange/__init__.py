"""Official Python SDK for the Nexus Exchange API (experimental).

See the README for the current support table. Quick start::

    from nexus_exchange import Client

    with Client() as client:
        for market in client.fetch_markets():
            print(market.market_id)
"""

from __future__ import annotations

from .auth import (
    SIGN_IN_MESSAGE,
    AgentRegistered,
    AgentRegistration,
    EthSigner,
    LoginRequest,
    LoginResponse,
)
from .client import DEFAULT_USER_AGENT, Client, Network
from .errors import (
    ApiError,
    AuthError,
    MissingCredentialsError,
    NexusExchangeError,
    TransportError,
)
from .types import (
    AccountSummary,
    AdlClosure,
    AdlEvent,
    AgentInfo,
    ApiKeyInfo,
    CreditResult,
    DepositResult,
    Fill,
    FundingSample,
    HealthStatus,
    Market,
    MarketStatus,
    MarketSummary,
    MarkPrice,
    Ohlcv,
    Order,
    OrderBook,
    OrderRequest,
    OrderResponse,
    Position,
    PriceLevel,
    RateLimitStatus,
    Ticker,
    TierOverride,
    Trade,
    Withdrawal,
    WsToken,
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
    "EthSigner",
    "LoginRequest",
    "AgentRegistration",
    "LoginResponse",
    "AgentRegistered",
    "SIGN_IN_MESSAGE",
    "AccountSummary",
    "Position",
    "Fill",
    "Order",
    "OrderRequest",
    "OrderResponse",
    "DepositResult",
    "CreditResult",
    "Withdrawal",
    "RateLimitStatus",
    "ApiKeyInfo",
    "AgentInfo",
    "TierOverride",
    "WsToken",
    "NexusExchangeError",
    "ApiError",
    "AuthError",
    "TransportError",
    "MissingCredentialsError",
    "DEFAULT_USER_AGENT",
    "__version__",
]
