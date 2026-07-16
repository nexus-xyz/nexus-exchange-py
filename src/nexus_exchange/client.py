"""Synchronous HTTP client for the Nexus Exchange API.

A thin wrapper mirroring the Rust SDK: typed methods over the REST routes, HMAC
request signing, one error hierarchy. **Experimental.** Covers the public
market-data routes, the signed account / trading / admin routes, and the
wallet-signed auth flows (EIP-191 login, EIP-712 agent registration) — see the
README's support table. WebSocket streaming is not built yet.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from enum import Enum
from importlib import metadata
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .auth import AgentRegistered, AgentRegistration, EthSigner, LoginResponse
from .errors import ApiError, MissingCredentialsError, TransportError
from .types import (
    AccountSummary,
    AdlEvent,
    AgentInfo,
    AmendOrder,
    ApiKeyInfo,
    BatchOrderResult,
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
    RateLimitStatus,
    Ticker,
    TierOverride,
    Trade,
    Withdrawal,
    WsToken,
)

__all__ = ["Client", "Network", "DEFAULT_USER_AGENT", "DEFAULT_API_VERSION"]

_DISTRIBUTION_NAME = "nexus-exchange"


def _resolve_version() -> str:
    """Version of the installed ``nexus-exchange`` distribution.

    Read from package metadata so the ``User-Agent`` always reflects the
    actually-installed version — one source of truth (``pyproject.toml``) rather
    than a hand-updated string that can drift. Falls back to a literal when
    running from a source tree with no install, so import never fails.
    """
    try:
        return metadata.version(_DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:  # pragma: no cover - only without an install
        return "0.3.0"


#: Package version, resolved from installed distribution metadata.
__version__ = _resolve_version()

#: Identifies Python-SDK traffic in the exchange's per-client usage metrics
#: (ENG-4804). Normalized to ``nexus-exchange-py/<package version>`` and sent as
#: ``User-Agent`` on every request.
DEFAULT_USER_AGENT = f"nexus-exchange-py/{__version__}"

#: Exchange API spec tag this SDK is compiled against, sent as
#: ``X-Nexus-Api-Version`` on every request so the edge can pin each request to a
#: contract version (ENG-5350). Mirrors the repo's source of truth in
#: ``.api-version``; that file is not shipped in the wheel, so the tag is baked
#: in here and ``tests/test_headers.py`` asserts the two never drift.
DEFAULT_API_VERSION = "v0.7.1"

DEFAULT_TIMEOUT = 30.0

#: Path prefix for the direct-service ("/api/v1") surface. Under the gateway
#: elimination (ENG-4740) each backend service exposes its own REST API under
#: this prefix, served at the host root rather than the ``/api/exchange``
#: gateway base. The HMAC signature is computed over the full request path
#: *including* this prefix (e.g. ``/api/v1/orders``), matching the server.
API_V1_PREFIX = "/api/v1"


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
        """Legacy gateway base (``/api/exchange``) for routes not yet migrated
        to the direct ``/api/v1`` service."""
        return {
            Network.STABLE: "https://exchange.nexus.xyz/api/exchange",
            Network.BETA: "https://beta.exchange.nexus.xyz/api/exchange",
            Network.LOCAL: "http://localhost:9090",
        }[self]

    @property
    def direct_base_url(self) -> str:
        """Direct-service base for the ``/api/v1`` surface — the host root, with
        no ``/api/exchange`` gateway prefix (see :data:`API_V1_PREFIX`)."""
        return {
            Network.STABLE: "https://exchange.nexus.xyz",
            Network.BETA: "https://beta.exchange.nexus.xyz",
            Network.LOCAL: "http://localhost:9090",
        }[self]


class Client:
    """Client for the Nexus Exchange REST API.

    Public market-data methods need no credentials. Pass ``api_key`` +
    ``api_secret`` (HMAC) to sign requests. Note the public gateway proxies
    signed calls to the *site* account; for per-account auth point ``base_url``
    at a direct gateway (e.g. ``Network.LOCAL``). See the README.

    Routing targets two bases. The migrated market-data and account/trading
    surface is served directly by its backend service under ``/api/v1`` at the
    host root (:attr:`Network.direct_base_url`); routes not yet migrated stay on
    the legacy ``/api/exchange`` gateway (:attr:`Network.base_url`). A custom
    ``base_url`` overrides *both* and must therefore point at the service root
    (e.g. ``http://localhost:9090``), not a ``/api/exchange`` gateway URL.

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
        api_version: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (base_url or network.base_url).rstrip("/")
        # Direct-service base for the /api/v1 surface. A caller-supplied base_url
        # overrides both bases (the local/direct-gateway case); otherwise each
        # network supplies its own gateway and direct roots.
        self._direct_base_url = (base_url or network.direct_base_url).rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        # Spec tag advertised on every request. Defaults to the tag the package
        # is pinned to; overridable so a caller can target a specific contract.
        # A blank / whitespace-only override falls back to the default rather
        # than sending an empty header.
        self._api_version = (api_version or "").strip() or DEFAULT_API_VERSION
        # Emitted on every request, whether the httpx client is owned or
        # caller-supplied. Copied per request in ``_request`` so the per-call
        # content-type / signing headers never mutate this shared dict.
        self._default_headers = {
            "user-agent": DEFAULT_USER_AGENT,
            "x-nexus-api-version": self._api_version,
        }
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

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
    # Most market-data reads are served by the direct /api/v1 service
    # (``direct=True``). A handful have no /api/v1 equivalent yet and stay on
    # the legacy gateway: ``GET /markets`` (the list route), ``/adl-events``,
    # ``/account/{addr}/adl-history`` and ``/health``.
    def fetch_markets(self) -> list[Market]:
        """``GET /markets`` — all tradable markets and their trading rules.

        Not migrated to ``/api/v1`` (no direct-service route yet); stays on the
        legacy gateway.
        """
        data = self._request("GET", "/markets")
        rows = data if isinstance(data, list) else data.get("markets", [])
        return [Market.from_dict(m) for m in rows]

    def fetch_market_summaries(self) -> list[MarketSummary]:
        """``GET /markets/summary`` — per-market 24h volume and halt state."""
        data = self._request("GET", "/markets/summary", direct=True)
        rows = data if isinstance(data, list) else data.get("markets", [])
        return [MarketSummary.from_dict(m) for m in rows]

    def fetch_tickers(self) -> dict[str, Ticker]:
        """``GET /tickers`` — tickers for all markets, keyed by market id.

        The envelope is a bare object keyed by market id (spec:
        ``additionalProperties: Ticker``); an empty result is ``{}``.
        """
        data = self._request("GET", "/tickers", direct=True)
        if not isinstance(data, dict):
            return {}
        return {mid: Ticker.from_dict(t) for mid, t in data.items()}

    def fetch_ticker(self, market_id: str) -> Ticker:
        """``GET /markets/{market_id}/ticker`` — latest ticker for one market."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/ticker", direct=True)
        return Ticker.from_dict(data if isinstance(data, dict) else {"symbol": market_id})

    def fetch_order_book(self, market_id: str) -> OrderBook:
        """``GET /markets/{market_id}/orderbook`` — order book snapshot."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/orderbook", direct=True)
        return OrderBook.from_dict(data if isinstance(data, dict) else {})

    def fetch_trades(self, market_id: str, limit: int | None = None) -> list[Trade]:
        """``GET /markets/{market_id}/trades`` — recent public trades (newest first)."""
        query = _query(limit=limit)
        data = self._request(
            "GET", f"/markets/{quote(market_id, safe='')}/trades", query=query, direct=True
        )
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
        data = self._request(
            "GET", f"/markets/{quote(market_id, safe='')}/candles", query=query, direct=True
        )
        rows = data if isinstance(data, list) else []
        return [Ohlcv.from_row(r) for r in rows]

    def fetch_funding_rate_history(
        self, market_id: str, limit: int | None = None
    ) -> list[FundingSample]:
        """``GET /markets/{market_id}/funding`` — intra-hour funding-rate history."""
        query = _query(limit=limit)
        data = self._request(
            "GET", f"/markets/{quote(market_id, safe='')}/funding", query=query, direct=True
        )
        rows = data if isinstance(data, list) else []
        return [FundingSample.from_dict(s) for s in rows]

    def fetch_mark_price(self, market_id: str) -> MarkPrice:
        """``GET /markets/{market_id}/mark-price`` — current mark price."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/mark-price", direct=True)
        return MarkPrice.from_dict(data if isinstance(data, dict) else {})

    def fetch_market_status(self, market_id: str) -> MarketStatus:
        """``GET /markets/{market_id}/status`` — lifecycle / halt status."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/status", direct=True)
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

    # -- wallet-signed auth ----------------------------------------------
    def sign_in(self, signer: EthSigner) -> LoginResponse:
        """``POST /auth/login`` — EIP-191 session login.

        Signs the fixed login message with ``signer`` and posts the result.
        Unauthenticated: the EIP-191 signature in the body is the credential.
        Returns the session token (treat as secret) and the recovered address.
        """
        body = signer.sign_in().to_dict()
        data = self._request("POST", "/auth/login", body=body)
        return LoginResponse.from_dict(data if isinstance(data, dict) else {})

    def register_agent(self, registration: AgentRegistration) -> AgentRegistered:
        """``POST /agents/register`` — EIP-712 agent-key registration.

        Takes a pre-signed body from
        :meth:`EthSigner.register_agent <nexus_exchange.EthSigner.register_agent>`.
        Unauthenticated: the EIP-712 signature in the body is the credential.
        """
        data = self._request("POST", "/agents/register", body=registration.to_dict())
        return AgentRegistered.from_dict(data if isinstance(data, dict) else {})

    # -- account (signed reads) ------------------------------------------
    def fetch_balance(self) -> AccountSummary:
        """``GET /account`` — balance and collateral summary. Requires credentials."""
        data = self._request("GET", "/account", signed=True, direct=True)
        return AccountSummary.from_dict(data if isinstance(data, dict) else {})

    def fetch_positions(self) -> list[Position]:
        """``GET /positions`` — open positions. Requires credentials."""
        data = self._request("GET", "/positions", signed=True, direct=True)
        return [Position.from_dict(p) for p in (data if isinstance(data, list) else [])]

    def fetch_my_trades(self) -> list[Fill]:
        """``GET /fills`` — recent fills (private executions). Requires credentials."""
        data = self._request("GET", "/fills", signed=True, direct=True)
        return [Fill.from_dict(f) for f in (data if isinstance(data, list) else [])]

    def fetch_withdrawals(self) -> list[Withdrawal]:
        """``GET /withdrawals`` — withdrawal history. Requires credentials."""
        data = self._request("GET", "/withdrawals", signed=True)
        return [Withdrawal.from_dict(w) for w in (data if isinstance(data, list) else [])]

    def fetch_rate_limit_status(self) -> RateLimitStatus:
        """``GET /account/rate-limit`` — the caller's rate-limit status.

        Requires credentials. Does not consume a rate-limit token.
        """
        data = self._request("GET", "/account/rate-limit", signed=True, direct=True)
        return RateLimitStatus.from_dict(data if isinstance(data, dict) else {})

    # -- account (signed writes) -----------------------------------------
    def deposit(self, amount: Decimal | str) -> DepositResult:
        """``POST /account/deposit`` — deposit USDX collateral. Requires credentials.

        Not in the ``/api/v1`` spec; stays on the legacy gateway.
        """
        data = self._request("POST", "/account/deposit", body={"amount": str(amount)}, signed=True)
        return DepositResult.from_dict(data if isinstance(data, dict) else {})

    def claim_credit(self, amount: Decimal | str | None = None) -> CreditResult:
        """``POST /account/credit`` — claim synthetic (testnet) USDX from the faucet.

        Omit ``amount`` to claim the full remaining daily allowance. Requires
        credentials.
        """
        body = {} if amount is None else {"amount": str(amount)}
        data = self._request("POST", "/account/credit", body=body, signed=True, direct=True)
        return CreditResult.from_dict(data if isinstance(data, dict) else {})

    def adjust_margin(
        self, market_id: str, direction: str, amount: Decimal | str
    ) -> MarginAdjustment:
        """``POST /account/margin`` — add/remove isolated margin on a position.

        Requires credentials. Only applies to a position in ``isolated`` margin
        mode; the server rejects a cross-margined position with
        ``MarginModeNotIsolated``. ``direction`` is ``"add"`` or ``"remove"``
        (sent verbatim); ``amount`` is the collateral to move, sent as a decimal
        string and must be positive.

        Not in the ``/api/v1`` spec; stays on the legacy gateway.
        """
        if not market_id:
            raise ValueError("market_id is required")
        if direction not in ("add", "remove"):
            raise ValueError('direction must be "add" or "remove"')
        if Decimal(str(amount)) <= 0:
            raise ValueError("margin amount must be positive")
        data = self._request(
            "POST",
            "/account/margin",
            body={"market_id": market_id, "direction": direction, "amount": str(amount)},
            signed=True,
        )
        return MarginAdjustment.from_dict(data if isinstance(data, dict) else {})

    def set_leverage(self, market_id: str, leverage: int) -> LeverageUpdate:
        """``POST /account/leverage`` — set the leverage used for a market.

        Requires credentials. ``leverage`` is the integer multiplier (e.g. ``10``
        for 10x) and must be at least 1; the server rejects a value above the
        market's ceiling.

        Ahead of the pinned spec (a code-only op, like the Rust SDK), so it
        stays on the legacy gateway and is not listed in ``endpoints.txt``.
        """
        if not market_id:
            raise ValueError("market_id is required")
        if leverage < 1:
            raise ValueError("leverage must be at least 1")
        data = self._request(
            "POST",
            "/account/leverage",
            body={"market_id": market_id, "leverage": leverage},
            signed=True,
        )
        return LeverageUpdate.from_dict(data if isinstance(data, dict) else {})

    # -- orders (signed) -------------------------------------------------
    def create_order(self, order: OrderRequest) -> OrderResponse:
        """``POST /orders`` — place a single order. Requires credentials."""
        data = self._request("POST", "/orders", body=order.to_payload(), signed=True, direct=True)
        return OrderResponse.from_dict(data if isinstance(data, dict) else {})

    def create_orders(self, orders: list[OrderRequest]) -> list[BatchOrderResult]:
        """``POST /orders/batch`` — submit a batch of orders (sequential, non-atomic).

        Requires credentials. Returns one :class:`BatchOrderResult` per submitted
        order, in request order. The batch is non-atomic, so each entry
        independently reports either a placed order (``outcome == "ok"``) or a
        per-order rejection (``outcome == "err"``) — check ``result.is_ok`` /
        ``result.is_err`` on each entry.

        Positional alignment is preserved even for malformed payloads: a
        response element that is not an object decodes to an ``err``-shaped
        placeholder (``error == "malformed_result"``) rather than being
        dropped, and a payload that is not a list at all yields one such
        placeholder per submitted order — so ``zip(orders, results)`` is
        always safe.
        """
        body = [o.to_payload() for o in orders]
        data = self._request("POST", "/orders/batch", body=body, signed=True, direct=True)
        if not isinstance(data, list):
            # A non-list payload carries no per-order results to align; surface
            # one error-shaped entry per submitted order instead of returning [].
            return [BatchOrderResult.malformed(data) for _ in orders]
        return [
            BatchOrderResult.from_dict(r) if isinstance(r, dict) else BatchOrderResult.malformed(r)
            for r in data
        ]

    def fetch_open_orders(self) -> list[Order]:
        """``GET /orders`` — open orders for the account. Requires credentials."""
        data = self._request("GET", "/orders", signed=True, direct=True)
        return [Order.from_dict(o) for o in (data if isinstance(data, list) else [])]

    def fetch_order(self, order_id: str) -> Order:
        """``GET /orders/{order_id}`` — fetch a single order. Requires credentials.

        Stays on the legacy gateway: the ``/api/v1`` order-by-id route exposes
        only ``PATCH`` (amend) and ``DELETE`` (cancel); GET-by-id was not
        migrated to the direct service.
        """
        data = self._request("GET", f"/orders/{quote(order_id, safe='')}", signed=True)
        return Order.from_dict(data if isinstance(data, dict) else {})

    def cancel_order(self, order_id: str) -> Any:
        """``DELETE /orders/{order_id}`` — cancel a single order. Requires credentials."""
        return self._request(
            "DELETE", f"/orders/{quote(order_id, safe='')}", signed=True, direct=True
        )

    def cancel_all_orders(self) -> Any:
        """``DELETE /orders`` — cancel all open orders. Requires credentials."""
        return self._request("DELETE", "/orders", signed=True, direct=True)

    def amend_order(self, order_id: str, market_id: str, amend: AmendOrder) -> OrderResponse:
        """``PATCH /orders/{order_id}`` — amend a resting order's price/size.

        Requires credentials. ``market_id`` is required (the engine routes the
        amend by market, ENG-4645) and is sent as a query parameter, so it is
        part of the signed canonical string. ``amend`` must change at least one
        field; an empty amend raises :class:`ValueError` before any request.
        """
        if not market_id:
            raise ValueError("market_id is required")
        if not amend.has_changes():
            raise ValueError("amend_order requires at least one field to change")
        query = urlencode({"market_id": market_id})
        data = self._request(
            "PATCH",
            f"/orders/{quote(order_id, safe='')}",
            query=query,
            body=amend.to_payload(),
            signed=True,
            direct=True,
        )
        return OrderResponse.from_dict(data if isinstance(data, dict) else {})

    # -- keys / agents (signed) ------------------------------------------
    # None of the keys / agents / ws-token routes are in the /api/v1 spec yet,
    # so they stay on the legacy gateway (no ``direct=True``).
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
    # Admin/observability was intentionally excluded from the /api/v1 spec
    # (ENG-4748), so these stay on the legacy gateway.
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
        direct: bool = False,
    ) -> Any:
        # `direct` routes target the /api/v1 backend service at the host root;
        # everything else stays on the legacy gateway base. The /api/v1 prefix
        # is part of the signed canonical path, so resolve the full path *once*
        # and use the same value for both signing and the sent URL.
        base = self._direct_base_url if direct else self._base_url
        full_path = f"{API_V1_PREFIX}{path}" if direct else path

        body_bytes = b"" if body is None else json.dumps(body).encode()
        # Seed from the defaults (User-Agent + X-Nexus-Api-Version) so both ride
        # along on every request; copy so per-call headers stay local.
        headers: dict[str, str] = dict(self._default_headers)
        if body is not None:
            headers["content-type"] = "application/json"
        if signed:
            headers.update(self._sign(method, full_path, query, body_bytes))

        # Build the URL by hand so the signed query matches the sent query byte
        # for byte (no client-side re-encoding).
        url = f"{base}{full_path}"
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
