"""Asyncio WebSocket streaming client (``GET /ws``).

A typed, reconnecting streaming client mirroring the Rust SDK's ``src/ws``
(the op-envelope protocol, the :class:`Channel` set, and its reliability
guarantees). It is the asyncio counterpart to the synchronous REST
:class:`~nexus_exchange.Client`: open a stream with
:meth:`~nexus_exchange.Client.stream`, then ``async for`` over the decoded
:class:`ServerMessage` frames.

Two failure modes sink most exchange WebSocket clients — a *fixed-sleep*
reconnect that stampedes the endpoint the moment it recovers, and an *unbounded*
internal queue that grows without limit when the consumer can't keep up. This
client closes both, exactly as the Rust SDK does:

* **Reconnect** uses capped exponential backoff with full jitter
  (:class:`Backoff`), so a fleet of clients spreads its retries rather than
  synchronizing on a fixed interval.
* **Delivery** flows through a *bounded* queue. When the consumer falls behind
  and the queue fills, the read loop drops excess frames rather than blocking —
  bounding memory *and* keeping the socket drained so server pings are always
  read and ponged. The number dropped is reported as a :class:`Lagged` item
  immediately before the next delivered message, not silently.

# Wire protocol

Every frame is a JSON object carrying an ``op`` discriminator. Outbound the
client sends ``subscribe`` / ``unsubscribe`` ops naming a :class:`Channel`;
inbound the server sends ``subscribed`` / ``unsubscribed`` acknowledgements,
``event`` data frames (forwarded verbatim — no order-book reconstruction),
``out_of_sync`` gap signals, and ``error`` frames, decoded into
:class:`ServerMessage`. Account channels are private and require a single-use
``/ws-tokens`` presented as a ``token=`` query parameter on the upgrade URL.

# Cursor resume

Each ``event`` carries a monotonic ``seq`` and each ``subscribed`` carries the
``seq_at_join`` the stream was at when the subscription took effect. The client
tracks the highest ``seq`` seen per channel and, on reconnect, replays each
``subscribe`` with a ``since`` cursor so the server resumes *after* the last
frame processed. An ``out_of_sync`` means the cursor predates the server's ring
buffer; the client drops that cursor (resuming from the live edge) and surfaces
the frame so the consumer can REST-refetch.

# Example

```python
import asyncio
from nexus_exchange import Client, Channel

async def main() -> None:
    client = Client(Network.LOCAL)
    async with client.stream([Channel.trades("BTC-USDX-PERP")]) as stream:
        async for msg in stream:
            print(msg)

asyncio.run(main())
```
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import connect as ws_connect

from .types import WsToken

__all__ = [
    "Channel",
    "ServerMessage",
    "Subscribed",
    "Unsubscribed",
    "Event",
    "OutOfSync",
    "ServerError",
    "EngineEnvelope",
    "Lagged",
    "Backoff",
    "WsStream",
    "StreamItem",
    "DEFAULT_WS_CHANNEL_CAPACITY",
]

#: Default bound on the buffered-event queue. Once this many items are buffered
#: ahead of a slow consumer, the read loop drops further frames (and reports the
#: gap as :class:`Lagged`) rather than buffering without limit.
DEFAULT_WS_CHANNEL_CAPACITY = 1024


# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Channel:
    """A channel to subscribe to over the streaming API.

    Public channels (``trades``, ``book``, ``candles``) are per-market and need
    no authentication. Account channels (``orders``, ``fills``, ``positions``,
    ``balances``) stream the authenticated account's private activity and require
    a minted ``/ws-tokens``. Construct via the classmethods rather than directly.
    Mirrors the Rust SDK's ``Channel`` enum.
    """

    name: str
    market: str | None = None
    interval: str | None = None

    _PUBLIC = ("trades", "book", "candles")
    _PRIVATE = ("orders", "fills", "positions", "balances")

    @classmethod
    def trades(cls, market: str) -> Channel:
        """Public trade prints for ``market`` (e.g. ``BTC-USDX-PERP``)."""
        return cls("trades", market=market)

    @classmethod
    def book(cls, market: str) -> Channel:
        """Public order-book updates for ``market`` (snapshots + deltas, verbatim)."""
        return cls("book", market=market)

    @classmethod
    def candles(cls, market: str, interval: str) -> Channel:
        """Public OHLCV candles for ``market`` at ``interval`` (e.g. ``1m``)."""
        return cls("candles", market=market, interval=interval)

    @classmethod
    def orders(cls) -> Channel:
        """The authenticated account's order lifecycle updates. Private."""
        return cls("orders")

    @classmethod
    def fills(cls) -> Channel:
        """The authenticated account's fills (private trade executions). Private."""
        return cls("fills")

    @classmethod
    def positions(cls) -> Channel:
        """The authenticated account's position updates. Private."""
        return cls("positions")

    @classmethod
    def balances(cls) -> Channel:
        """The authenticated account's balance updates. Private."""
        return cls("balances")

    @property
    def is_private(self) -> bool:
        """Whether this channel needs an authenticated (token-upgraded) connection."""
        return self.name in self._PRIVATE

    @property
    def key(self) -> tuple[str, str | None, str | None]:
        """Stable identity for cursor tracking and replay-set de-duplication."""
        return (self.name, self.market, self.interval)

    def _frame(self, op: str, since: int | None = None) -> str:
        """Serialize a ``subscribe`` / ``unsubscribe`` op, omitting absent fields."""
        frame: dict[str, Any] = {"op": op, "channel": self.name}
        if self.market is not None:
            frame["market"] = self.market
        if self.interval is not None:
            frame["interval"] = self.interval
        if since is not None and op == "subscribe":
            frame["since"] = since
        return json.dumps(frame)


