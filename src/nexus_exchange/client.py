"""Synchronous HTTP client for the Nexus Exchange API.

A thin wrapper mirroring the Rust SDK: typed methods over the REST routes, HMAC
request signing, one error hierarchy. **Experimental.** Covers the public
market-data routes plus the signed account / trading / admin routes (see the
README's support table). WebSocket streaming and the wallet-signed auth flows
(EIP-191 login, EIP-712 agent registration) are not built yet.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .errors import ApiError, MissingCredentialsError, TransportError
from .types import (
    AccountSummary,
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
    RateLimitStatus,
    Ticker,
    TierOverride,
    Trade,
    Withdrawal,
    WsToken,
)

__all__ = ["Client", "Network", "DEFAULT_USER_AGENT"]

#: Identifies Python-SDK traffic in the exchange's per-client usage metrics.
DEFAULT_USER_AGENT = "nexus-exchange-py/0.1.0"
DEFAULT_TIMEOUT = 30.0


def _query(**params: Any) -> str:
    """Build a URL-encoded query string from non-``None`` params.

    Params are emitted in the order given so the signed canonical query and the
    sent query stay byte-for-byte identical (see :meth:`Client._request`).
    """
    items = [(k, str(v)) for k, v in params.items() if v is not None]
    return urlencode(items)


class Network(str, Enum):
    """Which Nexus Exchange environment to target."""

    STABLE = "stable"
    BETA = "beta"
    LOCAL = "local"

    @property
    def base_url(self) -> str:
        return {
            Network.STABLE: "https://exchange.nexus.xyz/api/exchange",
            Network.BETA: "https://beta.exchange.nexus.xyz/api/exchange",
            Network.LOCAL: "http://localhost:9090",
        }[self]


class Client:
    """Client for the Nexus Exchange REST API.

    Public market-data methods need no credentials. Pass ``api_key`` +
    ``api_secret`` (HMAC) to sign requests. Note the public gateway proxies
    signed calls to the *site* account; for per-account auth point ``base_url``
    at a direct gateway (e.g. ``Network.LOCAL``). See the README.

    Usable as a context manager::

        with Client() as client:
            markets = client.fetch_markets()
    """

    def __init__(
        self,
        network: Network = Network.STABLE,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (base_url or network.base_url).rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(
            timeout=timeout, headers={"user-agent": DEFAULT_USER_AGENT}
        )

    # -- lifecycle --------------------------------------------------------
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def has_credentials(self) -> bool:
        return bool(self._api_key and self._api_secret)

    # -- public market data ----------------------------------------------
    def fetch_markets(self) -> list[Market]:
        """``GET /markets`` — all tradable markets and their trading rules."""
        data = self._request("GET", "/markets")
        rows = data if isinstance(data, list) else data.get("markets", [])
        return [Market.from_dict(m) for m in rows]

    def fetch_market_summaries(self) -> list[MarketSummary]:
        """``GET /markets/summary`` — per-market 24h volume and halt state."""
        data = self._request("GET", "/markets/summary")
        rows = data if isinstance(data, list) else data.get("markets", [])
        return [MarketSummary.from_dict(m) for m in rows]

    def fetch_tickers(self) -> dict[str, Ticker]:
        """``GET /tickers`` — tickers for all markets, keyed by market id.

        The envelope is a bare object keyed by market id (spec:
        ``additionalProperties: Ticker``); an empty result is ``{}``.
        """
        data = self._request("GET", "/tickers")
        if not isinstance(data, dict):
            return {}
        return {mid: Ticker.from_dict(t) for mid, t in data.items()}

    def fetch_ticker(self, market_id: str) -> Ticker:
        """``GET /markets/{market_id}/ticker`` — latest ticker for one market."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/ticker")
        return Ticker.from_dict(data if isinstance(data, dict) else {"symbol": market_id})

    def fetch_order_book(self, market_id: str) -> OrderBook:
        """``GET /markets/{market_id}/orderbook`` — order book snapshot."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/orderbook")
        return OrderBook.from_dict(data if isinstance(data, dict) else {})

    def fetch_trades(self, market_id: str, limit: int | None = None) -> list[Trade]:
        """``GET /markets/{market_id}/trades`` — recent public trades (newest first)."""
        query = _query(limit=limit)
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/trades", query=query)
        rows = data if isinstance(data, list) else []
        return [Trade.from_dict(t) for t in rows]

    def fetch_ohlcv(
        self,
        market_id: str,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> list[Ohlcv]:
        """``GET /markets/{market_id}/candles`` — OHLCV candles."""
        query = _query(timeframe=timeframe, limit=limit)
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/candles", query=query)
        rows = data if isinstance(data, list) else []
        return [Ohlcv.from_row(r) for r in rows]

    def fetch_funding_rate_history(
        self, market_id: str, limit: int | None = None
    ) -> list[FundingSample]:
        """``GET /markets/{market_id}/funding`` — intra-hour funding-rate history."""
        query = _query(limit=limit)
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/funding", query=query)
        rows = data if isinstance(data, list) else []
        return [FundingSample.from_dict(s) for s in rows]

    def fetch_mark_price(self, market_id: str) -> MarkPrice:
        """``GET /markets/{market_id}/mark-price`` — current mark price."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/mark-price")
        return MarkPrice.from_dict(data if isinstance(data, dict) else {})

    def fetch_market_status(self, market_id: str) -> MarketStatus:
        """``GET /markets/{market_id}/status`` — lifecycle / halt status."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/status")
        return MarketStatus.from_dict(data if isinstance(data, dict) else {})

    def fetch_market_adl_events(self, market_id: str, limit: int | None = None) -> list[AdlEvent]:
        """``GET /markets/{market_id}/adl-events`` — ADL settlement events (newest first)."""
        query = _query(limit=limit)
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/adl-events", query=query)
        rows = data if isinstance(data, list) else []
        return [AdlEvent.from_dict(e) for e in rows]

    def fetch_account_adl_history(self, address: str, limit: int | None = None) -> list[AdlEvent]:
        """``GET /account/{address}/adl-history`` — ADL events touching an account."""
        query = _query(limit=limit)
        data = self._request("GET", f"/account/{quote(address, safe='')}/adl-history", query=query)
        rows = data if isinstance(data, list) else []
        return [AdlEvent.from_dict(e) for e in rows]

    def health_check(self) -> HealthStatus:
        """``GET /health`` — indexer health/status snapshot."""
        data = self._request("GET", "/health")
        return HealthStatus.from_dict(data if isinstance(data, dict) else {})

    # -- account (signed reads) ------------------------------------------
    def fetch_balance(self) -> AccountSummary:
        """``GET /account`` — balance and collateral summary. Requires credentials."""
        data = self._request("GET", "/account", signed=True)
        return AccountSummary.from_dict(data if isinstance(data, dict) else {})

    def fetch_positions(self) -> list[Position]:
        """``GET /positions`` — open positions. Requires credentials."""
        data = self._request("GET", "/positions", signed=True)
        return [Position.from_dict(p) for p in (data if isinstance(data, list) else [])]

    def fetch_my_trades(self) -> list[Fill]:
        """``GET /fills`` — recent fills (private executions). Requires credentials."""
        data = self._request("GET", "/fills", signed=True)
        return [Fill.from_dict(f) for f in (data if isinstance(data, list) else [])]

    def fetch_withdrawals(self) -> list[Withdrawal]:
        """``GET /withdrawals`` — withdrawal history. Requires credentials."""
        data = self._request("GET", "/withdrawals", signed=True)
        return [Withdrawal.from_dict(w) for w in (data if isinstance(data, list) else [])]

    def fetch_rate_limit_status(self) -> RateLimitStatus:
        """``GET /account/rate-limit`` — the caller's rate-limit status.

        Requires credentials. Does not consume a rate-limit token.
        """
        data = self._request("GET", "/account/rate-limit", signed=True)
        return RateLimitStatus.from_dict(data if isinstance(data, dict) else {})

    # -- account (signed writes) -----------------------------------------
    def deposit(self, amount: Decimal | str) -> DepositResult:
        """``POST /account/deposit`` — deposit USDX collateral. Requires credentials."""
        data = self._request("POST", "/account/deposit", body={"amount": str(amount)}, signed=True)
        return DepositResult.from_dict(data if isinstance(data, dict) else {})

    def claim_credit(self, amount: Decimal | str | None = None) -> CreditResult:
        """``POST /account/credit`` — claim synthetic (testnet) USDX from the faucet.

        Omit ``amount`` to claim the full remaining daily allowance. Requires
        credentials.
        """
        body = {} if amount is None else {"amount": str(amount)}
        data = self._request("POST", "/account/credit", body=body, signed=True)
        return CreditResult.from_dict(data if isinstance(data, dict) else {})

    # -- orders (signed) -------------------------------------------------
    def create_order(self, order: OrderRequest) -> OrderResponse:
        """``POST /orders`` — place a single order. Requires credentials."""
        data = self._request("POST", "/orders", body=order.to_payload(), signed=True)
        return OrderResponse.from_dict(data if isinstance(data, dict) else {})

    def create_orders(self, orders: list[OrderRequest]) -> Any:
        """``POST /orders/batch`` — submit a batch of orders (sequential, non-atomic).

        Requires credentials. The per-order result array is untyped in the spec,
        so the raw decoded JSON is returned.
        """
        body = [o.to_payload() for o in orders]
        return self._request("POST", "/orders/batch", body=body, signed=True)

    def fetch_open_orders(self) -> list[Order]:
        """``GET /orders`` — open orders for the account. Requires credentials."""
        data = self._request("GET", "/orders", signed=True)
        return [Order.from_dict(o) for o in (data if isinstance(data, list) else [])]

    def fetch_order(self, order_id: str) -> Order:
        """``GET /orders/{order_id}`` — fetch a single order. Requires credentials."""
        data = self._request("GET", f"/orders/{quote(order_id, safe='')}", signed=True)
        return Order.from_dict(data if isinstance(data, dict) else {})

    def cancel_order(self, order_id: str) -> Any:
        """``DELETE /orders/{order_id}`` — cancel a single order. Requires credentials."""
        return self._request("DELETE", f"/orders/{quote(order_id, safe='')}", signed=True)

    def cancel_all_orders(self) -> Any:
        """``DELETE /orders`` — cancel all open orders. Requires credentials."""
        return self._request("DELETE", "/orders", signed=True)

    # -- keys / agents (signed) ------------------------------------------
    def fetch_api_keys(self) -> list[ApiKeyInfo]:
        """``GET /keys`` — API keys for the session. Requires credentials."""
        data = self._request("GET", "/keys", signed=True)
        return [ApiKeyInfo.from_dict(k) for k in (data if isinstance(data, list) else [])]

    def delete_api_key(self, key_id: str) -> Any:
        """``DELETE /keys/{key_id}`` — delete an API key you own. Requires credentials."""
        return self._request("DELETE", f"/keys/{quote(key_id, safe='')}", signed=True)

    def fetch_agents(self) -> list[AgentInfo]:
        """``GET /agents`` — non-expired agent keys. Requires credentials."""
        data = self._request("GET", "/agents", signed=True)
        return [AgentInfo.from_dict(a) for a in (data if isinstance(data, list) else [])]

    def revoke_agent(self, address: str) -> Any:
        """``DELETE /agents/{address}`` — revoke an agent key. Requires credentials."""
        return self._request("DELETE", f"/agents/{quote(address, safe='')}", signed=True)

    def mint_web_socket_token(self) -> WsToken:
        """``POST /ws-tokens`` — mint a single-use WebSocket token. Requires credentials."""
        data = self._request("POST", "/ws-tokens", signed=True)
        return WsToken.from_dict(data if isinstance(data, dict) else {})

    # -- admin (signed) --------------------------------------------------
    def set_account_tier(self, address: str, tier: str) -> TierOverride:
        """``PUT /admin/tiers`` — set an account's rate-limit tier. Requires admin creds."""
        data = self._request(
            "PUT", "/admin/tiers", body={"address": address, "tier": tier}, signed=True
        )
        return TierOverride.from_dict(data if isinstance(data, dict) else {})

    def fetch_tier_overrides(self) -> list[TierOverride]:
        """``GET /admin/tiers`` — list tier overrides. Requires admin creds."""
        data = self._request("GET", "/admin/tiers", signed=True)
        return [TierOverride.from_dict(t) for t in (data if isinstance(data, list) else [])]

    def reset_account_tier(self, address: str) -> Any:
        """``DELETE /admin/tiers/{address}`` — reset to default tier. Requires admin creds."""
        return self._request("DELETE", f"/admin/tiers/{quote(address, safe='')}", signed=True)

    # -- request plumbing -------------------------------------------------
    def _sign(self, method: str, path: str, query: str, body: bytes) -> dict[str, str]:
        if not self._api_key or not self._api_secret:
            raise MissingCredentialsError("signed request requires api_key and api_secret")
        ts = str(int(time.time() * 1000))
        body_hash = hashlib.sha256(body).hexdigest()
        # Canonical string the indexer verifies (auth.rs::verify_hmac):
        #   <ts>\n<METHOD>\n<path>\n<query>\n<sha256hex(body)>
        canonical = "\n".join([ts, method.upper(), path, query, body_hash])
        signature = hmac.new(
            bytes.fromhex(self._api_secret), canonical.encode(), hashlib.sha256
        ).hexdigest()
        return {"x-api-key": self._api_key, "x-timestamp": ts, "x-signature": signature}

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        body: Any | None = None,
        signed: bool = False,
    ) -> Any:
        body_bytes = b"" if body is None else json.dumps(body).encode()
        headers: dict[str, str] = {}
        if body is not None:
            headers["content-type"] = "application/json"
        if signed:
            headers.update(self._sign(method, path, query, body_bytes))

        # Build the URL by hand so the signed query matches the sent query byte
        # for byte (no client-side re-encoding).
        url = f"{self._base_url}{path}"
        if query:
            url = f"{url}?{query}"

        try:
            resp = self._http.request(
                method,
                url,
                headers=headers,
                content=body_bytes if body is not None else None,
            )
        except httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc

        if resp.status_code >= 400:
            code: str | None = None
            message: str | None = None
            try:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    code = parsed.get("code")
                    message = parsed.get("message")
            except ValueError:
                pass
            raise ApiError(resp.status_code, resp.text[:2000], code=code, message=message)

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text
