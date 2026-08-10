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
from collections.abc import Iterator
from decimal import Decimal
from importlib import metadata
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from ._parse import to_dict_list
from .auth import AgentRegistered, AgentRegistration, EthSigner, LoginResponse
from .errors import ApiError, MissingCredentialsError, TransportError
from .networks import Network, NetworkConfig, SigningDomain
from .pagination import NEXT_CURSOR_HEADER, Page, iter_items
from .types import (
    AccountFees,
    AccountPortfolioSummary,
    AccountState,
    AccountSummary,
    AdlEvent,
    AgentInfo,
    AmendOrder,
    ApiKeyInfo,
    BatchOrderResult,
    BridgeAssetsResponse,
    BridgeDeposit,
    BridgeDepositAddress,
    CancelOnDisconnectStatus,
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
    PortfolioHistory,
    PortfolioWindow,
    Position,
    RateLimitStatus,
    Ticker,
    TierOverride,
    Trade,
    Withdrawal,
    WsToken,
)

# `Network` and friends live in networks.py (one place for the host map, per
# ENG-6442) and are re-exported here so `from nexus_exchange.client import
# Network` keeps working.
__all__ = [
    "Client",
    "Network",
    "NetworkConfig",
    "SigningDomain",
    "DEFAULT_USER_AGENT",
    "DEFAULT_API_VERSION",
    "FILLS_LIMIT_MAX",
    "PORTFOLIO_LIMIT_MAX",
    "TRADES_LIMIT_MAX",
]

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
DEFAULT_API_VERSION = "v0.7.3"

DEFAULT_TIMEOUT = 30.0

#: Path prefix for the direct-service ("/api/v1") surface. Under the gateway
#: elimination (ENG-4740) each backend service exposes its own REST API under
#: this prefix, served at the host root rather than the ``/api/exchange``
#: gateway base. The HMAC signature is computed over the full request path
#: *including* this prefix (e.g. ``/api/v1/orders``), matching the server.
API_V1_PREFIX = "/api/v1"


#: Upper bound the spec puts on the portfolio-history ``limit`` parameter (the
#: largest window's point capacity, ``all`` = 366). Validated client-side so an
#: out-of-schema value fails fast instead of depending on server clamping.
#:
#: This bound belongs to ``/account/portfolio-history`` **only** — it is not a
#: fleet-wide list-endpoint cap, and that endpoint is not cursor-paginated. The
#: paginated endpoints carry their own, larger maxima; see
#: :data:`TRADES_LIMIT_MAX` / :data:`FILLS_LIMIT_MAX`.
PORTFOLIO_LIMIT_MAX = 366

#: Upper bound the spec puts on ``limit`` for ``GET /markets/{id}/trades``
#: (``maximum: 1000``, default 100).
TRADES_LIMIT_MAX = 1000

#: Upper bound the spec puts on ``limit`` for ``GET /fills`` (``maximum: 1000``,
#: default 100).
FILLS_LIMIT_MAX = 1000


def _portfolio_window(window: PortfolioWindow | str | None) -> str | None:
    """Validate a portfolio ``window`` and return its wire string.

    ``None`` means "omit the parameter" — the server defaults to ``day``. A
    value outside :class:`PortfolioWindow` raises :class:`ValueError` here
    rather than spending a signed request on a guaranteed ``400``
    (``invalid_window``).

    Always resolves through ``PortfolioWindow(...).value``: ``str()`` on a
    ``str``-mixin enum member renders ``"PortfolioWindow.DAY"``, and
    :func:`_query` stringifies whatever it is given — so passing a member
    straight through would put the repr on the wire (and into the signed
    canonical query).
    """
    if window is None:
        return None
    try:
        return PortfolioWindow(window).value
    except ValueError:
        allowed = ", ".join(w.value for w in PortfolioWindow)
        raise ValueError(f"window must be one of: {allowed} (got {window!r})") from None


