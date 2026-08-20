import hashlib
import hmac

import pytest

from nexus_exchange import ApiError, Client, MissingCredentialsError, Network


def test_network_base_urls() -> None:
    # Testnet is the successor to the old `STABLE` channel and keeps its exact
    # targets: the legacy gateway still serves testnet, and the hosted per-network
    # host is not resolvable yet.
    assert Network.TESTNET.base_url == "https://exchange.nexus.xyz/api/exchange"
    # The /api/v1 surface is mounted UNDER the gateway prefix on this deploy, so
    # the direct base is the gateway base too. The host root 404s (ENG-10063).
    assert Network.TESTNET.direct_base_url == "https://exchange.nexus.xyz/api/exchange"
    assert Client(Network.LOCAL)._base_url == "http://localhost:9090"
    assert Client(Network.LOCAL)._direct_base_url == "http://localhost:9090"


def test_direct_route_signs_full_api_v1_path(httpx_mock) -> None:
    # A /api/v1 route must be signed over the FULL path including the prefix
    # (the server verifies "/api/v1/account", not "/account") and sent to the
    # direct-service base, not the gateway.
    secret = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    httpx_mock.add_response(url="http://localhost:9090/api/v1/account", json={})
    with Client(Network.LOCAL, api_key="nx_test", api_secret=secret) as client:
        client._request("GET", "/account", signed=True, direct=True)

    req = httpx_mock.get_request()
    ts = req.headers["x-timestamp"]
    body_hash = hashlib.sha256(b"").hexdigest()
    canonical = "\n".join([ts, "GET", "/api/v1/account", "", body_hash])
    expected = hmac.new(bytes.fromhex(secret), canonical.encode(), hashlib.sha256).hexdigest()
    assert str(req.url) == "http://localhost:9090/api/v1/account"
    assert req.headers["x-signature"] == expected


def test_custom_base_url_overrides_both_bases() -> None:
    # A caller-supplied base_url is the service root for legacy and direct
    # routes alike (the local / direct-gateway case), so /api/v1 stacks on it
    # without duplicating a gateway prefix.
    client = Client(base_url="http://127.0.0.1:8080")
    assert client._base_url == "http://127.0.0.1:8080"
    assert client._direct_base_url == "http://127.0.0.1:8080"


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


def test_testnet_direct_route_composes_the_gateway_mounted_url(httpx_mock) -> None:
    # The regression this pins: `direct_base_url` used to be the bare host root,
    # so every one of the ~36 `direct=True` routes composed
    # https://exchange.nexus.xyz/api/v1/... — which 404s to the frontend. The
    # whole suite passed anyway, because the only composed-URL test ran against
    # Network.LOCAL, where a bare origin IS the right base. Measured live:
    #
    #     .../api/exchange/api/v1/markets/summary  -> 200 application/json
    #     .../api/v1/markets/summary               -> 404 text/html (frontend)
    #
    # Assert on the URL, not on the config field, so this fails if either the
    # default or the composition regresses.
    httpx_mock.add_response(
        url="https://exchange.nexus.xyz/api/exchange/api/v1/markets/summary",
        json=[],
    )
    with Client(Network.TESTNET) as client:
        client._request("GET", "/markets/summary", direct=True)

    assert (
        str(httpx_mock.get_request().url)
        == "https://exchange.nexus.xyz/api/exchange/api/v1/markets/summary"
    )


def test_testnet_legacy_route_stays_on_the_gateway_base(httpx_mock) -> None:
    # The other half of the split: a route with no /api/v1 variant must not pick
    # up the prefix, and must still land under the gateway.
    httpx_mock.add_response(
        url="https://exchange.nexus.xyz/api/exchange/ws/token", json={"token": "t"}
    )
    with Client(Network.TESTNET) as client:
        client._request("POST", "/ws/token")

    assert str(httpx_mock.get_request().url) == "https://exchange.nexus.xyz/api/exchange/ws/token"
