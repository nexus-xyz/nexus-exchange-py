import hashlib
import hmac
import json

import pytest

from nexus_exchange import (
    ApiError,
    Client,
    InvalidRequestError,
    MissingCredentialsError,
    Network,
)

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _expected_signature(req, *, method: str, path: str, query: str = "") -> str:
    ts = req.headers["x-timestamp"]
    body_hash = hashlib.sha256(req.content).hexdigest()
    canonical = "\n".join([ts, method, path, query, body_hash])
    return hmac.new(bytes.fromhex(SECRET), canonical.encode(), hashlib.sha256).hexdigest()


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
        url="http://localhost:9090/markets/summary",
        json=[{"market_id": "BTC-USDX-PERP"}, {"symbol": "ETH-USDX-PERP"}],
    )
    with Client(Network.LOCAL) as client:
        markets = client.fetch_markets()
    assert [m.market_id for m in markets] == ["BTC-USDX-PERP", "ETH-USDX-PERP"]


def test_api_error_on_4xx_is_terminal(httpx_mock) -> None:
    httpx_mock.add_response(status_code=404, json={"code": "not_found", "message": "nope"})
    with Client(Network.LOCAL) as client:
        with pytest.raises(ApiError) as excinfo:
            client.fetch_ticker("NOPE")
    assert excinfo.value.status == 404
    assert excinfo.value.code == "not_found"
    assert excinfo.value.transient is False


def test_fetch_account_is_signed_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/account",
        json={"balance": "1000", "equity": "1010", "positions": []},
    )
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        account = client.fetch_account()

    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert req.headers["x-signature"] == _expected_signature(req, method="GET", path="/account")
    assert account.raw["balance"] == "1000"


def test_fetch_positions_and_fills_parse(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/positions",
        json=[{"market_id": "BTC-USDX-PERP", "size": "0.5"}],
    )
    httpx_mock.add_response(
        url="http://localhost:9090/fills",
        json=[{"id": "f1", "order_id": "o1", "market_id": "BTC-USDX-PERP"}],
    )
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        positions = client.fetch_positions()
        fills = client.fetch_fills()

    assert [p.market_id for p in positions] == ["BTC-USDX-PERP"]
    assert fills[0].id == "f1"
    assert fills[0].order_id == "o1"


def test_place_order_body_mapping_and_signature(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/orders",
        json={"order": {"id": "o1", "market_id": "BTC-USDX-PERP"}, "fills": []},
    )
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        resp = client.place_order(
            "BTC-USDX-PERP",
            side="Buy",
            quantity="0.1",
            price="65000",
            client_order_id="abc",
        )

    req = httpx_mock.get_request()
    assert req.method == "POST"
    body = json.loads(req.content)
    assert body == {
        "market_id": "BTC-USDX-PERP",
        "side": "Buy",
        "order_type": "Limit",
        "quantity": "0.1",
        "time_in_force": "GTC",  # limit default
        "price": "65000",
        "client_order_id": "abc",
    }
    assert req.headers["x-signature"] == _expected_signature(req, method="POST", path="/orders")
    assert resp["order"]["id"] == "o1"


def test_place_market_order_omits_price_defaults_ioc(httpx_mock) -> None:
    httpx_mock.add_response(url="http://localhost:9090/orders", json={"order": {}})
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        client.place_order("BTC-USDX-PERP", side="Sell", quantity="1", order_type="Market")

    body = json.loads(httpx_mock.get_request().content)
    assert "price" not in body
    assert body["order_type"] == "Market"
    assert body["time_in_force"] == "IOC"


def test_place_limit_order_without_price_rejected_locally() -> None:
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        with pytest.raises(InvalidRequestError):
            client.place_order("BTC-USDX-PERP", side="Buy", quantity="0.1")


def test_cancel_order_url_and_signature(httpx_mock) -> None:
    httpx_mock.add_response(method="DELETE", url="http://localhost:9090/orders/o1", json={})
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        client.cancel_order("o1")

    req = httpx_mock.get_request()
    assert req.method == "DELETE"
    assert str(req.url) == "http://localhost:9090/orders/o1"
    assert req.headers["x-signature"] == _expected_signature(
        req, method="DELETE", path="/orders/o1"
    )


def test_cancel_order_with_market_id_query(httpx_mock) -> None:
    httpx_mock.add_response(
        method="DELETE",
        url="http://localhost:9090/orders/o1?market_id=BTC-USDX-PERP",
        json={},
    )
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        client.cancel_order("o1", market_id="BTC-USDX-PERP")

    req = httpx_mock.get_request()
    # The query is signed exactly as sent (signed === sent).
    assert req.headers["x-signature"] == _expected_signature(
        req, method="DELETE", path="/orders/o1", query="market_id=BTC-USDX-PERP"
    )


def test_cancel_all_targets_collection(httpx_mock) -> None:
    httpx_mock.add_response(method="DELETE", url="http://localhost:9090/orders", json={})
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        client.cancel_all()

    req = httpx_mock.get_request()
    assert str(req.url) == "http://localhost:9090/orders"


def test_cancel_order_empty_id_rejected_locally() -> None:
    with Client(Network.LOCAL, api_key="nx_test", api_secret=SECRET) as client:
        with pytest.raises(InvalidRequestError):
            client.cancel_order("")