def _portfolio_limit(limit: int | None) -> int | None:
    """Validate a portfolio-history ``limit`` against the spec's ``1..366`` range.

    ``minimum: 1, maximum: 366`` on the parameter schema is a constraint on the
    *request*, so a conforming client does not send outside it and this raises
    :class:`ValueError` before signing. The parameter description's "a larger
    value is clamped, not rejected" documents how the server *tolerates*
    non-conforming input; it is not licence to send it. Note the effective cap is
    per-window (``day`` 288, ``week`` 168, ``month`` 120, ``all`` 366), so a
    value inside ``1..366`` may still be clamped — asking for more than the
    window holds never returns more.

    Rejects ``bool`` explicitly — it is an ``int`` subclass, and letting it
    through would send ``limit=True``.
    """
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if not 1 <= limit <= PORTFOLIO_LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {PORTFOLIO_LIMIT_MAX} (got {limit})")
    return limit


def _decode_body(resp: httpx.Response) -> Any:
    """Decode a 2xx response body: parsed JSON, raw text, or ``None`` if empty."""
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _page_limit(limit: int | None, maximum: int, endpoint: str) -> int | None:
    """Validate a paginated list endpoint's ``limit`` against its spec maximum.

    ``maximum`` is a constraint on the *request*, so a conforming client does not
    send past it: this raises :class:`ValueError` before the request (and, on a
    signed route, before signing) rather than relying on the server to clamp.
    Each endpoint has its own maximum — they are **not** interchangeable (trades
    and fills 1000, orders/history 500, positions/closed 200, equity-history
    720) — so the bound is passed in per call site rather than shared.

    The lower bound is the SDK's own: the paginated endpoints declare no
    ``minimum``, but ``limit=0`` would return an empty page, which for a
    cursor-paginated endpoint reads as "no more results" and would silently end a
    walk at zero items. Rejecting it is friendlier than that.

    Rejects ``bool`` explicitly — it is an ``int`` subclass, and letting it
    through would send ``limit=True``.
    """
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if not 1 <= limit <= maximum:
        raise ValueError(f"{endpoint} limit must be between 1 and {maximum} (got {limit})")
    return limit


def _check_iter_args(limit: int | None, maximum: int, endpoint: str, max_pages: int | None) -> None:
    """Validate an ``iter_*`` call's own arguments **eagerly**, at call time.

    ``iter_*`` returns the generator built by :func:`iter_items`, and a generator
    function does not execute its body until the first ``next()``. So every
    caller-error check inside it — ``_page_limit`` via the ``fetch_page`` lambda,
    and ``iter_pages``' own ``max_pages`` bound — was deferred: an outright bad
    argument came back as a healthy-looking generator and only raised later, at a
    place in the caller's code that had nothing to do with the mistake.

    That also made the paired methods disagree about when a caller's own error
    surfaces, which is the part worth fixing (@Luc-Campos in review)::

        fetch_trades_page("BTC-USDX-PERP", limit=999_999)  # ValueError, at once
        iter_trades("BTC-USDX-PERP", limit=999_999)        # generator, no error

    A caller mistake is not a paging outcome, so it should not wait for paging to
    start. Server-side failures stay lazy — they belong to the request.

    Re-validating ``limit`` here means ``_page_limit`` runs twice per walk (once
    now, once inside the first ``fetch_page``). It is pure and cheap, and the
    alternative is threading a pre-validated value through the lambda, which
    would put the check somewhere a reader of ``fetch_*_page`` would not find it.
    """
    _page_limit(limit, maximum, endpoint)
    if max_pages is not None and max_pages < 0:
        raise ValueError(f"max_pages must be non-negative (got {max_pages})")


def _query(**params: Any) -> str:
    """Build a URL-encoded query string from non-``None`` params.

    Params are emitted in the order given so the signed canonical query and the
    sent query stay byte-for-byte identical (see :meth:`Client._request`).
    """
    items = [(k, str(v)) for k, v in params.items() if v is not None]
    return urlencode(items)


