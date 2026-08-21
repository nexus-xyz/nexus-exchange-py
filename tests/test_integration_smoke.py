"""Integration smoke test: a real ``Client`` round-trip over a real socket.

The unit tests in ``test_client.py`` mock httpx, so they never exercise the
actual transport — URL building, header serialization, status handling, and JSON
decoding all happen inside httpx's mock layer. This module instead stands up a
real local HTTP server (stdlib ``http.server`` on ``127.0.0.1:0``), serves
canned, spec-shaped responses, and drives a real ``Client`` against it end to
end. It mirrors the Rust SDK's wiremock tests and the MCP integration smoke.

No network access is needed — the server is loopback-only — so this runs in CI
alongside the unit tests. For an opt-in round-trip against the *public* gateway,
see ``scripts/smoke.py``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nexus_exchange import ApiError, Client

# Canned, spec-shaped (v0.7.1) response bodies, keyed by request path.
_MARKETS = [
    {
        "market_id": "BTC-USDX-PERP",
        "base_asset": "BTC",
        "quote_asset": "USDX",
        "tick_size": "0.1",
        "lot_size": "0.001",
        "min_order_size": "0.001",
        "max_order_size": "100",
        "initial_margin_rate": "0.05",
        "maintenance_margin_rate": "0.03",
        "max_leverage": 20,
    },
    {
        "market_id": "ETH-USDX-PERP",
        "base_asset": "ETH",
        "quote_asset": "USDX",
        "tick_size": "0.01",
        "lot_size": "0.01",
        "min_order_size": "0.01",
        "max_order_size": "1000",
        "initial_margin_rate": "0.05",
        "maintenance_margin_rate": "0.03",
        "max_leverage": 20,
    },
]

_TICKER = {
    "symbol": "BTC-USDX-PERP",
    "timestamp": 1776033900000,
    "datetime": "2026-04-13T00:00:00Z",
    "bid": 50010.0,
    "ask": 50012.5,
    "last": 50011.6,
    "markPrice": 50011.6,
    "info": {},
}

_ORDERBOOK = {
    "symbol": "BTC-USDX-PERP",
    "bids": [["50010.0", "1.5"], ["50009.5", "2.25"]],
    "asks": [["50012.5", "0.75"]],
    "timestamp": 1776033900000,
    "datetime": "2026-04-13T00:00:00Z",
    "nonce": 7,
}


class _Handler(BaseHTTPRequestHandler):
    """Serves the canned bodies above; 404s anything else as a JSON envelope."""

    def log_message(self, *_args: object) -> None:  # keep test output quiet
        pass

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server dispatch name)
        # `/markets` is a legacy-gateway route; the ticker and order-book reads
        # are served by the direct /api/v1 service, so they carry the prefix.
        if self.path == "/markets":
            self._send(200, _MARKETS)
        elif self.path == "/api/v1/markets/BTC-USDX-PERP/ticker":
            self._send(200, _TICKER)
        elif self.path == "/api/v1/markets/BTC-USDX-PERP/orderbook":
            self._send(200, _ORDERBOOK)
        else:
            self._send(404, {"code": "not_found", "message": f"no route {self.path}"})


@pytest.fixture
def live_client() -> Iterator[Client]:
    """A real ``Client`` pointed at a loopback HTTP server on an ephemeral port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        with Client(base_url=f"http://{host}:{port}") as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_fetch_markets_round_trip(live_client: Client) -> None:
    markets = live_client.fetch_markets()
    assert [m.market_id for m in markets] == ["BTC-USDX-PERP", "ETH-USDX-PERP"]
    # Trading-rule decimals decode exactly off the real socket round trip.
    assert str(markets[0].tick_size) == "0.1"
    assert markets[0].max_leverage == 20


def test_fetch_ticker_round_trip(live_client: Client) -> None:
    ticker = live_client.fetch_ticker("BTC-USDX-PERP")
    assert ticker.market_id == "BTC-USDX-PERP"
    assert ticker.last is not None and str(ticker.last) == "50011.6"


def test_fetch_order_book_round_trip(live_client: Client) -> None:
    book = live_client.fetch_order_book("BTC-USDX-PERP")
    assert book.symbol == "BTC-USDX-PERP"
    assert [str(lvl.price) for lvl in book.bids] == ["50010.0", "50009.5"]
    assert [str(lvl.amount) for lvl in book.asks] == ["0.75"]


def test_unknown_route_raises_api_error(live_client: Client) -> None:
    # A real 404 over the socket must surface as a terminal ApiError with the
    # decoded error envelope — exercising the status path end to end.
    with pytest.raises(ApiError) as excinfo:
        live_client.fetch_ticker("NOPE")
    assert excinfo.value.status == 404
    assert excinfo.value.code == "not_found"
    assert excinfo.value.transient is False
