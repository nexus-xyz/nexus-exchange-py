import hashlib
import hmac
from decimal import Decimal

import httpx
import pytest

from nexus_exchange import (
    ApiError,
    Client,
    MissingCredentialsError,
    Network,
    OrderRequest,
    TransportError,
)


def test_network_base_urls() -> None:
    # Testnet targets the spec's published REST base (EDR-006, ENG-18322). The
    # bare host routes nothing, and `/v1` is stripped at the edge.
    assert Network.TESTNET.base_url == "https://api.testnet.nexus.xyz/v1"
    assert Client(Network.LOCAL)._base_url == "http://localhost:9090"


def test_prefixed_base_sends_under_prefix_and_signs_the_bare_path(httpx_mock) -> None:
    # One base for every request: the path is appended to the `/v1` base and
    # signed without it, because the edge strips `/v1` before the indexer
    # verifies `/account`.
    secret = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    httpx_mock.add_response(url="https://api.testnet.nexus.xyz/v1/account", json={})
    with Client(Network.TESTNET, api_key="nx_test", api_secret=secret) as client:
        client.fetch_balance()

    req = httpx_mock.get_request()
    assert str(req.url) == "https://api.testnet.nexus.xyz/v1/account"
    ts = req.headers["x-timestamp"]
    body_hash = hashlib.sha256(b"").hexdigest()
    canonical = "\n".join([ts, "GET", "/account", "", body_hash])
    expected = hmac.new(bytes.fromhex(secret), canonical.encode(), hashlib.sha256).hexdigest()
    assert req.headers["x-signature"] == expected


def test_bridge_reads_keep_the_api_v1_spelling(httpx_mock) -> None:
    # The pinned spec has no bare twins for the bridge reads, so they go out as
    # `/v1/api/v1/bridge/...` and are signed as `/api/v1/bridge/...`.
    httpx_mock.add_response(url="https://api.testnet.nexus.xyz/v1/api/v1/bridge/assets", json={})
    with Client(Network.TESTNET) as client:
        client.fetch_bridge_assets()
    assert (
        str(httpx_mock.get_request().url) == "https://api.testnet.nexus.xyz/v1/api/v1/bridge/assets"
    )


def test_custom_base_url_is_the_one_base() -> None:
    client = Client(base_url="http://127.0.0.1:8080")
    assert client._base_url == "http://127.0.0.1:8080"


def test_signed_request_uses_canonical_hmac(httpx_mock) -> None:
    secret = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    httpx_mock.add_response(json={"ok": True})
    with Client(Network.LOCAL, api_key="nx_test", api_secret=secret) as client:
        client._request("GET", "/account", signed=True)

    req = httpx_mock.get_request()
    ts = req.headers["x-timestamp"]
    body_hash = hashlib.sha256(b"").hexdigest()
    canonical = "\n".join([ts, "GET", "/account", "", body_hash])
    expected = hmac.new(bytes.fromhex(secret), canonical.encode(), hashlib.sha256).hexdigest()
    assert req.headers["x-api-key"] == "nx_test"
    assert req.headers["x-signature"] == expected
    assert req.headers["user-agent"].startswith("nexus-exchange-py/")


def test_signed_without_credentials_raises() -> None:
    with Client(Network.LOCAL) as client:
        with pytest.raises(MissingCredentialsError):
            client._request("GET", "/account", signed=True)


def test_fetch_markets_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/markets",
        json=[
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
            }
        ],
    )
    with Client(Network.LOCAL) as client:
        markets = client.fetch_markets()
    assert markets[0].market_id == "BTC-USDX-PERP"
    assert str(markets[0].tick_size) == "0.1"
    assert markets[0].max_leverage == 20


def test_api_error_on_4xx_is_terminal(httpx_mock) -> None:
    httpx_mock.add_response(status_code=404, json={"code": "not_found", "message": "nope"})
    with Client(Network.LOCAL) as client:
        with pytest.raises(ApiError) as excinfo:
            client.fetch_ticker("NOPE")
    assert excinfo.value.status == 404
    assert excinfo.value.code == "not_found"
    assert excinfo.value.transient is False


def test_testnet_non_v1_route_stays_on_the_base(httpx_mock) -> None:
    # The other half of the split: a route with no /api/v1 variant must not pick
    # up the prefix, and must still land under the route prefix the base carries.
    httpx_mock.add_response(url="https://api.testnet.nexus.xyz/v1/ws/token", json={"token": "t"})
    with Client(Network.TESTNET) as client:
        client._request("POST", "/ws/token")

    assert str(httpx_mock.get_request().url) == "https://api.testnet.nexus.xyz/v1/ws/token"


def test_has_credentials_reflects_keys() -> None:
    assert Client(Network.LOCAL).has_credentials is False
    secret = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    assert Client(Network.LOCAL, api_key="nx_test", api_secret=secret).has_credentials is True


def test_tickers_non_dict_response_is_empty_map(httpx_mock) -> None:
    # A malformed /tickers envelope (array instead of the spec's keyed object)
    # degrades to an empty map rather than raising. Tickers are served by the
    # direct /api/v1 service (ENG-4946).
    httpx_mock.add_response(url="http://localhost:9090/tickers", json=[])
    with Client(Network.LOCAL) as client:
        assert client.fetch_tickers() == {}


def test_order_request_serializes_reduce_only() -> None:
    payload = OrderRequest.market(
        "BTC-USDX-PERP", "Sell", Decimal("1"), reduce_only=True
    ).to_payload()
    assert payload["reduce_only"] is True


def test_transport_error_wraps_httpx_error(httpx_mock) -> None:
    # A network-layer failure surfaces as the SDK's TransportError, not a raw
    # httpx exception, so callers catch one error hierarchy.
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    with Client(Network.LOCAL) as client:
        with pytest.raises(TransportError):
            client.fetch_markets()


def test_no_content_response_decodes_to_none(httpx_mock) -> None:
    # An empty 200 body (e.g. some DELETEs) decodes to None, not a parse error.
    # DELETE /orders is served by the direct /api/v1 service (ENG-4946).
    httpx_mock.add_response(url="http://localhost:9090/orders", method="DELETE", status_code=200)
    secret = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    with Client(Network.LOCAL, api_key="nx_test", api_secret=secret) as client:
        assert client.cancel_all_orders() is None


def test_non_json_error_body_still_raises_api_error(httpx_mock) -> None:
    # A 5xx with a plain-text (non-JSON) body must still raise ApiError with the
    # status, leaving code/message None rather than failing to parse the envelope.
    httpx_mock.add_response(status_code=502, text="upstream down")
    with Client(Network.LOCAL) as client:
        with pytest.raises(ApiError) as excinfo:
            client.fetch_markets()
    assert excinfo.value.status == 502
    assert excinfo.value.code is None
    assert excinfo.value.transient is True


def test_non_json_success_body_returns_raw_text(httpx_mock) -> None:
    # A 200 whose body is not JSON falls back to the raw text rather than raising.
    httpx_mock.add_response(url="http://localhost:9090/health", text="OK")
    with Client(Network.LOCAL) as client:
        assert client._request("GET", "/health") == "OK"
