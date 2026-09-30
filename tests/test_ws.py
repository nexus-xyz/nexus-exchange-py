"""Unit tests for the async WebSocket streaming client (fake connection).

Mirrors the Rust/TS SDK ws clients (ENG-4045): op-envelope subscribe framing,
per-(channel, market) seq tracking, out_of_sync handling and re-subscribe, reconnect
with resume (`since = last_seq`, 0 included), backoff reset after a frame, per-connect
token minting, and the ws:// + token guard.

A fake connection scripts inbound frames and records outbound sends, so no real
socket or `websockets` package is needed. Backoff sleep/jitter are stubbed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from nexus_exchange import WsClient, WsError


class _Closed(Exception):
    """Signals the fake connection dropped (→ client reconnects)."""


class FakeConn:
    """A scripted WebSocket connection.

    ``frames`` are delivered by ``recv()`` in order. When exhausted, ``recv``
    raises ``_Closed`` if ``close_after`` else blocks forever (idle open socket).
    """

    def __init__(self, frames: list[str], *, close_after: bool = False) -> None:
        self._frames = list(frames)
        self._close_after = close_after
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if self._frames:
            await asyncio.sleep(0)  # yield so sends/iteration interleave
            return self._frames.pop(0)
        if self._close_after:
            raise _Closed
        await asyncio.Event().wait()  # idle: block until cancelled
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


def _factory(conns: list[FakeConn]):
    """A connect() double returning each FakeConn in turn; records URLs."""
    urls: list[str] = []
    it = iter(conns)

    async def connect(url: str) -> FakeConn:
        urls.append(url)
        try:
            return next(it)
        except StopIteration:
            return FakeConn([])  # further reconnects idle-block

    return connect, urls


def _event(channel: str, market: str, seq: int, payload) -> str:
    return json.dumps(
        {"op": "event", "channel": channel, "market": market, "seq": seq, "payload": payload}
    )


def _instant(ws: WsClient) -> None:
    """Stub backoff so reconnect tests don't actually wait."""

    async def _sleep(_: float) -> None:
        await asyncio.sleep(0)

    ws._sleep = _sleep
    ws._rand = lambda: 0.5


async def _take(sub, n: int, timeout: float = 1.0) -> list:
    out: list = []

    async def _drain() -> None:
        async for e in sub:
            out.append(e)
            if len(out) >= n:
                return

    await asyncio.wait_for(_drain(), timeout)
    return out


# -- basics ------------------------------------------------------------------


async def test_subscribe_sends_op_envelope_and_delivers_events() -> None:
    conn = FakeConn(
        [
            _event("trades", "BTC-USDX-PERP", 1, {"price": "50000"}),
            _event("trades", "BTC-USDX-PERP", 2, {"price": "50010"}),
        ]
    )
    connect, _ = _factory([conn])
    async with WsClient("wss://x.test", connect=connect) as ws:
        sub = ws.subscribe("trades", market="BTC-USDX-PERP")
        events = await _take(sub, 2)
    assert [e.seq for e in events] == [1, 2]
    assert events[0].data == {"price": "50000"}
    assert conn.sent[0] == {"op": "subscribe", "channel": "trades", "market": "BTC-USDX-PERP"}


async def test_first_subscribe_includes_since() -> None:
    conn = FakeConn([_event("book", "ETH-USDX-PERP", 100, {})])
    connect, _ = _factory([conn])
    async with WsClient("wss://x.test", connect=connect) as ws:
        sub = ws.subscribe("book", market="ETH-USDX-PERP", since=42)
        await _take(sub, 1)
    assert conn.sent[0]["since"] == 42


async def test_out_of_order_and_duplicate_seqs_are_dropped() -> None:
    conn = FakeConn(
        [
            _event("trades", "BTC-USDX-PERP", 5, {"n": 5}),
            _event("trades", "BTC-USDX-PERP", 5, {"n": "dup"}),
            _event("trades", "BTC-USDX-PERP", 3, {"n": "old"}),
            _event("trades", "BTC-USDX-PERP", 6, {"n": 6}),
        ]
    )
    connect, _ = _factory([conn])
    async with WsClient("wss://x.test", connect=connect) as ws:
        sub = ws.subscribe("trades", market="BTC-USDX-PERP")
        events = await _take(sub, 2)
    assert [e.seq for e in events] == [5, 6]


