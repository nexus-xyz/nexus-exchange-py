"""Async WebSocket streaming client for the Nexus Exchange ``/ws`` endpoint.

Mirrors the Rust and TypeScript SDKs' streaming clients (ENG-4045):

- Opens a single WebSocket and multiplexes any number of ``subscribe()`` calls
  onto it, each surfaced as an ``async for`` iterator of :class:`WsEvent`.
- Tracks the highest ``seq`` per ``(channel, market)``. On disconnect it
  reconnects with jittered exponential backoff, re-mints a fresh single-use
  token (account-scoped streams), and re-subscribes from the last ``seq`` so the
  server replays anything missed from its ring buffer.
- Surfaces the server's ``out_of_sync`` gap signal (and a local drop-oldest
  sentinel under backpressure) so the consumer knows to REST-refetch.

Public market-data channels (``book`` / ``trades`` / ``candles``) need no auth.
Account-scoped channels (``orders`` / ``fills`` / ``positions`` / ``balances``)
require a short-lived token minted via ``POST /ws-tokens`` — supply a
``token_provider`` (e.g. wrapping :meth:`Client.mint_web_socket_token`).

The wire protocol is the op-envelope shared by all SDKs: outbound
``{"op":"subscribe","channel",...,"since"?}``; inbound frames tagged by ``op``
(``event`` / ``out_of_sync`` / ``subscribed`` / ``unsubscribed`` / ``error``).

Requires the optional ``websockets`` dependency: ``pip install nexus-exchange[ws]``
(or pass a custom ``connect`` coroutine).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from .errors import NexusExchangeError

__all__ = [
    "WsClient",
    "WsEvent",
    "WsSubscription",
    "WsError",
    "PUBLIC_CHANNELS",
    "ACCOUNT_CHANNELS",
    "CHANNELS",
]

#: Public market-data channels — no authentication required.
PUBLIC_CHANNELS = frozenset({"book", "trades", "candles"})
#: Account-scoped channels — require a ``token_provider``.
ACCOUNT_CHANNELS = frozenset({"orders", "fills", "positions", "balances"})
#: Every recognized channel.
CHANNELS = PUBLIC_CHANNELS | ACCOUNT_CHANNELS

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class WsError(NexusExchangeError):
    """A WebSocket configuration or usage error (e.g. a bad channel/url)."""


class WsConnection(Protocol):
    """Minimal async WebSocket connection the client drives.

    Matches the ``websockets`` library's connection; ``recv`` raises when the
    connection drops (any exception is treated as a disconnect → reconnect).
    """

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


#: Opens a connection to ``url`` (an authenticated ``wss://…?token=…`` URL).
Connect = Callable[[str], Awaitable[WsConnection]]
#: Supplies a fresh single-use token per (re)connect. Sync or async; a sync
#: callable is run in a thread so it never blocks the event loop.
TokenProvider = Callable[[], Awaitable[str]] | Callable[[], str]


@dataclass
class WsEvent:
    """A single event delivered to a subscription consumer."""

    channel: str
    market: str | None
    #: Server-assigned monotonic sequence per (channel, market).
    seq: int
    #: Opaque event payload, forwarded verbatim. ``None`` when ``out_of_sync``.
    data: Any
    #: Candle interval, echoed for candle events when present.
    interval: str | None = None
    #: True for a synthetic notice — not a real engine event — telling the
    #: consumer the stream lost continuity (server ring overran, or the local
    #: buffer dropped events under backpressure). Do a full REST refetch.
    out_of_sync: bool = False


@dataclass
class _Sub:
    key: str
    channel: str
    market: str | None
    interval: str | None
    last_seq: int = 0
    initial_since: int | None = None
    queue: deque[WsEvent] = field(default_factory=deque)
    waiters: list[asyncio.Future[WsEvent | None]] = field(default_factory=list)
    closed: bool = False


def _sub_key(channel: str, market: str | None) -> str:
    # Keyed by (channel, market) — matching the TS SDK. A single candle interval
    # per market is supported; a second subscribe for the same market replaces
    # the first.
    return f"{channel}|{market or ''}"


def _coerce_seq(value: Any) -> int | None:
    """Coerce an untrusted wire ``seq`` to a non-negative int, or ``None``.

    Accepts JSON numbers and decimal strings (u64 values past 2**53 arrive as
    strings and must survive intact).
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