# --------------------------------------------------------------------------
# Decoded inbound frames
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EngineEnvelope:
    """Engine-stamped gap metadata on an ``event`` frame (D-044).

    A jump in ``(epoch, sequence)`` tells a consumer the matching engine restarted
    or it missed engine output, independent of the per-stream ``seq`` cursor.
    """

    epoch: int = 0
    sequence: int = 0
    emitted_at: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EngineEnvelope:
        return cls(
            epoch=int(d.get("epoch", 0)),
            sequence=int(d.get("sequence", 0)),
            emitted_at=int(d.get("emitted_at", 0)),
        )


@dataclass(frozen=True)
class Subscribed:
    """Acknowledges that a subscription is active.

    ``seq_at_join`` is the stream's sequence at the moment the subscription took
    effect — the baseline the client resumes from if the connection drops.
    """

    channel: str
    seq_at_join: int
    market: str | None = None
    interval: str | None = None


@dataclass(frozen=True)
class Unsubscribed:
    """Acknowledges that a subscription was removed."""

    channel: str
    market: str | None = None
    interval: str | None = None


@dataclass(frozen=True)
class Event:
    """A data frame for a subscribed channel.

    ``seq`` is a monotonic per-stream sequence; ``payload`` is forwarded verbatim
    (no client-side reconstruction).
    """

    channel: str
    seq: int
    payload: Any
    market: str | None = None
    interval: str | None = None
    engine_envelope: EngineEnvelope | None = None


@dataclass(frozen=True)
class OutOfSync:
    """The resume cursor predates the server's ring buffer — a real gap.

    Non-fatal: the connection stays up and the client drops this channel's cursor.
    The consumer should REST-refetch current state and treat the stream as resumed
    from now.
    """

    channel: str
    market: str | None = None
    interval: str | None = None
    oldest_seq: int | None = None


@dataclass(frozen=True)
class ServerError:
    """A protocol-level error reported by the server (e.g. a bad subscription).

    A normal, non-fatal frame — the connection stays up.
    """

    message: str | None = None


#: A decoded inbound op-envelope frame.
ServerMessage = Subscribed | Unsubscribed | Event | OutOfSync | ServerError


@dataclass(frozen=True)
class Lagged:
    """The consumer fell behind and ``dropped`` frames were discarded.

    Yielded in order, immediately before the next delivered :class:`ServerMessage`,
    so a consumer always knows when and how much it missed. A gap signal, not a
    fatal error — the stream continues.
    """

    dropped: int


