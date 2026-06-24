import hashlib
import hmac

import pytest

from nexus_exchange import ApiError, Client, MissingCredentialsError, Network


def test_network_base_urls() -> None:
    assert Network.STABLE.base_url.startswith("https://")
    assert Client(Network.LOCAL)._base_url == "http://localhost:9090"


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