async def test_out_of_sync_emits_sentinel_and_resets_cursor() -> None:
    conn = FakeConn(
        [
            _event("book", "BTC-USDX-PERP", 10, {}),
            json.dumps(
                {"op": "out_of_sync", "channel": "book", "market": "BTC-USDX-PERP", "oldest_seq": 3}
            ),
        ]
    )
    connect, _ = _factory([conn])
    async with WsClient("wss://x.test", connect=connect) as ws:
        sub = ws.subscribe("book", market="BTC-USDX-PERP")
        events = await _take(sub, 2)
    assert events[0].seq == 10 and not events[0].out_of_sync
    assert events[1].out_of_sync is True and events[1].data is None


def _frame(**fields) -> str:
    return json.dumps(fields)


async def test_out_of_sync_resubscribes_from_the_live_edge_on_an_open_socket() -> None:
    # The server ends a subscription it answers out_of_sync for (ENG-18683), so
    # the client must subscribe again on the same socket, without `since`.
    conn = FakeConn(
        [
            _event("trades", "BTC-USDX-PERP", 10, {}),
            _frame(op="out_of_sync", channel="trades", market="BTC-USDX-PERP", oldest_seq=3),
        ]
    )
    connect, _ = _factory([conn])
    ws = WsClient("wss://x.test", connect=connect)
    _instant(ws)
    async with ws:
        sub = ws.subscribe("trades", market="BTC-USDX-PERP")
        events = sub.events  # one iterator: leaving `_take` would tear the sub down

        async def _next():
            return await asyncio.wait_for(events.__anext__(), 1.0)

        assert (await _next()).seq == 10
        assert (await _next()).out_of_sync and sub.health == "resyncing"
        for _ in range(5):
            await asyncio.sleep(0)
        subscribes = [m for m in conn.sent if m["op"] == "subscribe"]
        assert len(subscribes) == 2 and "since" not in subscribes[1]
        # The ack is the authority for live.
        ack = _frame(op="subscribed", channel="trades", market="BTC-USDX-PERP", seq_at_join=40)
        ws._handle(ack)
        assert sub.health == "live"
        ws._handle(_event("trades", "BTC-USDX-PERP", 41, {"n": 41}))
        assert (await _next()).seq == 41


async def test_out_of_sync_without_a_market_resyncs_every_market_of_the_channel() -> None:
    conn = FakeConn([])
    connect, _ = _factory([conn])
    ws = WsClient("wss://x.test", connect=connect)
    async with ws:
        btc = ws.subscribe("book", market="BTC-USDX-PERP")
        eth = ws.subscribe("book", market="ETH-USDX-PERP")
        trades = ws.subscribe("trades", market="BTC-USDX-PERP")
        for _ in range(5):
            await asyncio.sleep(0)
        ws._handle(_frame(op="out_of_sync", channel="book", market=None, oldest_seq=None))
        for _ in range(5):
            await asyncio.sleep(0)
        assert (btc.health, eth.health, trades.health) == ("resyncing", "resyncing", "live")
        resent = [m.get("market") for m in conn.sent[3:] if m["op"] == "subscribe"]
    assert sorted(resent) == ["BTC-USDX-PERP", "ETH-USDX-PERP"]


async def test_out_of_sync_drops_the_cursor_instead_of_rewinding_to_oldest_seq() -> None:
    conn1 = FakeConn(
        [
            _event("trades", "BTC-USDX-PERP", 10, {}),
            _frame(op="out_of_sync", channel="trades", market="BTC-USDX-PERP", oldest_seq=3),
        ],
        close_after=True,
    )
    conn2 = FakeConn([])
    connect, _ = _factory([conn1, conn2])
    ws = WsClient("wss://x.test", connect=connect)
    _instant(ws)
    async with ws:
        sub = ws.subscribe("trades", market="BTC-USDX-PERP")
        await _take(sub, 2)
        for _ in range(10):
            await asyncio.sleep(0)
    resume = [m for m in conn2.sent if m["op"] == "subscribe"][0]
    assert "since" not in resume  # not since=3: that replays what the refetch covers