#: An item yielded by a :class:`WsStream`: a decoded frame or a lag marker.
StreamItem = ServerMessage | Lagged


def _decode(text: str) -> ServerMessage | None:
    """Decode one op-envelope frame, or ``None`` for unknown / unparseable frames.

    An unknown future ``op`` or a non-JSON frame is skipped rather than tearing
    down an otherwise healthy connection (matches the Rust client).
    """
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    op = d.get("op")
    if op == "subscribed":
        return Subscribed(
            channel=str(d.get("channel", "")),
            seq_at_join=int(d.get("seq_at_join", 0)),
            market=d.get("market"),
            interval=d.get("interval"),
        )
    if op == "unsubscribed":
        return Unsubscribed(
            channel=str(d.get("channel", "")),
            market=d.get("market"),
            interval=d.get("interval"),
        )
    if op == "event":
        env = d.get("engine_envelope")
        return Event(
            channel=str(d.get("channel", "")),
            seq=int(d.get("seq", 0)),
            payload=d.get("payload"),
            market=d.get("market"),
            interval=d.get("interval"),
            engine_envelope=EngineEnvelope.from_dict(env) if isinstance(env, dict) else None,
        )
    if op == "out_of_sync":
        return OutOfSync(
            channel=str(d.get("channel", "")),
            market=d.get("market"),
            interval=d.get("interval"),
            oldest_seq=d.get("oldest_seq"),
        )
    if op == "error":
        return ServerError(message=d.get("message"))
    return None


def _cursor_advance(msg: ServerMessage) -> tuple[tuple[str, str | None, str | None], int] | None:
    """The ``(key, seq)`` this frame folds into the cursor map, if any."""
    if isinstance(msg, Event):
        return ((msg.channel, msg.market, msg.interval), msg.seq)
    if isinstance(msg, Subscribed):
        return ((msg.channel, msg.market, msg.interval), msg.seq_at_join)
    return None


def _cursor_reset(msg: ServerMessage) -> tuple[str, str | None, str | None] | None:
    """The cursor key this frame *invalidates* (``out_of_sync``), if any."""
    if isinstance(msg, OutOfSync):
        return (msg.channel, msg.market, msg.interval)
    return None


# --------------------------------------------------------------------------
# Reconnect backoff: exponential growth with full jitter
# --------------------------------------------------------------------------
@dataclass
class Backoff:
    """Reconnect backoff policy: exponential growth with full jitter.

    A fixed reconnect sleep makes every client wake on the same cadence and
    stampede the endpoint the instant it recovers. This uses capped exponential
    backoff plus *full jitter*: each delay is a uniform draw from
    ``[0, ceiling]``, spacing out a single client's retries and decorrelating a
    fleet. Mirrors the Rust SDK's ``Backoff``.
    """

    initial: float = 0.5
    max: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True

    def iter(self) -> _BackoffIter:
        """Begin an independent sequence of delays."""
        return _BackoffIter(self)


class _BackoffIter:
    """A live sequence of reconnect delays produced from a :class:`Backoff`."""

    def __init__(self, policy: Backoff) -> None:
        self._policy = policy
        self._ceiling = policy.initial

    def next_delay(self) -> float:
        """Produce the next delay (seconds) and advance the exponential ceiling."""
        ceiling = min(self._ceiling, self._policy.max)
        delay = random.uniform(0.0, ceiling) if self._policy.jitter else ceiling
        self._ceiling = min(ceiling * max(self._policy.multiplier, 1.0), self._policy.max)
        return delay

    def reset(self) -> None:
        """Reset to the initial ceiling — call after a connection carries a frame."""
        self._ceiling = self._policy.initial


def _with_token(ws_url: str, token: str) -> str:
    """Append ``token`` to ``ws_url`` as an encoded ``token=`` query parameter."""
    sep = "&" if "?" in ws_url else "?"
    return f"{ws_url}{sep}{urlencode({'token': token})}"


# A sentinel pushed onto the internal queue to wake the consumer when the
# background task ends, so iteration terminates promptly on close().
_CLOSED = object()


