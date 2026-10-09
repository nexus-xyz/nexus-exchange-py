"""Signed trading actions on the eight order-path routes (ENG-20652, D27).

The pinned digests are copied from the exchange terminal's
``eng/apps/exchange-terminal/lib/agent/trading-intent.test.ts`` (nexus-xyz/nexus
main), which copies them from the server's
``exchange-sec-utils/src/trading_intent.rs :: tests::the_digests_are_pinned``
(alloy) and ``trading_request.rs``'s ``*_rebuilds_the_pinned_digest`` tests. D27
pins the same values against viem's ``hashTypedData``. Two of them are also the
spec's own test vector under "Signed trading actions". They are not values
captured from this code, so a wrong field, type, order or encoder step fails here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from eth_account import Account

from nexus_exchange import (
    AgentSigner,
    AmendOrder,
    AsyncClient,
    AuthError,
    Client,
    Funds,
    Network,
    NetworkConfig,
    OrderRequest,
)
from nexus_exchange.auth import _trading_digest, trading_action

_ACCOUNT = bytes.fromhex("11" * 20)
_ORDER_ID = "6f1c2b9e-3d4a-4f5b-8c7d-9e0f1a2b3c4d"
_LIMIT = (
    '{"market_id":"BTC-USDX-PERP","side":"Buy","order_type":"Limit","price":"65000.5",'
    '"quantity":"0.25","time_in_force":"GTC","stp":"CancelNewest","client_id":"order-1",'
    '"max_slippage_bps":50}'
)
_TRAILING = (
    '{"market_id":"ETH-USDX-PERP","side":"Sell","order_type":"TrailingStop","quantity":"1.5",'
    '"time_in_force":"IOC","reduce_only":true,"trailing_offset_bps":25}'
)
_Q = "market_id=BTC-USDX-PERP"

#: ``(method, path, query, body, digest)`` under account 0x11..11, domain
#: ``prd-testnet``, timestampMs 1700000000000, nonce 7.
PINNED = [
    ("POST", "/orders", "", _LIMIT,
     "15c5dd8665e1f92b194fb0b4932c561532a5100da669f8dbd402b8335fd58c7b"),
    ("POST", "/orders/batch", "", f"[{_LIMIT},{_TRAILING}]",
     "53c15273f220674d0510aff5eee8d892730917d499297d33595ac893d0b8ce2f"),
    ("PATCH", f"/orders/{_ORDER_ID}", _Q, '{"price":"65100"}',
     "723bc9cf519a7319623275969385653b538b3b8256835f0f1bb73b11378c9c06"),
    ("DELETE", f"/orders/{_ORDER_ID}", _Q, "",
     "04e7207aba00c39b57ba72949490c5a76bef60ad4079fc548df4fe622e032eef"),
    ("DELETE", "/orders", "", "",
     "d9b1603bf0dda6e8c534997901394243f134c2ad36ce0b272c488c8fad285a5f"),
    ("POST", "/account/margin", "", '{"market_id":"BTC-USDX-PERP","amount":"100",'
     '"direction":"add"}',
     "56ee08e84884de1e3baebcaa9f36d5ae9a8568a29b2650003e06827d388cf896"),
    ("POST", "/account/margin-mode", "", '{"market_id":"BTC-USDX-PERP","margin_mode":"isolated"}',
     "b4888215bfc76f851acda76338aa6163d05f894bf752efb2c092024fd187d5bc"),
    ("POST", "/leverage", "", '{"market_id":"BTC-USDX-PERP","leverage":10}',
     "f2740853950fe99dc8fcc1acc8f6f345c37414666469bf4c4d82c4011a2ede5d"),
    # The fields the eight above leave empty (trading_request.rs).
    ("POST", "/orders", "",
     '{"market_id":"BTC-USDX-PERP","side":"Sell","order_type":"StopLimit","price":"64900",'
     '"quantity":"0.5","time_in_force":"GTC","stop_price":"65000","trigger_price":"64950",'
     '"limit_offset_bps":15}',
     "21f7423a134a55e4bc11d15ca9c4bd8e618825740ed5a935f57110451a44be97"),
    ("PATCH", f"/orders/{_ORDER_ID}", _Q, '{"price":"65100","size":"0.75"}',
     "0deda3a52b83258a40b4f2e4522d15977b107f2ecf0e7096fb1232967815182b"),
    ("DELETE", "/orders", _Q, "",
     "52ae8f0733f20bb01ca01651dfe35694fc6e30c32c0a7833bb245d765ecab489"),
]  # fmt: skip


def _digest(method: str, path: str, query: str, body: str, account: bytes = _ACCOUNT) -> str:
    action = trading_action(method, path, query, body.encode())
    assert action is not None, f"{method} {path} must be a trading route"
    return _trading_digest(action, account, "prd-testnet", 1_700_000_000_000, 7).hex()


@pytest.mark.parametrize("v", PINNED, ids=lambda v: f"{v[0]} {v[1]}?{v[2]}")
def test_digest_reproduces_the_pinned_vector(v: tuple[str, str, str, str, str]) -> None:
    method, path, query, body, pinned = v
    assert _digest(method, path, query, body) == pinned


def test_every_struct_is_pinned() -> None:
    names = {trading_action(m, p, q, b.encode())[0] for m, p, q, b, _ in PINNED}  # type: ignore[index]
    assert len(names) == 8


def test_prefix_null_and_percent_encoding_match_the_server() -> None:
    bare = _digest("POST", "/orders", "", _LIMIT)
    assert _digest("post", "/api/v1/orders/", "", _LIMIT) == bare
    assert (
        _digest("DELETE", f"/orders/{_ORDER_ID}", "market_id=BTC%2DUSDX%2DPERP", "")
        == (PINNED[3][4])
    )
    with_null = _LIMIT.replace('"max_slippage_bps":50', '"max_slippage_bps":null')
    without = _LIMIT.replace(',"max_slippage_bps":50', "")
    assert _digest("POST", "/orders", "", with_null) == _digest("POST", "/orders", "", without)


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/orders/preview"), ("GET", "/orders"), ("PUT", "/account/preferences")],
)
def test_other_routes_are_not_trading_actions(method: str, path: str) -> None:
    assert trading_action(method, path, "", b"{}") is None


@pytest.mark.parametrize(
    ("method", "path", "query", "body"),
    [
        ("POST", "/orders", "", _LIMIT.replace('"order-1"', '""')),  # "" signs as absent
        ("DELETE", "/orders", "market_id=", ""),
        ("DELETE", f"/orders/{_ORDER_ID}", "", ""),  # market_id required
        ("POST", "/orders", "", _LIMIT.replace('"65000.5"', "65000.5")),  # decimal as number
        ("POST", "/leverage", "", '{"market_id":"M","leverage":2.5}'),
        ("POST", "/orders", "", ""),
    ],
)
def test_requests_the_server_would_refuse_are_refused(
    method: str, path: str, query: str, body: str
) -> None:
    with pytest.raises(AuthError):
        trading_action(method, path, query, body.encode())


# -- on the wire ----------------------------------------------------------

_KEY = "0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
_AGENT = Account.from_key(_KEY).address
_OWNER = "0x" + "aa" * 20
_SUB = "0x" + "bb" * 20
_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_BASE = "https://dev.example.com"
_DEVNET = NetworkConfig.custom(
    label="dev", funds=Funds.PLAY, base_url=_BASE, deployment_domain="devnet"
)
_ORDER = OrderRequest.limit("BTC-USDX-PERP", "Buy", Decimal("1"), Decimal("100"))

#: ``(call, method, path, query)`` for each trading route the client has.
ROUTES = [
    (lambda c: c.create_order(_ORDER), "POST", "/orders", ""),
    (lambda c: c.create_orders([_ORDER, _ORDER]), "POST", "/orders/batch", ""),
    (lambda c: c.edit_order("o1", "BTC-USDX-PERP", AmendOrder(price=Decimal("101"))),
     "PATCH", "/orders/o1", _Q),
    (lambda c: c.cancel_order("o1", "BTC-USDX-PERP"), "DELETE", "/orders/o1", _Q),
    (lambda c: c.cancel_all_orders(), "DELETE", "/orders", ""),
    (lambda c: c.add_margin("BTC-USDX-PERP", "add", "5"), "POST", "/account/margin", ""),
]  # fmt: skip


def _signer(headers: dict[str, str], method: str, path: str, query: str, body: bytes,
            account: str = _OWNER) -> str:  # fmt: skip
    """Rebuild the digest from what was sent and recover who signed it."""
    action = trading_action(method, path, query, body)
    assert action is not None
    digest = _trading_digest(
        action,
        bytes.fromhex(account[2:]),
        "devnet",
        int(headers["x-action-timestamp"]),
        int(headers["x-action-nonce"]),
    )
    return str(Account._recover_hash(digest, signature=headers["x-action-signature"]))


def _mock(httpx_mock, method: str, path: str, query: str) -> None:
    url = f"{_BASE}{path}" + (f"?{query}" if query else "")
    httpx_mock.add_response(method=method, url=url, json=[] if path.endswith("batch") else {})


def _agent(**kw) -> AgentSigner:
    return AgentSigner.from_hex(_KEY, account=_OWNER, **kw)


@pytest.mark.parametrize(("call", "method", "path", "query"), ROUTES, ids=lambda r: str(r))
def test_agent_client_signs_the_action_instead_of_the_canonical_string(
    httpx_mock, call, method: str, path: str, query: str
) -> None:
    _mock(httpx_mock, method, path, query)
    with Client(_DEVNET, agent=_agent()) as client:
        call(client)
    (req,) = httpx_mock.get_requests()
    h = dict(req.headers)
    assert h["x-agent"] == _AGENT.lower()
    for absent in ("x-signature", "x-timestamp", "x-nonce", "x-api-key", "x-acting-account"):
        assert absent not in h
    assert _signer(h, method, path, query, req.content) == _AGENT


@pytest.mark.parametrize(("call", "method", "path", "query"), ROUTES, ids=lambda r: str(r))
async def test_async_client_signs_the_same_way(
    httpx_mock, call, method: str, path: str, query: str
) -> None:
    _mock(httpx_mock, method, path, query)
    async with AsyncClient(_DEVNET, agent=_agent()) as client:
        await call(client)
    (req,) = httpx_mock.get_requests()
    h = dict(req.headers)
    assert "x-signature" not in h
    assert _signer(h, method, path, query, req.content) == _AGENT


def test_hmac_client_carries_the_action_beside_hmac_and_no_x_agent(httpx_mock) -> None:
    _mock(httpx_mock, "POST", "/orders", "")
    with Client(_DEVNET, api_key="nx_test", api_secret=_SECRET, agent=_agent()) as client:
        client.create_order(_ORDER)
    (req,) = httpx_mock.get_requests()
    h = dict(req.headers)
    assert h["x-api-key"] == "nx_test"
    assert {"x-timestamp", "x-signature"} <= h.keys()
    assert "x-agent" not in h
    assert _signer(h, "POST", "/orders", "", req.content) == _AGENT


def test_hmac_client_with_an_agent_can_still_manage_agents(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/agents", json=[])
    with Client(_DEVNET, api_key="nx_test", api_secret=_SECRET, agent=_agent()) as client:
        client.fetch_agents()
    (req,) = httpx_mock.get_requests()
    assert "x-action-signature" not in req.headers


def test_subaccount_is_signed_and_named_in_x_acting_account(httpx_mock) -> None:
    _mock(httpx_mock, "DELETE", "/orders", "")
    with Client(_DEVNET, agent=_agent(), acting_account=_SUB) as client:
        client.cancel_all_orders()
    (req,) = httpx_mock.get_requests()
    h = dict(req.headers)
    assert h["x-acting-account"] == _SUB
    assert _signer(h, "DELETE", "/orders", "", b"", account=_SUB) == _AGENT


def test_preview_reads_and_unnamed_deployments_keep_the_canonical_string(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/orders/preview", json={})
    httpx_mock.add_response(url="http://localhost:9090/orders", method="POST", json={})
    with Client(_DEVNET, agent=_agent()) as client:
        client.preview_order(_ORDER)
    with Client(Network.LOCAL, agent=_agent()) as client:
        client.create_order(_ORDER)
    for req in httpx_mock.get_requests():
        assert "x-signature" in req.headers
        assert "x-action-signature" not in req.headers


def test_nonces_are_shared_with_the_canonical_string_and_increase(httpx_mock) -> None:
    _mock(httpx_mock, "POST", "/orders", "")
    httpx_mock.add_response(url=f"{_BASE}/orders/preview", json={})
    with Client(_DEVNET, agent=_agent()) as client:
        client._now_ms = lambda: 5
        client.create_order(_ORDER)
        client.preview_order(_ORDER)
    first, second = httpx_mock.get_requests()
    assert (first.headers["x-action-nonce"], second.headers["x-nonce"]) == ("5", "6")


def test_construction_refuses_what_could_not_sign() -> None:
    with pytest.raises(ValueError, match="account="):
        Client(_DEVNET, agent=AgentSigner.from_hex(_KEY))
    with pytest.raises(ValueError, match="acting_account"):
        Client(Network.LOCAL, agent=_agent(), acting_account=_SUB)
    with pytest.raises(ValueError, match="acting_account"):
        Client(_DEVNET, acting_account=_SUB)
    with pytest.raises(AuthError):
        AgentSigner.from_hex(_KEY, account="0x1234")
