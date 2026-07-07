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
from .client import DEFAULT_USER_AGENT, Client, Network, RetryConfig
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
    AmendOrder,
    ApiKeyInfo,
    CreditResult,
    DepositResult,
    Fill,
    FundingSample,
    HealthStatus,
    LeverageUpdate,
    MarginAdjustment,
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

__version__ = "0.2.0"

__all__ = [
    "Client",
    "Network",
    "RetryConfig",
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
    "AmendOrder",
    "MarginAdjustment",
    "LeverageUpdate",
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