async def test_a_join_at_seq_zero_is_resumed_with_since_zero() -> None:
    conn1 = FakeConn(
        [_frame(op="subscribed", channel="fills", market=None, seq_at_join=0)], close_after=True
    )
    conn2 = FakeConn([])
    connect, _ = _factory([conn1, conn2])
    ws = WsClient("wss://x.test", connect=connect, token_provider=lambda: "tok")
    _instant(ws)
    async with ws:
        ws.subscribe("fills")
        for _ in range(20):
            await asyncio.sleep(0)
    resume = [m for m in conn2.sent if m["op"] == "subscribe"][0]
    assert resume["since"] == 0


async def test_backoff_keeps_growing_when_sockets_open_and_drop_without_a_frame() -> None:
    conns = [FakeConn([], close_after=True) for _ in range(3)]
    connect, _ = _factory(conns)
    ws = WsClient("wss://x.test", connect=connect)
    delays: list[float] = []

    async def _sleep(d: float) -> None:
        delays.append(d)
        await asyncio.sleep(0)

    ws._sleep = _sleep
    ws._rand = lambda: 0.5
    async with ws:
        ws.subscribe("trades", market="BTC-USDX-PERP")
        for _ in range(30):
            await asyncio.sleep(0)
    assert delays[:3] == sorted(delays[:3]) and delays[0] < delays[2]


async def test_backoff_resets_after_a_frame_arrives() -> None:
    conns = [
        FakeConn([], close_after=True),
        FakeConn([_event("trades", "BTC-USDX-PERP", 1, {})], close_after=True),
    ]
    connect, _ = _factory(conns)
    ws = WsClient("wss://x.test", connect=connect)
    delays: list[float] = []

    async def _sleep(d: float) -> None:
        delays.append(d)
        await asyncio.sleep(0)

    ws._sleep = _sleep
    ws._rand = lambda: 0.5
    async with ws:
        ws.subscribe("trades", market="BTC-USDX-PERP")
        for _ in range(30):
            await asyncio.sleep(0)
    # First drop had no frame (attempt 1), the second delivered one (reset to 1).
    assert delays[0] == delays[1]


# -- reconnect / resume ------------------------------------------------------


async def test_reconnect_resumes_from_last_seq_and_remints_token() -> None:
    conn1 = FakeConn([_event("trades", "BTC-USDX-PERP", 7, {})], close_after=True)
    conn2 = FakeConn([_event("trades", "BTC-USDX-PERP", 8, {})])
    connect, urls = _factory([conn1, conn2])

    tokens = iter(["tok1", "tok2"])
    ws = WsClient("wss://x.test", connect=connect, token_provider=lambda: next(tokens))
    _instant(ws)
    async with ws:
        # token_provider is set, so the client mints a fresh token per connect
        # even for this public channel — that's what we assert on below.
        sub = ws.subscribe("trades", market="BTC-USDX-PERP")
        events = await _take(sub, 2)

    assert [e.seq for e in events] == [7, 8]
    # Fresh single-use token per connect, presented as a query param.
    assert "token=tok1" in urls[0] and "token=tok2" in urls[1]
    # The resubscribe after reconnect resumes from the last delivered seq.
    resume = [m for m in conn2.sent if m.get("channel") == "trades"][0]
    assert resume["since"] == 7


# -- validation --------------------------------------------------------------


async def test_account_channel_without_token_provider_errors() -> None:
    async with WsClient("wss://x.test", connect=_factory([])[0]) as ws:
        with pytest.raises(WsError, match="account-scoped"):
            ws.subscribe("orders")


def test_ws_scheme_and_insecure_token_guard() -> None:
    with pytest.raises(WsError, match="ws:// or wss://"):
        WsClient("http://x.test")
    with pytest.raises(WsError, match="insecure ws://"):
        WsClient("ws://remote.test", token_provider=lambda: "t")
    # loopback ws:// with a token is allowed (local dev).
    WsClient("ws://localhost:9090", token_provider=lambda: "t")


async def test_unknown_channel_rejected() -> None:
    async with WsClient("wss://x.test", connect=_factory([])[0]) as ws:
        with pytest.raises(WsError, match="unknown channel"):
            ws.subscribe("nonsense")