# --------------------------------------------------------------------------
# The streaming client
# --------------------------------------------------------------------------
class WsStream:
    """A live, typed, asyncio subscription to the streaming API.

    Created by :meth:`~nexus_exchange.Client.stream`. ``async for`` over it to
    pull decoded :class:`ServerMessage` frames (and :class:`Lagged` gap markers).
    A background task owns the socket, reconnects transparently with jittered
    backoff, ponges heartbeats, re-mints the ``/ws-tokens`` for private streams,
    and resumes each channel from its cursor. Use as an async context manager, or
    call :meth:`close` to shut the task down.

    Connecting starts lazily on the first iteration (or :meth:`start`), so
    constructing a stream needs no running event loop.
    """

    def __init__(
        self,
        *,
        ws_url: str,
        channels: Sequence[Channel],
        mint_token: Callable[[], WsToken],
        user_agent: str,
        backoff: Backoff,
        channel_capacity: int = DEFAULT_WS_CHANNEL_CAPACITY,
    ) -> None:
        self._ws_url = ws_url
        self._channels: list[Channel] = list(channels)
        self._mint_token = mint_token
        self._user_agent = user_agent
        self._backoff = backoff
        self._capacity = max(1, channel_capacity)
        # Bounded queue: the read loop never blocks on it (see _deliver).
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._capacity)
        self._cursors: dict[tuple[str, str | None, str | None], int] = {}
        self._dropped = 0
        self._task: asyncio.Task[None] | None = None
        self._closing = asyncio.Event()
        self._ws: Any = None  # the live connection, for command sends / close

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        """Spawn the background reconnect task (idempotent)."""
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def close(self) -> None:
        """Gracefully stop the background task and close the socket."""
        self._closing.set()
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 — best-effort close
                pass
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Unblock any consumer parked on the queue. A slow consumer may have left
        # the bounded queue full; closing must never raise.
        self._enqueue_closed()

    async def __aenter__(self) -> WsStream:
        self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def __aiter__(self) -> AsyncIterator[StreamItem]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[StreamItem]:
        self.start()
        while True:
            item = await self._queue.get()
            if item is _CLOSED:
                return
            yield item

    # -- subscription management -----------------------------------------
    async def subscribe(self, channel: Channel) -> None:
        """Add a channel: subscribe now (if connected) and on every reconnect.

        Subscribing to a channel already in the set is a no-op. A private channel
        added to a currently-public connection triggers a transparent reconnect so
        the client can mint a token and upgrade.
        """
        if any(c.key == channel.key for c in self._channels):
            return
        ws = self._ws
        authed = any(c.is_private for c in self._channels)
        self._channels.append(channel)
        if ws is not None and not (channel.is_private and not authed):
            since = self._cursors.get(channel.key)
            try:
                await ws.send(channel._frame("subscribe", since))
            except Exception:  # noqa: BLE001 — read loop will observe + reconnect
                pass
        elif channel.is_private and not authed and ws is not None:
            # Force a reconnect so the next attempt mints a token and upgrades.
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def unsubscribe(self, channel: Channel) -> None:
        """Remove a channel: unsubscribe on the wire and stop replaying it."""
        self._channels = [c for c in self._channels if c.key != channel.key]
        self._cursors.pop(channel.key, None)
        ws = self._ws
        if ws is not None:
            try:
                await ws.send(channel._frame("unsubscribe"))
            except Exception:  # noqa: BLE001
                pass

    # -- background reconnect loop ---------------------------------------
    async def _run(self) -> None:
        """Connect, serve until the socket drops or close is requested, back off, retry."""
        delays = self._backoff.iter()
        try:
            while not self._closing.is_set():
                authed = any(c.is_private for c in self._channels)
                try:
                    url = await self._connect_url(authed)
                except Exception as exc:  # noqa: BLE001 — surface mint failures, keep retrying
                    await self._emit(ServerError(message=f"ws token mint failed: {exc}"))
                    if await self._wait_backoff(delays):
                        return
                    continue

                delivered = await self._serve(url)
                if delivered:
                    delays.reset()
                if self._closing.is_set():
                    return
                if await self._wait_backoff(delays):
                    return
        finally:
            # Wake any consumer parked on the queue once the task ends.
            self._enqueue_closed()

    async def _connect_url(self, authed: bool) -> str:
        """Resolve the connect URL, minting a fresh single-use token when private."""
        if not authed:
            return self._ws_url
        # The token mint is a synchronous REST call; run it off the event loop so
        # it never blocks other tasks. A fresh token is minted per (re)connect
        # because tokens are single-use.
        token = await asyncio.to_thread(self._mint_token)
        return _with_token(self._ws_url, token.token)

    async def _serve(self, url: str) -> bool:
        """Drive one live connection until it drops or close is requested.

        Returns whether the connection carried at least one frame (so the caller
        can reset the backoff only on a genuinely healthy connection).
        """
        delivered = False
        try:
            # ``ping_interval`` drives keepalive; the library auto-ponges server
            # pings, so the read loop only handles data frames.
            async with ws_connect(
                url,
                additional_headers={"User-Agent": self._user_agent},
                ping_interval=20,
                ping_timeout=20,
                open_timeout=10,
            ) as ws:
                self._ws = ws
                # (Re)subscribe every channel, carrying its `since` cursor.
                for channel in self._channels:
                    since = self._cursors.get(channel.key)
                    await ws.send(channel._frame("subscribe", since))

                async for raw in ws:
                    text = raw.decode() if isinstance(raw, bytes) else raw
                    msg = _decode(text)
                    if msg is None:
                        continue
                    advance = _cursor_advance(msg)
                    if advance is not None:
                        key, seq = advance
                        self._cursors[key] = max(self._cursors.get(key, 0), seq)
                    reset = _cursor_reset(msg)
                    if reset is not None:
                        self._cursors.pop(reset, None)
                    if self._deliver(msg):
                        delivered = True
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — transient connect/read failure: reconnect transparently
            pass
        finally:
            self._ws = None
        return delivered

    async def _wait_backoff(self, delays: _BackoffIter) -> bool:
        """Wait out one backoff delay. Returns ``True`` if close was requested."""
        try:
            await asyncio.wait_for(self._closing.wait(), timeout=delays.next_delay())
            return True  # close fired before the delay elapsed
        except asyncio.TimeoutError:
            return False

    # -- delivery ---------------------------------------------------------
    def _deliver(self, msg: ServerMessage) -> bool:
        """Forward a frame without blocking the read loop.

        On a full queue the frame is dropped and counted; the count is flushed as
        a :class:`Lagged` immediately before the next successfully delivered
        message, so the consumer sees gaps in order. Returns whether ``msg`` was
        delivered (vs dropped).
        """
        if self._dropped > 0:
            try:
                self._queue.put_nowait(Lagged(dropped=self._dropped))
                self._dropped = 0
            except asyncio.QueueFull:
                pass  # still no room; keep accumulating
        try:
            self._queue.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            return False

    def _enqueue_closed(self) -> None:
        """Enqueue the close sentinel without ever raising.

        A slow or stopped consumer can leave the bounded delivery queue full at
        shutdown. A bare ``put_nowait(_CLOSED)`` would then raise
        :class:`asyncio.QueueFull` out of ``close()`` / ``__aexit__`` / the
        background task's ``finally`` — closing must never raise. Drain one slot
        first so the sentinel always fits and a parked consumer is guaranteed to
        observe it; fall back to swallowing ``QueueFull`` if even that races.
        """
        try:
            self._queue.put_nowait(_CLOSED)
            return
        except asyncio.QueueFull:
            pass
        try:
            self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self._queue.put_nowait(_CLOSED)
        except asyncio.QueueFull:
            pass

    async def _emit(self, item: StreamItem) -> bool:
        """Best-effort deliver a one-off item (e.g. a surfaced error)."""
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False


# Re-export the underlying library's close-code exception for callers that want
# to distinguish a clean server close in their own handling.
ConnectionClosed = websockets.exceptions.ConnectionClosed
