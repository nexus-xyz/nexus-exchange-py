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

# Canned, spec-shaped (v0.6.2) response bodies, keyed by request path.
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

_HEALTH = {
    "events_received": 12345,
    "fills_total": 678,
    "uptime_seconds": 4242,
    "connected": True,
    "health": "healthy",
}

_ACCOUNT = {
    "balance": "1000.00",
    "collateral": "1000.00",
    "equity": "1012.34",
    "available_margin": "812.34",
    "positions": [],
}

_ORDER = {
    "id": "o-live-1",
    "market_id": "BTC-USDX-PERP",
    "side": "Buy",
    "order_type": "Limit",
    "price": "1000",
    "quantity": "0.001",
    "filled_qty": "0",
    "status": "Open",
    "time_in_force": "GTC",
    "created_at": 1776033900000,
    "updated_at": 1776033900000,
}

# A signed request that reaches the handler must carry all three HMAC headers.
_SIGNED_HEADERS = ("x-api-key", "x-timestamp", "x-signature")


class _Handler(BaseHTTPRequestHandler):
    """Serves the canned bodies above; 404s anything else as a JSON envelope.

    Signed routes additionally 401 if the HMAC headers are missing, so a signed
    round trip proves the client actually attached them over the wire.
    """

    def log_message(self, *_args: object) -> None:  # keep test output quiet
        pass

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_signed(self) -> bool:
        if all(h in self.headers for h in _SIGNED_HEADERS):
            return True
        self._send(401, {"code": "unauthorized", "message": "missing signature"})
        return False

    def do_GET(self) -> None:  # noqa: N802 (http.server dispatch name)
        # `/markets` and `/health` are legacy-gateway routes; the ticker and
        # account reads are served by the direct /api/v1 service, so they carry
        # the /api/v1 prefix (ENG-4946).
        if self.path == "/markets":
            self._send(200, _MARKETS)
        elif self.path == "/api/v1/markets/BTC-USDX-PERP/ticker":
            self._send(200, _TICKER)
        elif self.path == "/health":
            self._send(200, _HEALTH)
        elif self.path == "/api/v1/account":
            if self._require_signed():
                self._send(200, _ACCOUNT)
        else:
            self._send(404, {"code": "not_found", "message": f"no route {self.path}"})

    def do_POST(self) -> None:  # noqa: N802 (http.server dispatch name)
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)  # drain the signed body off the socket
        if self.path == "/api/v1/orders":
            if self._require_signed():
                self._send(200, {"order": _ORDER, "fills": []})
        else:
            self._send(404, {"code": "not_found", "message": f"no route {self.path}"})

    def do_DELETE(self) -> None:  # noqa: N802 (http.server dispatch name)
        if self.path == "/api/v1/orders/o-live-1":
            if self._require_signed():
                self._send(200, {"cancelled": True})
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


def test_health_check_round_trip(live_client: Client) -> None:
    health = live_client.health_check()
    assert health.connected is True
    assert health.events_received == 12345


def test_unknown_route_raises_api_error(live_client: Client) -> None:
    # A real 404 over the socket must surface as a terminal ApiError with the
    # decoded error envelope — exercising the status path end to end.
    with pytest.raises(ApiError) as excinfo:
        live_client.fetch_ticker("NOPE")
    assert excinfo.value.status == 404
    assert excinfo.value.code == "not_found"
    assert excinfo.value.transient is False


_LIVE_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


@pytest.fixture
def signed_live_client() -> Iterator[Client]:
    """A real, HMAC-signing ``Client`` against the same loopback server."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        with Client(
            base_url=f"http://{host}:{port}", api_key="nx_test", api_secret=_LIVE_SECRET
        ) as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_signed_account_round_trip(signed_live_client: Client) -> None:
    # Proves the HMAC headers survive real header serialization over the socket:
    # the handler 401s the read unless all three arrive.
    acct = signed_live_client.fetch_balance()
    assert str(acct.equity) == "1012.34"
    assert acct.positions == []


def test_signed_order_place_and_cancel_round_trip(signed_live_client: Client) -> None:
    # A signed POST with a JSON body, then a signed DELETE — the full order
    # lifecycle over a real socket, body and all.
    from decimal import Decimal

    from nexus_exchange import OrderRequest

    order = OrderRequest.limit("BTC-USDX-PERP", "Buy", Decimal("1000"), Decimal("0.001"))
    resp = signed_live_client.create_order(order)
    assert resp.order.id == "o-live-1"
    assert resp.order.status == "Open"

    result = signed_live_client.cancel_order("o-live-1")
    assert result == {"cancelled": True}


def test_signed_route_rejects_unsigned_client(live_client: Client) -> None:
    # The unsigned fixture client can't sign; calling the signed method raises
    # before any request leaves the process.
    from nexus_exchange import MissingCredentialsError

    with pytest.raises(MissingCredentialsError):
        live_client.fetch_balance()