class WsSubscription:
    """A live subscription. Iterate ``async for event in sub`` (or ``sub.events``)."""

    def __init__(self, client: WsClient, sub: _Sub) -> None:
        self._client = client
        self._sub = sub

    def __aiter__(self) -> AsyncIterator[WsEvent]:
        return self._client._iterate(self._sub)

    @property
    def events(self) -> AsyncIterator[WsEvent]:
        """The event stream as an async iterator (alias for iterating ``self``)."""
        return self._client._iterate(self._sub)

    def unsubscribe(self) -> None:
        """Tear down this subscription. Idempotent."""
        self._client._teardown_sub(self._sub)


class WsClient:
    """Multiplexing async WebSocket client for the Nexus Exchange streaming API.

    Use as an async context manager::

        async with WsClient(url) as ws:
            sub = ws.subscribe("trades", market="BTC-USDX-PERP")
            async for event in sub:
                print(event.seq, event.data)

    Each instance owns one socket; share it across your app rather than opening
    many. ``close()`` (or leaving the ``async with``) ends every subscription.
    """

    def __init__(
        self,
        url: str,
        *,
        path: str = "/ws",
        token_provider: TokenProvider | None = None,
        connect: Connect | None = None,
        max_queue: int = 1024,
        base_reconnect_delay: float = 0.25,
        max_reconnect_delay: float = 10.0,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss"):
            raise WsError(f"WebSocket url must be ws:// or wss://, got {url!r}")
        # Never send an auth token in cleartext to a remote host.
        if token_provider and parts.scheme == "ws" and parts.hostname not in _LOCAL_HOSTS:
            raise WsError(
                f"refusing to mint auth tokens over insecure ws:// to {parts.hostname}; use wss://"
            )
        if max_queue < 1:
            raise WsError("max_queue must be a positive integer")

        self._base = url.rstrip("/")
        self._path = path
        self._token_provider = token_provider
        self._connect = connect or _default_connect
        self._max_queue = max_queue
        self._base_delay = base_reconnect_delay
        self._max_delay = max_reconnect_delay

        self._subs: dict[str, _Sub] = {}
        self._sent_on_socket: set[str] = set()
        self._conn: WsConnection | None = None
        self._state = "closed"
        self._closing = False
        self._task: asyncio.Task[None] | None = None
        # Injectable so tests control timing/jitter without real waiting.
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._rand: Callable[[], float] = random.random

    # -- public API ------------------------------------------------------

    def status(self) -> str:
        """Connection state: ``connecting`` / ``open`` / ``reconnecting`` / ``closed``."""
        return self._state

    def subscribe(
        self,
        channel: str,
        *,
        market: str | None = None,
        interval: str | None = None,
        since: int | None = None,
    ) -> WsSubscription:
        """Subscribe to ``channel``; returns a :class:`WsSubscription` to iterate.

        Account-scoped channels require a ``token_provider`` (checked here).
        A second subscribe for the same ``(channel, market)`` replaces the first.
        """
        if self._closing:
            raise WsError("cannot subscribe on a closed WsClient")
        if channel not in CHANNELS:
            raise WsError(f"unknown channel: {channel!r}")
        if channel in ACCOUNT_CHANNELS and self._token_provider is None:
            raise WsError(f"channel {channel!r} is account-scoped and requires a token_provider")
        if since is not None and since < 0:
            raise WsError("since must be >= 0")

        key = _sub_key(channel, market)
        existing = self._subs.get(key)
        if existing is not None:
            self._teardown_sub(existing)

        sub = _Sub(
            key=key,
            channel=channel,
            market=market,
            interval=interval,
            initial_since=since,
        )
        self._subs[key] = sub

        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())
        elif self._conn is not None and self._state == "open":
            # Live socket: fire the subscribe now (best-effort; the run loop
            # re-sends on the next connect otherwise).
            asyncio.ensure_future(self._send_subscribe(self._conn, sub))

        return WsSubscription(self, sub)

    def close(self) -> None:
        """Close the connection and end every subscription. Idempotent."""
        if self._closing:
            return
        self._closing = True
        for sub in list(self._subs.values()):
            self._teardown_sub(sub)
        if self._task is not None:
            self._task.cancel()
        self._state = "closed"

    async def aclose(self) -> None:
        """Async close that also awaits the background task's teardown."""
        self.close()
        if self._task is not None:
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._conn is not None:
            await _safe_close(self._conn)
            self._conn = None

    async def __aenter__(self) -> WsClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- connection loop -------------------------------------------------

    async def _run(self) -> None:
        attempts = 0
        try:
            while not self._closing and self._subs:
                token = await self._mint_token()
                if self._token_provider is not None and not token:
                    attempts += 1
                    await self._backoff(attempts)
                    continue

                self._state = "reconnecting" if attempts > 0 else "connecting"
                try:
                    conn = await self._connect(self._build_url(token))
                except Exception:
                    attempts += 1
                    await self._backoff(attempts)
                    continue

                self._conn = conn
                self._sent_on_socket.clear()
                self._state = "open"
                attempts = 0
                for sub in list(self._subs.values()):
                    await self._send_subscribe(conn, sub)

                await self._recv_loop(conn)
                await _safe_close(conn)
                self._conn = None

                if self._closing or not self._subs:
                    break
                self._state = "reconnecting"
                attempts += 1
                await self._backoff(attempts)
        finally:
            self._state = "closed"

    async def _recv_loop(self, conn: WsConnection) -> None:
        while not self._closing and self._subs:
            try:
                raw = await conn.recv()
            except Exception:
                return  # connection dropped → reconnect
            try:
                self._handle(raw)
            except Exception:
                # A malformed-but-parseable frame must never crash the client.
                pass

    async def _mint_token(self) -> str | None:
        if self._token_provider is None:
            return None
        try:
            if inspect.iscoroutinefunction(self._token_provider):
                token = await self._token_provider()
            else:
                # A sync provider (e.g. wrapping the signed REST mint) runs in a
                # thread so its blocking I/O never stalls the event loop.
                token = await asyncio.to_thread(self._token_provider)
        except Exception:
            return None
        return token or None

    def _build_url(self, token: str | None) -> str:
        endpoint = f"{self._base}{self._path}"
        if not token:
            return endpoint
        sep = "&" if "?" in endpoint else "?"
        return f"{endpoint}{sep}token={quote(token, safe='')}"

    async def _backoff(self, attempts: int) -> None:
        ceiling = min(self._max_delay, self._base_delay * (2 ** (attempts - 1)))
        # Equal jitter: [ceiling/2, ceiling] — never zero (no busy-loop), yet
        # spread across clients to avoid a thundering herd.
        delay = ceiling / 2 + self._rand() * (ceiling / 2)
        await self._sleep(delay)

    # -- outbound ops ----------------------------------------------------

    async def _send_subscribe(self, conn: WsConnection, sub: _Sub) -> None:
        if sub.key in self._sent_on_socket or sub.closed:
            return
        # On reconnect, resume from last_seq; on a first subscribe use the
        # consumer's `since` if given, else live-from-now.
        since = sub.last_seq if sub.last_seq > 0 else sub.initial_since
        msg: dict[str, Any] = {"op": "subscribe", "channel": sub.channel}
        if sub.market is not None:
            msg["market"] = sub.market
        if sub.interval is not None:
            msg["interval"] = sub.interval
        if since is not None:
            msg["since"] = since
        try:
            await conn.send(json.dumps(msg))
            self._sent_on_socket.add(sub.key)
        except Exception:
            pass  # lost a race with close — the run loop reconnects

    async def _send_unsubscribe(self, sub: _Sub) -> None:
        conn = self._conn
        if conn is None:
            return
        msg: dict[str, Any] = {"op": "unsubscribe", "channel": sub.channel}
        if sub.market is not None:
            msg["market"] = sub.market
        try:
            await conn.send(json.dumps(msg))
        except Exception:
            pass

    # -- inbound routing -------------------------------------------------

    def _handle(self, raw: str | bytes) -> None:
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            return
        op = msg.get("op")
        channel = msg.get("channel")
        if op == "event":
            if channel not in CHANNELS:
                return
            market = msg.get("market") or None
            seq = _coerce_seq(msg.get("seq"))
            if seq is None:
                return
            sub = self._subs.get(_sub_key(channel, market))
            if sub is None or sub.closed or seq <= sub.last_seq:
                return  # drop duplicates / out-of-order (replay overlap)
            sub.last_seq = seq
            self._deliver(
                sub,
                WsEvent(channel, market, seq, msg.get("payload"), msg.get("interval")),
            )
        elif op == "out_of_sync":
            if channel not in CHANNELS:
                return
            market = msg.get("market") or None
            sub = self._subs.get(_sub_key(channel, market))
            if sub is None or sub.closed:
                return
            oldest = _coerce_seq(msg.get("oldest_seq"))
            sub.last_seq = oldest if oldest is not None else 0
            self._deliver(
                sub,
                WsEvent(channel, market, sub.last_seq, None, sub.interval, out_of_sync=True),
            )
        elif op == "subscribed":
            if channel not in CHANNELS:
                return
            market = msg.get("market") or None
            sub = self._subs.get(_sub_key(channel, market))
            seq_at_join = _coerce_seq(msg.get("seq_at_join"))
            if sub is not None and seq_at_join is not None:
                # Seed the resume baseline so a reconnect before any event still
                # resumes from the join point.
                sub.last_seq = max(sub.last_seq, seq_at_join)
        # unsubscribed / error / unknown: nothing to route.

    def _deliver(self, sub: _Sub, evt: WsEvent) -> None:
        if sub.closed:
            return
        while sub.waiters:
            fut = sub.waiters.pop(0)
            if not fut.done():
                fut.set_result(evt)
                return
        sub.queue.append(evt)
        if len(sub.queue) > self._max_queue:
            # Slow/absent consumer: keep the newest, drop the oldest, and leave
            # a single out_of_sync sentinel at the tail. Total stays bounded.
            while len(sub.queue) > self._max_queue - 1:
                sub.queue.popleft()
            sub.queue.append(
                WsEvent(sub.channel, sub.market, sub.last_seq, None, sub.interval, True)
            )

    async def _iterate(self, sub: _Sub) -> AsyncIterator[WsEvent]:
        try:
            while True:
                if sub.queue:
                    yield sub.queue.popleft()
                    continue
                if sub.closed:
                    return
                fut: asyncio.Future[WsEvent | None] = asyncio.get_event_loop().create_future()
                sub.waiters.append(fut)
                evt = await fut
                if evt is None:  # woken by teardown
                    return
                yield evt
        finally:
            # Consumer abandoned the iterator (break/return/throw): stop buffering.
            self._teardown_sub(sub)

    def _teardown_sub(self, sub: _Sub) -> None:
        if sub.closed:
            return
        sub.closed = True
        self._sent_on_socket.discard(sub.key)
        if self._subs.get(sub.key) is sub:
            del self._subs[sub.key]
        if self._conn is not None:
            asyncio.ensure_future(self._send_unsubscribe(sub))
        for fut in sub.waiters:
            if not fut.done():
                fut.set_result(None)
        sub.waiters.clear()
        sub.queue.clear()
        if not self._subs and not self._closing and self._task is not None:
            # Nothing left to keep the socket open for.
            self._task.cancel()
            self._state = "closed"


async def _safe_close(conn: WsConnection) -> None:
    try:
        await conn.close()
    except Exception:
        pass


async def _default_connect(url: str) -> WsConnection:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - exercised via error message
        raise WsError(
            "WebSocket support requires the 'websockets' package: "
            "pip install nexus-exchange[ws] (or pass connect=...)"
        ) from exc
    return await websockets.connect(url)