class Client:
    """Client for the Nexus Exchange REST API.

    Public market-data methods need no credentials. Pass ``api_key`` +
    ``api_secret`` (HMAC) to sign requests. Note the public gateway proxies
    signed calls to the *site* account; for per-account auth point ``base_url``
    at a direct gateway (e.g. ``Network.LOCAL``). See the README.

    ``network`` selects which network — whose money — the client talks to, and
    defaults to :attr:`Network.TESTNET` (play funds). One client targets exactly
    one network: credentials are minted per network and are invalid on any
    other, so never reuse a key, signature or agent registration across clients
    pointing at different networks.

    Routing targets two bases. The migrated market-data and account/trading
    surface is requested under an ``/api/v1`` prefix, which the client appends to
    :attr:`Network.direct_base_url`; routes not yet migrated go to
    :attr:`Network.base_url` unprefixed. On testnet both bases are the same
    ``/api/exchange`` value, because that deploy mounts ``/api/v1`` under the
    gateway rather than at the host root (measured — ENG-9200; the host-root form
    is answered by the web frontend with a 404 *HTML* page). Local runs the
    service with no gateway in front, so there both are the host root.

    A custom ``base_url`` overrides *both* unless ``direct_base_url`` is also
    given, which is usually what you want — including for the retired ``beta``
    channel, now a one-line override::

        Client(base_url="https://beta.exchange.nexus.xyz/api/exchange")

    Pass ``direct_base_url`` separately only for a deploy that answers ``/api/v1``
    somewhere else. Either way, do not include ``/api/v1`` in it: the client adds
    the prefix, and a base that already carries it is rejected at construction
    rather than left to sign a doubled path. That is the one difference from the
    TypeScript SDK's similarly-named ``baseUrl``, which *is* the direct base with
    the prefix baked in.

    Usable as a context manager::

        with Client() as client:
            markets = client.fetch_markets()
    """

    def __init__(
        self,
        network: Network = Network.TESTNET,
        *,
        base_url: str | None = None,
        direct_base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_version: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        # Normalize through the enum so a bare string ("mainnet") is accepted and
        # anything unrecognized — including the retired "stable"/"beta" channels
        # — raises here rather than silently targeting the wrong network.
        network = Network(network)
        self._network = network
        # A caller-supplied base_url overrides the network default; direct_base_url
        # falls back to base_url so a single override still covers both surfaces
        # (the local/direct-gateway case) while a deploy that keeps the gateway
        # split can set them apart.
        self._base_url = self._resolve_base(network, base_url, network.base_url, "base_url")
        self._direct_base_url = self._resolve_base(
            network,
            direct_base_url or base_url,
            network.direct_base_url,
            "direct_base_url",
        )
        # A base that already carries /api/v1 would be prefixed a second time, and
        # getting it wrong is silent: the bad path is signed as well as sent.
        self._reject_prefixed_direct_base(self._direct_base_url)
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

    @staticmethod
    def _resolve_base(
        network: Network, override: str | None, default: str | None, param: str
    ) -> str:
        """Pick the base URL for one surface, or explain why there isn't one.

        A network whose default is ``None`` has no published, reachable target
        yet — mainnet today. Rather than invent a host, or fall back to another
        network's (which for mainnet would mean sending real-funds traffic to
        testnet), this refuses at construction time with the override to pass.
        Failing here beats failing at request time, after an order is built.

        A blank override falls back to the network default, matching how a blank
        ``api_version`` is treated. An override that survives stripping but is
        empty once trailing slashes go (``"/"``) is rejected outright: a client
        with an empty base silently issues relative requests.
        """
        base = (override or "").strip() or default
        if base is None:
            raise ValueError(
                f"{network.label} has no default {param} yet: its host "
                f"({network.config.published_rest_base}) is published but not "
                f"resolvable, so this SDK will not guess one for a real-funds "
                f"network. Pass {param}=... explicitly to target it."
            )
        base = base.rstrip("/")
        if not base:
            raise ValueError(f"{param} must be a non-empty URL")
        return base

    @staticmethod
    def _reject_prefixed_direct_base(base: str) -> None:
        """Refuse a direct base that already ends in :data:`API_V1_PREFIX`.

        This client appends the prefix per request, so a base that already carries
        it produces ``/api/v1/api/v1/orders`` — signed over that same wrong path,
        so it reads as an auth failure rather than a misconfiguration. There is no
        deploy for which double-prefixing is right.

        Worth a guard and not just the docs, because the value a caller is most
        likely to have on hand *does* include the prefix: the TypeScript SDK's
        ``baseUrl`` is the direct base with ``/api/v1`` already in it, and the
        README's cross-SDK table exists precisely because that paste happens.

        This replaced a guard that refused an ``/api/exchange`` base here
        (ENG-9200). That guard encoded the assumption that the direct surface is
        served at the host root — which the live testnet deploy contradicts: it
        mounts ``/api/v1`` *under* the gateway, so the guard rejected the one
        value that works and left no way to configure the client correctly. Where
        the mount lives is per-deploy and is not this SDK's business to police; a
        base prefixed twice is wrong on any deploy, so that is what is checked.

        Raises rather than stripping: this base is where signed requests go, and
        silently retargeting them is not this SDK's call to make.
        """
        # Compare path segments, not a substring, so a host or query happening to
        # contain the words is not mistaken for the prefix.
        segments = [s for s in urlsplit(base).path.split("/") if s]
        prefix_segments = [s for s in API_V1_PREFIX.split("/") if s]
        if segments[-len(prefix_segments) :] != prefix_segments:
            return
        trimmed = base[: base.rindex(API_V1_PREFIX)] or "/"
        raise ValueError(
            f"direct_base_url must not already include {API_V1_PREFIX!r} (got {base!r}): "
            f"this client appends it per request, so the path sent — and signed — would "
            f"be '{urlsplit(base).path}{API_V1_PREFIX}/…'. Pass "
            f"direct_base_url={trimmed!r} instead. Note this differs from the "
            f"TypeScript SDK's `baseUrl`, which has the prefix baked in."
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

    @property
    def network(self) -> Network:
        """The network this client targets. Fixed for the client's lifetime —
        credentials are per-network, so switching means a new client."""
        return self._network

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
        """``GET /markets/{market_id}/trades`` — recent public trades (newest first).

        Returns the first page only. ``limit`` bounds it and must fall in
        ``1..1000`` (:data:`TRADES_LIMIT_MAX`); omit it for the server's default
        of 100. For more than one page use :meth:`iter_trades` (every trade) or
        :meth:`fetch_trades_page` (one page plus its cursor).
        """
        return self.fetch_trades_page(market_id, limit=limit).items

    def fetch_trades_page(
        self,
        market_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Trade]:
        """``GET /markets/{market_id}/trades`` — one page, plus its next cursor.

        The manual-paging form: :attr:`Page.next_cursor` is the value to pass
        back as ``cursor`` for the following page, and is ``None`` on the last
        page. Use this when the cursor must outlive the process (a resumable
        backfill); use :meth:`iter_trades` to just walk everything.
        """
        query = _query(
            limit=_page_limit(limit, TRADES_LIMIT_MAX, "trades"),
            cursor=cursor,
        )
        data, next_cursor = self._request_page(
            f"/markets/{quote(market_id, safe='')}/trades", query=query, direct=True
        )
        rows = data if isinstance(data, list) else []
        return Page([Trade.from_dict(t) for t in rows], next_cursor)

    def iter_trades(
        self,
        market_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[Trade]:
        """Every public trade for ``market_id``, paging by cursor as needed.

        A generator: pages are fetched lazily, one request at a time, so breaking
        out of the loop stops the requests. ``limit`` sets the *page* size (not a
        total), ``cursor`` resumes from a saved position, and ``max_pages`` bounds
        the walk — worth setting on a market with a long trade history, since
        nothing else limits how far back this goes.

        See :mod:`nexus_exchange.pagination` for the termination rules (absent
        ``X-Next-Cursor`` ends the walk; a non-advancing cursor raises
        :class:`~nexus_exchange.PaginationError`).
        """
        _check_iter_args(limit, TRADES_LIMIT_MAX, "trades", max_pages)
        return iter_items(
            lambda c: self.fetch_trades_page(market_id, limit=limit, cursor=c),
            cursor=cursor,
            max_pages=max_pages,
        )

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
        """``GET /positions`` — open positions. Requires credentials.

        As of API spec v0.7.2 each :class:`Position` also carries per-position
        risk detail (notional value, ROE, margin used, max leverage, funding
        paid). Those fields are ``None`` when the server cannot derive them,
        with the reason in the companion ``*_error`` — see :class:`Position`.

        A non-object in the list raises :class:`~nexus_exchange.DecodeError`
        rather than being skipped, so this list, ``fetch_balance().positions``
        and ``fetch_account_state().positions`` all fail the same way instead of
        one of them silently understating exposure.
        """
        data = self._request("GET", "/positions", signed=True, direct=True)
        return [Position.from_dict(p) for p in to_dict_list(data, "positions", required=False)]

    def fetch_account_state(self) -> AccountState:
        """``GET /account/state`` — consolidated account snapshot. Requires credentials.

        One signed call for the portfolio summary aggregates *and* every open
        position, replacing a ``/account/summary`` + ``/positions`` pair. Both
        halves come from one coherent server-side read, so
        ``state.summary.open_positions_count == len(state.positions)``.

        ``state.summary.withdrawable`` is the authoritative wallet-withdrawable
        balance. This endpoint **fails closed**: when the engine-authoritative
        margin view is unavailable it returns HTTP 502
        (``authoritative_margin_unavailable``, raised as :class:`ApiError`)
        rather than a locally-estimated figure. That is transient — retry after
        a short delay rather than falling back to a self-computed number.

        Use :meth:`fetch_account_summary` when only the aggregates are needed —
        it returns the same ``summary`` without the position list.
        """
        data = self._request("GET", "/account/state", signed=True, direct=True)
        return AccountState.from_dict(data if isinstance(data, dict) else {})

    def fetch_account_summary(self) -> AccountPortfolioSummary:
        """``GET /account/summary`` — aggregate portfolio view. Requires credentials.

        The summary-only half of :meth:`fetch_account_state`: equity, PnL,
        volume, margin and ``withdrawable``, without the position list. Prefer it
        when the aggregates are all you need; prefer
        :meth:`fetch_account_state` when you also want positions, since that is
        one coherent read rather than two calls that can straddle a fill.

        Distinct from :meth:`fetch_balance` (``GET /account``), which is the
        balance/collateral view — see :class:`AccountPortfolioSummary` versus
        :class:`AccountSummary`.

        Fails closed on the same HTTP 502 (``authoritative_margin_unavailable``,
        raised as :class:`ApiError`) as :meth:`fetch_account_state`, for the same
        reason: no locally-estimated ``withdrawable`` is ever substituted.
        """
        data = self._request("GET", "/account/summary", signed=True, direct=True)
        return AccountPortfolioSummary.from_dict(data if isinstance(data, dict) else {})

    def fetch_account_fees(self) -> AccountFees:
        """``GET /account/fees`` — the account's effective fee schedule.

        Requires credentials; the account is taken from the signing credentials,
        not a parameter. Returns the forward-looking *schedule* rate (maker /
        taker bps, fee tier, rolling 30-day volume, active discounts), not a
        realized per-fill average. ``maker_fee_bps`` may be negative — a rebate
        paid to the maker. See :class:`AccountFees` for the ``schedule``
        scoping caveat and the ``volume_30d_estimated`` flag.
        """
        data = self._request("GET", "/account/fees", signed=True, direct=True)
        return AccountFees.from_dict(data if isinstance(data, dict) else {})

    def fetch_portfolio_history(
        self,
        window: PortfolioWindow | str | None = None,
        limit: int | None = None,
    ) -> PortfolioHistory:
        """``GET /account/portfolio-history`` — portfolio time series.

        Requires credentials. Returns equity, cumulative trading PnL, and
        cumulative traded volume over ``window``, downsampled at a fixed
        per-window cadence, ``points`` **oldest first**.

        ``window`` takes a :class:`PortfolioWindow` member or its string value
        (``"day"`` / ``"week"`` / ``"month"`` / ``"all"``); omit it for the
        server's ``day`` default. ``limit`` caps the returned points and must
        fall in ``1..366``. Both are validated before the request, so a bad
        value raises :class:`ValueError` instead of costing a signed round trip
        (the server would answer ``400 invalid_window``). Each window has its
        own point capacity — ``limit`` above it is capped server-side, so asking
        for more never yields more.

        A malformed *response* raises :class:`~nexus_exchange.DecodeError`
        instead, so caller error and server-payload defects are distinguishable
        by type; every field of this response is spec-``required`` and none is
        defaulted. See :class:`PortfolioHistory`.
        """
        query = _query(
            window=_portfolio_window(window),
            limit=_portfolio_limit(limit),
        )
        data = self._request(
            "GET", "/account/portfolio-history", query=query, signed=True, direct=True
        )
        return PortfolioHistory.from_dict(data if isinstance(data, dict) else {})

    def fetch_my_trades(self, limit: int | None = None) -> list[Fill]:
        """``GET /fills`` — recent fills (private executions). Requires credentials.

        Returns the first page only. ``limit`` bounds it and must fall in
        ``1..1000`` (:data:`FILLS_LIMIT_MAX`); omit it for the server's default of
        100. For the full history use :meth:`iter_my_trades` (every fill) or
        :meth:`fetch_my_trades_page` (one page plus its cursor).
        """
        return self.fetch_my_trades_page(limit=limit).items

    def fetch_my_trades_page(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Fill]:
        """``GET /fills`` — one page of fills, plus its next cursor.

        The manual-paging form; see :meth:`fetch_trades_page`. Requires
        credentials.
        """
        query = _query(
            limit=_page_limit(limit, FILLS_LIMIT_MAX, "fills"),
            cursor=cursor,
        )
        data, next_cursor = self._request_page("/fills", query=query, signed=True, direct=True)
        rows = data if isinstance(data, list) else []
        return Page([Fill.from_dict(f) for f in rows], next_cursor)

    def iter_my_trades(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[Fill]:
        """Every fill on the account, paging by cursor as needed.

        A generator; each page costs one signed request, issued lazily. ``limit``
        sets the page size, ``cursor`` resumes from a saved position, and
        ``max_pages`` bounds the walk. Requires credentials.

        See :mod:`nexus_exchange.pagination` for the termination rules.
        """
        _check_iter_args(limit, FILLS_LIMIT_MAX, "fills", max_pages)
        return iter_items(
            lambda c: self.fetch_my_trades_page(limit=limit, cursor=c),
            cursor=cursor,
            max_pages=max_pages,
        )

    def fetch_withdrawals(self) -> list[Withdrawal]:
        """``GET /withdrawals`` — withdrawal history. Requires credentials."""
        data = self._request("GET", "/withdrawals", signed=True)
        return [Withdrawal.from_dict(w) for w in (data if isinstance(data, list) else [])]

    # -- bridge (deposits) ---------------------------------------------------

    def fetch_bridge_assets(self) -> BridgeAssetsResponse:
        """``GET /bridge/assets`` — bridgeable chains and assets. Requires credentials."""
        data = self._request("GET", "/bridge/assets", signed=True, direct=True)
        return BridgeAssetsResponse.from_dict(data if isinstance(data, dict) else {})

    def create_bridge_deposit_address(self, chain: str) -> BridgeDepositAddress:
        """``POST /bridge/deposit-addresses`` — get-or-create the account's deposit
        address on ``chain`` (idempotent per account+chain). Requires credentials.
        """
        data = self._request(
            "POST",
            "/bridge/deposit-addresses",
            body={"chain": chain},
            signed=True,
            direct=True,
        )
        return BridgeDepositAddress.from_dict(data if isinstance(data, dict) else {})

    def list_bridge_deposit_addresses(self) -> list[BridgeDepositAddress]:
        """``GET /bridge/deposit-addresses`` — list deposit addresses. Requires credentials."""
        data = self._request("GET", "/bridge/deposit-addresses", signed=True, direct=True)
        return [BridgeDepositAddress.from_dict(a) for a in (data if isinstance(data, list) else [])]

    def fetch_bridge_deposits(
        self,
        limit: int | None = None,
        chain: str | None = None,
        asset: str | None = None,
        status: str | None = None,
    ) -> list[BridgeDeposit]:
        """``GET /bridge/deposits`` — the account's bridge deposits; all filters
        optional. Poll a deposit until its ``status`` reaches ``credited``.
        Requires credentials.
        """
        query = _query(limit=limit, chain=chain, asset=asset, status=status)
        data = self._request("GET", "/bridge/deposits", query=query, signed=True, direct=True)
        return [BridgeDeposit.from_dict(x) for x in (data if isinstance(data, list) else [])]

    def fetch_bridge_deposit(self, deposit_id: str) -> BridgeDeposit:
        """``GET /bridge/deposits/{id}`` — a single bridge deposit. Requires credentials."""
        data = self._request(
            "GET",
            f"/bridge/deposits/{quote(deposit_id, safe='')}",
            signed=True,
            direct=True,
        )
        return BridgeDeposit.from_dict(data if isinstance(data, dict) else {})

    def fetch_rate_limit_status(self) -> RateLimitStatus:
        """``GET /account/rate-limit`` — the caller's rate-limit status.

        Requires credentials. Does not consume a rate-limit token.
        """
        data = self._request("GET", "/account/rate-limit", signed=True, direct=True)
        return RateLimitStatus.from_dict(data if isinstance(data, dict) else {})

    def fetch_cancel_on_disconnect(self) -> CancelOnDisconnectStatus:
        """``GET /account/cancel-on-disconnect`` — the account's COD state.

        Requires credentials. ``enabled`` is the account's own opt-in, while
        ``active`` is whether COD will actually fire (the opt-in *and* the
        exchange-side feature switch): ``enabled`` true with ``active`` false
        means the exchange has the feature switched off.
        """
        data = self._request("GET", "/account/cancel-on-disconnect", signed=True, direct=True)
        return CancelOnDisconnectStatus.from_dict(data if isinstance(data, dict) else {})

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

        Testnet and local only — the spec marks this operation
        ``x-nexus-network-availability: [testnet, local]``, and mainnet has no
        faucet because its collateral is USDX bridged from Ethereum Mainnet.
        Raises :class:`ValueError` on a faucet-less network rather than spending
        a signed request against a real-funds host.
        """
        if not self._network.has_faucet:
            raise ValueError(
                f"{self._network.label} has no faucet: `claim_credit` mints synthetic "
                f"funds and is testnet/local only. Real collateral is bridged — see "
                f"`deposit`."
            )
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

    def set_cancel_on_disconnect(self, enabled: bool) -> CancelOnDisconnectStatus:
        """``PUT /account/cancel-on-disconnect`` — opt the account in/out of COD.

        Requires credentials. Returns the updated status; note the returned
        ``active`` may stay false even when ``enabled`` is true if the exchange
        has the feature switched off (see :meth:`fetch_cancel_on_disconnect`).
        """
        data = self._request(
            "PUT",
            "/account/cancel-on-disconnect",
            body={"enabled": enabled},
            signed=True,
            direct=True,
        )
        return CancelOnDisconnectStatus.from_dict(data if isinstance(data, dict) else {})

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

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        body: Any | None = None,
        signed: bool = False,
        direct: bool = False,
    ) -> httpx.Response:
        """Issue one request and return the raw 2xx response.

        Split out of :meth:`_request` so the paginated readers can see the
        ``X-Next-Cursor`` *header* as well as the body — signing, routing, and
        error mapping stay in one place for every caller.
        """
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

        return resp

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
        resp = self._send(method, path, query=query, body=body, signed=signed, direct=direct)
        return _decode_body(resp)

    def _request_page(
        self,
        path: str,
        *,
        query: str = "",
        signed: bool = False,
        direct: bool = False,
    ) -> tuple[Any, str | None]:
        """``GET`` one page of a cursor-paginated list endpoint.

        Returns the decoded body alongside the ``X-Next-Cursor`` header, or
        ``None`` for that cursor when the header is absent — which per the spec
        means "this was the last page", not a failure. A present-but-empty header
        is treated as absent: an empty cursor cannot be sent back, so passing it
        on would re-request the first page forever.
        """
        resp = self._send("GET", path, query=query, signed=signed, direct=direct)
        cursor = (resp.headers.get(NEXT_CURSOR_HEADER) or "").strip() or None
        return _decode_body(resp), cursor
