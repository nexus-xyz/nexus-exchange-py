"""Unit tests for the async WebSocket streaming client (fake connection).

Mirrors the Rust/TS SDK ws clients (ENG-4045): op-envelope subscribe framing,
per-(channel, market) seq tracking, out_of_sync handling, reconnect with resume
(`since = last_seq`), per-connect token minting, and the ws:// + token guard.

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
