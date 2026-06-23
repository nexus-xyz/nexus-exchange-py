"""Synchronous HTTP client for the Nexus Exchange API.

A thin wrapper mirroring the Rust SDK: typed methods over the REST routes, HMAC
request signing, one error hierarchy. **Experimental** — only the public
market-data endpoints are implemented today (see the README's support table).
The request/signing plumbing already supports authenticated calls, but typed
account/trading methods are not built yet.
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

from .errors import ApiError, MissingCredentialsError, TransportError
from .types import Market, Ticker

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
