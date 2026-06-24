"""Tests for the asyncio WebSocket streaming client.

The socket is mocked: a fake ``connect`` yields a scripted sequence of frames so
we can assert subscribe framing, message decode, lagged-drop accounting, cursor
resume, and reconnect behaviour without a real server.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest

from nexus_exchange import (
    Backoff,
    Channel,
    Client,
    Event,
    Lagged,
    MissingCredentialsError,
    Network,
    OutOfSync,
    ServerError,
    Subscribed,
    Unsubscribed,
    WsStream,
    WsToken,
)
from nexus_exchange import ws as ws_mod


# --------------------------------------------------------------------------
# Channel framing
# --------------------------------------------------------------------------
def test_channel_classification_and_keys() -> None:
    assert Channel.orders().is_private
    assert Channel.fills().is_private
    assert not Channel.trades("BTC-USDX-PERP").is_private
    assert not Channel.candles("BTC-USDX-PERP", "1m").is_private
    assert Channel.trades("BTC-USDX-PERP").key == ("trades", "BTC-USDX-PERP", None)
    assert Channel.candles("ETH", "1m").key == ("candles", "ETH", "1m")
    assert Channel.orders().key == ("orders", None, None)


def test_subscribe_frame_includes_only_relevant_fields() -> None:
    trades = json.loads(Channel.trades("BTC-USDX-PERP")._frame("subscribe"))
    assert trades == {"op": "subscribe", "channel": "trades", "market": "BTC-USDX-PERP"}

    candles = json.loads(Channel.candles("ETH-USDX-PERP", "1m")._frame("subscribe", since=42))
    assert candles == {
        "op": "subscribe",
        "channel": "candles",
        "market": "ETH-USDX-PERP",
        "interval": "1m",
        "since": 42,
    }

    orders = json.loads(Channel.orders()._frame("subscribe"))
    assert orders == {"op": "subscribe", "channel": "orders"}

    # `since` is only carried on subscribe ops, never unsubscribe.
    unsub = json.loads(Channel.trades("BTC")._frame("unsubscribe", since=9))
    assert unsub == {"op": "unsubscribe", "channel": "trades", "market": "BTC"}


def test_with_token_appends_encoded_query() -> None:
    assert ws_mod._with_token("wss://h/ws", "ab cd/+=") == "wss://h/ws?token=ab+cd%2F%2B%3D"
    assert ws_mod._with_token("wss://h/ws?v=2", "tok") == "wss://h/ws?v=2&token=tok"


# --------------------------------------------------------------------------
# Frame decode + cursor logic
# --------------------------------------------------------------------------
def test_decode_each_frame_kind() -> None:
    sub = ws_mod._decode(
        json.dumps({"op": "subscribed", "channel": "trades", "market": "BTC", "seq_at_join": 100})
    )
    assert isinstance(sub, Subscribed) and sub.seq_at_join == 100

    ev = ws_mod._decode(
        json.dumps(
            {
                "op": "event",
                "channel": "trades",
                "market": "BTC",
                "seq": 105,
                "payload": {"price": "42000"},
                "engine_envelope": {"epoch": 3, "sequence": 9001, "emitted_at": 1},
            }
        )
    )
    assert isinstance(ev, Event)
    assert ev.seq == 105 and ev.payload == {"price": "42000"}
    assert ev.engine_envelope is not None and ev.engine_envelope.epoch == 3

    unsub = ws_mod._decode(json.dumps({"op": "unsubscribed", "channel": "fills"}))
    assert isinstance(unsub, Unsubscribed)

    oos = ws_mod._decode(
        json.dumps(
            {
                "op": "out_of_sync",
                "channel": "candles",
                "market": "BTC",
                "interval": "1m",
                "oldest_seq": 200,
            }
        )
    )
    assert isinstance(oos, OutOfSync) and oos.oldest_seq == 200 and oos.interval == "1m"

    err = ws_mod._decode(json.dumps({"op": "error", "message": "no such market"}))
    assert isinstance(err, ServerError) and err.message == "no such market"

    # Unknown op / non-JSON are skipped (None), not decoded.
    assert ws_mod._decode(json.dumps({"op": "future_op"})) is None
    assert ws_mod._decode("not json") is None


def test_cursor_advance_and_reset() -> None:
    ev = Event(channel="trades", market="BTC", seq=7, payload={})
    assert ws_mod._cursor_advance(ev) == (("trades", "BTC", None), 7)
    sub = Subscribed(channel="trades", market="BTC", seq_at_join=3)
    assert ws_mod._cursor_advance(sub) == (("trades", "BTC", None), 3)
    oos = OutOfSync(channel="trades", market="BTC")
    assert ws_mod._cursor_reset(oos) == ("trades", "BTC", None)
    assert ws_mod._cursor_advance(oos) is None
    # Candles cursors are keyed by interval, so the reset key must carry it.
    oos_candles = OutOfSync(channel="candles", market="BTC", interval="1m")
    assert ws_mod._cursor_reset(oos_candles) == ("candles", "BTC", "1m")


# --------------------------------------------------------------------------
# Backoff
# --------------------------------------------------------------------------
def test_backoff_no_jitter_follows_exponential_curve() -> None:
    it = Backoff(initial=0.1, max=10.0, multiplier=2.0, jitter=False).iter()
    assert it.next_delay() == pytest.approx(0.1)
    assert it.next_delay() == pytest.approx(0.2)
    assert it.next_delay() == pytest.approx(0.4)
    it.reset()
    assert it.next_delay() == pytest.approx(0.1)


def test_backoff_saturates_at_max_and_jitter_stays_within_ceiling() -> None:
    it = Backoff(initial=1.0, max=4.0, multiplier=10.0, jitter=False).iter()
    assert it.next_delay() == pytest.approx(1.0)
    assert it.next_delay() == pytest.approx(4.0)
    assert it.next_delay() == pytest.approx(4.0)

    jit = Backoff(initial=0.1, max=60.0, multiplier=2.0).iter()
    ceiling = 0.1
    for _ in range(12):
        d = jit.next_delay()
        assert 0.0 <= d <= ceiling + 1e-9
        ceiling = min(ceiling * 2.0, 60.0)


# --------------------------------------------------------------------------
# Lagged delivery (bounded queue)
# --------------------------------------------------------------------------
async def test_deliver_drops_excess_and_reports_via_lagged() -> None:
    stream = WsStream(
        ws_url="ws://x/ws",
        channels=[],
        mint_token=lambda: WsToken(token="t", raw={}),
        user_agent="ua",
        backoff=Backoff(),
        channel_capacity=2,
    )
    total = 7
    delivered_flags = []
    items: list[Any] = []
    for seq in range(total):
        if seq in (4, 6):  # drain so a pending Lagged can flush
            while not stream._queue.empty():
                items.append(stream._queue.get_nowait())
        delivered_flags.append(stream._deliver(Event(channel="trades", seq=seq, payload={})))

    while not stream._queue.empty():
        items.append(stream._queue.get_nowait())

    # Reconstruct order: every Event.seq equals running count of delivered+lagged.
    expected = 0
    received = 0
    lagged_total = 0
    for item in items:
        if isinstance(item, Lagged):
            assert item.dropped > 0
            expected += item.dropped
            lagged_total += item.dropped
        elif isinstance(item, Event):
            assert item.seq == expected, "reorder/duplicate frame"
            expected += 1
            received += 1
    assert received + lagged_total + stream._dropped == total
    assert delivered_flags[0] is True  # first frame delivered before queue fills
    assert lagged_total > 0
    assert received < total  # capacity 2 vs 7 must drop some


# --------------------------------------------------------------------------
# End-to-end with a mocked socket
# --------------------------------------------------------------------------
class FakeConn:
    """A scripted fake of a ``websockets`` connection (async ctx mgr + iterator)."""

    def __init__(self, frames: Sequence[str], *, on_open=None) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []
        self.closed = False
        self._on_open = on_open

    async def __aenter__(self) -> FakeConn:
        if self._on_open is not None:
            self._on_open(self)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for f in self._frames:
            yield f
        # Frames exhausted -> stream ends, mimicking a dropped socket.

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


def _ev(seq: int, market: str = "BTC-USDX-PERP", payload=None) -> str:
    return json.dumps(
        {"op": "event", "channel": "trades", "market": market, "seq": seq, "payload": payload or {}}
    )


async def _collect(stream: WsStream, n: int, timeout: float = 2.0) -> list[Any]:
    out: list[Any] = []

    async def pump() -> None:
        async for item in stream:
            out.append(item)
            if len(out) >= n:
                return

    await asyncio.wait_for(pump(), timeout=timeout)
    return out


async def test_subscribe_and_decode_over_mocked_socket(monkeypatch) -> None:
    conn = FakeConn(
        [
            json.dumps(
                {
                    "op": "subscribed",
                    "channel": "trades",
                    "market": "BTC-USDX-PERP",
                    "seq_at_join": 10,
                }
            ),
            _ev(11, payload={"price": "42000"}),
            _ev(12, payload={"price": "42010"}),
        ]
    )

    def fake_connect(url, **kwargs):
        assert url == "ws://localhost:9090/ws"  # public, no token
        return conn

    monkeypatch.setattr(ws_mod, "ws_connect", fake_connect)

    client = Client(Network.LOCAL)
    stream = client.stream([Channel.trades("BTC-USDX-PERP")])
    items = await _collect(stream, 3)
    await stream.close()

    # Subscribe frame was sent on open.
    assert json.loads(conn.sent[0]) == {
        "op": "subscribe",
        "channel": "trades",
        "market": "BTC-USDX-PERP",
    }
    assert isinstance(items[0], Subscribed) and items[0].seq_at_join == 10
    assert isinstance(items[1], Event) and items[1].payload == {"price": "42000"}
    assert isinstance(items[2], Event) and items[2].seq == 12


async def test_reconnect_resumes_with_since_cursor(monkeypatch) -> None:
    # First connection delivers seq 10..11 then drops; second must resubscribe
    # with `since=11` (the highest seq processed).
    conns = [
        FakeConn([_ev(10), _ev(11)]),
        FakeConn([_ev(12)]),
    ]
    calls = {"n": 0}

    def fake_connect(url, **kwargs):
        c = conns[calls["n"]]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ws_mod, "ws_connect", fake_connect)

    client = Client(Network.LOCAL)
    # Tiny, no-jitter backoff so the reconnect is near-instant in the test.
    stream = client.stream(
        [Channel.trades("BTC-USDX-PERP")],
        backoff=Backoff(initial=0.001, max=0.001, jitter=False),
    )
    items = await _collect(stream, 3)
    await stream.close()

    assert calls["n"] >= 2, "should have reconnected after the first socket dropped"
    # Second connection's first send carries the resume cursor.
    second_sub = json.loads(conns[1].sent[0])
    assert second_sub["since"] == 11
    seqs = [it.seq for it in items if isinstance(it, Event)]
    assert seqs == [10, 11, 12]


async def test_private_channel_mints_token_on_connect(monkeypatch) -> None:
    captured = {}

    conn = FakeConn(
        [json.dumps({"op": "event", "channel": "fills", "seq": 1, "payload": {"id": "f1"}})]
    )

    def fake_connect(url, **kwargs):
        captured["url"] = url
        return conn

    monkeypatch.setattr(ws_mod, "ws_connect", fake_connect)

    client = Client(Network.LOCAL, api_key="nx", api_secret="00ff")
    # Stub the (signed) REST mint so no network call happens.
    monkeypatch.setattr(client, "mint_web_socket_token", lambda: WsToken(token="tok-123", raw={}))

    stream = client.stream([Channel.fills()])
    items = await _collect(stream, 1)
    await stream.close()

    assert "token=tok-123" in captured["url"]
    assert isinstance(items[0], Event) and items[0].channel == "fills"


def test_private_channel_without_credentials_fails_fast() -> None:
    client = Client(Network.LOCAL)  # no creds
    with pytest.raises(MissingCredentialsError):
        client.stream([Channel.fills()])


def test_unconfigured_network_ws_url_raises() -> None:
    client = Client(Network.STABLE)  # ws_base is None (ENG-3398)
    assert client.ws_base is None
    with pytest.raises(ValueError, match="ENG-3398"):
        client.stream([Channel.trades("BTC-USDX-PERP")])


async def test_close_is_idempotent_and_ends_iteration(monkeypatch) -> None:
    conn = FakeConn([_ev(1)])
    monkeypatch.setattr(ws_mod, "ws_connect", lambda url, **k: conn)
    client = Client(Network.LOCAL)
    async with client.stream([Channel.trades("BTC")]) as stream:
        first = await anext(aiter(stream))
        assert isinstance(first, Event)
    # Exiting the context closed the stream; a second close must not raise.
    await stream.close()


def _candle_ev(seq: int, market: str = "BTC-USDX-PERP", interval: str = "1m") -> str:
    return json.dumps(
        {
            "op": "event",
            "channel": "candles",
            "market": market,
            "interval": interval,
            "seq": seq,
            "payload": {},
        }
    )


async def test_close_does_not_raise_when_queue_full(monkeypatch) -> None:
    # A slow consumer that never drains can leave the bounded delivery queue
    # full at shutdown. The _CLOSED sentinel must not raise QueueFull out of
    # close()/__aexit__ (regression for ws.py:461 / _run finally).
    conn = FakeConn([_ev(1), _ev(2), _ev(3), _ev(4), _ev(5)])
    monkeypatch.setattr(ws_mod, "ws_connect", lambda url, **k: conn)
    client = Client(Network.LOCAL)

    # capacity=1 with no consumer: the read loop fills the one slot and starts
    # dropping, so the queue stays full through shutdown.
    stream = client.stream([Channel.trades("BTC-USDX-PERP")], channel_capacity=1)
    stream.start()
    # Let the background task connect, drain the scripted frames, and fill the queue.
    await asyncio.sleep(0.05)
    assert stream._queue.full(), "test precondition: delivery queue should be full"

    # Must complete cleanly despite the full queue.
    await asyncio.wait_for(stream.close(), timeout=2.0)

    # __aexit__ path (also routes through the sentinel enqueue) must not raise either.
    async with client.stream([Channel.trades("BTC-USDX-PERP")], channel_capacity=1) as s2:
        await asyncio.sleep(0.05)
        assert s2._queue.full()
    # Exiting the context (await s2.close()) completed without raising.


async def test_out_of_sync_clears_candles_cursor_so_reconnect_drops_stale_since(
    monkeypatch,
) -> None:
    # A candles subscription advances its (channel, market, interval) cursor,
    # then receives an out_of_sync. The cursor for that interval must be cleared
    # so the reconnect resubscribes WITHOUT the stale `since` (regression for
    # ws.py:341 / OutOfSync missing `interval`).
    first = FakeConn(
        [
            json.dumps(
                {
                    "op": "subscribed",
                    "channel": "candles",
                    "market": "BTC-USDX-PERP",
                    "interval": "1m",
                    "seq_at_join": 50,
                }
            ),
            _candle_ev(51),
            _candle_ev(52),
            json.dumps(
                {
                    "op": "out_of_sync",
                    "channel": "candles",
                    "market": "BTC-USDX-PERP",
                    "interval": "1m",
                    "oldest_seq": 999,
                }
            ),
        ]
    )
    second = FakeConn([_candle_ev(60)])
    conns = [first, second]
    calls = {"n": 0}

    def fake_connect(url, **kwargs):
        c = conns[calls["n"]]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ws_mod, "ws_connect", fake_connect)

    client = Client(Network.LOCAL)
    stream = client.stream(
        [Channel.candles("BTC-USDX-PERP", "1m")],
        backoff=Backoff(initial=0.001, max=0.001, jitter=False),
    )
    # Subscribed + 2 events + OutOfSync + 1 event from the second connection.
    items = await _collect(stream, 5)
    await stream.close()

    assert calls["n"] >= 2, "should have reconnected after the first socket dropped"
    assert any(isinstance(it, OutOfSync) for it in items)

    # The reconnect's resubscribe must NOT carry a stale `since` — the candles
    # cursor was cleared by the out_of_sync.
    second_sub = json.loads(second.sent[0])
    assert second_sub["channel"] == "candles" and second_sub["interval"] == "1m"
    assert "since" not in second_sub, "stale candles cursor was not cleared on out_of_sync"
    # After the reconnect, seq 60 legitimately re-seeds the cursor at the live
    # edge — proving recovery resumed from now rather than the dead `since`.
    assert stream._cursors[("candles", "BTC-USDX-PERP", "1m")] == 60
