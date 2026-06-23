"""Synchronous HTTP client for the Nexus Exchange API.

A thin wrapper mirroring the Rust SDK: typed methods over the REST routes, HMAC
request signing, one error hierarchy. **Experimental** — coverage trails the
Rust SDK and grows incrementally (see the README's support table). Public
market-data reads need no credentials; the authenticated account and trading
methods sign every request via the shared plumbing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx

from .errors import ApiError, InvalidRequestError, MissingCredentialsError, TransportError
from .types import Account, Fill, Market, Order, Position, Ticker

__all__ = ["Client", "Network", "DEFAULT_USER_AGENT"]

#: Identifies Python-SDK traffic in the exchange's per-client usage metrics.
DEFAULT_USER_AGENT = "nexus-exchange-py/0.1.0"
DEFAULT_TIMEOUT = 30.0


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
        """``GET /markets/summary`` — list all markets."""
        data = self._request("GET", "/markets/summary")
        rows = data if isinstance(data, list) else data.get("markets", [])
        return [Market.from_dict(m) for m in rows]

    def fetch_ticker(self, market_id: str) -> Ticker:
        """``GET /markets/{market_id}/ticker`` — latest ticker for one market."""
        data = self._request("GET", f"/markets/{quote(market_id, safe='')}/ticker")
        return Ticker.from_dict(market_id, data if isinstance(data, dict) else {"value": data})

    def health_check(self) -> dict[str, Any]:
        """``GET /health`` — gateway health."""
        data = self._request("GET", "/health")
        return data if isinstance(data, dict) else {"status": data}

    # -- account & positions (signed) ------------------------------------
    def fetch_account(self) -> Account:
        """``GET /account`` (signed) — balance and collateral summary."""
        data = self._request("GET", "/account", signed=True)
        return Account.from_dict(data if isinstance(data, dict) else {})

    def fetch_positions(self) -> list[Position]:
        """``GET /positions`` (signed) — open positions for the account."""
        data = self._request("GET", "/positions", signed=True)
        rows = data if isinstance(data, list) else data.get("positions", [])
        return [Position.from_dict(p) for p in rows]

    def fetch_fills(self) -> list[Fill]:
        """``GET /fills`` (signed) — recent private trade executions."""
        data = self._request("GET", "/fills", signed=True)
        rows = data if isinstance(data, list) else data.get("fills", [])
        return [Fill.from_dict(f) for f in rows]

    # -- trading (signed) -------------------------------------------------
    def fetch_open_orders(self) -> list[Order]:
        """``GET /orders`` (signed) — open orders for the account."""
        data = self._request("GET", "/orders", signed=True)
        rows = data if isinstance(data, list) else data.get("orders", [])
        return [Order.from_dict(o) for o in rows]

    def place_order(
        self,
        market_id: str,
        side: str,
        quantity: str,
        *,
        order_type: str = "Limit",
        price: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """``POST /orders`` (signed) — place a single order.

        Mirrors the Rust SDK's request shape: ``side`` is ``Buy``/``Sell``,
        ``order_type`` is ``Limit``/``Market``, ``time_in_force`` is
        ``GTC``/``IOC``/``FOK``. Monetary fields (``price``, ``quantity``) are
        decimal strings, matching the Rust SDK's wire encoding. A limit order
        without a ``price`` is rejected before sending.

        Defaults follow the Rust constructors: a market order uses ``IOC``, a
        limit order ``GTC``, when ``time_in_force`` is not given.
        """
        if not market_id:
            raise InvalidRequestError("market_id must not be empty")
        is_limit = order_type.lower() == "limit"
        if is_limit and price is None:
            raise InvalidRequestError("a limit order requires a price")
        if time_in_force is None:
            time_in_force = "GTC" if is_limit else "IOC"

        body: dict[str, Any] = {
            "market_id": market_id,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "time_in_force": time_in_force,
        }
        if price is not None:
            body["price"] = price
        if reduce_only is not None:
            body["reduce_only"] = reduce_only
        if client_order_id is not None:
            body["client_order_id"] = client_order_id

        data = self._request("POST", "/orders", body=body, signed=True)
        return data if isinstance(data, dict) else {"result": data}

    def cancel_order(self, order_id: str, market_id: str | None = None) -> dict[str, Any]:
        """``DELETE /orders/{order_id}`` (signed) — cancel one order.

        ``market_id``, when given, is sent as a query parameter (some gateways
        require it to route the cancel). It is included in the signed canonical
        string so ``signed === sent``.
        """
        if not order_id:
            raise InvalidRequestError("order_id must not be empty")
        query = f"market_id={quote(market_id, safe='')}" if market_id else ""
        path = f"/orders/{quote(order_id, safe='')}"
        data = self._request("DELETE", path, query=query, signed=True)
        return data if isinstance(data, dict) else {"result": data}

    def cancel_all(self) -> dict[str, Any]:
        """``DELETE /orders`` (signed) — cancel all open orders."""
        data = self._request("DELETE", "/orders", signed=True)
        return data if isinstance(data, dict) else {"result": data}

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
