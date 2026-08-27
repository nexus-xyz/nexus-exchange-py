"""The operations added to close the pinned spec's coverage gap (ENG-9200).

Fifteen REST operations, taking `endpoints.txt` from 51 to 66 of the 68 the
pinned spec declares. The two that remain — `GET /ws` and `GET /stream` — are
WebSocket upgrades answering `101 Switching Protocols`, not REST operations;
this SDK opens no socket, so they have no method to test here. `POST /ws/token`
is the REST half of that story and *is* covered below.

Each operation is pinned the way `test_endpoint_surface.py` pins the rest of the
surface: exact path (so the `/api/v1` split per `endpoints.txt` cannot drift),
verb, whether the request was signed, and that the response parses into its
typed model. On top of that, three things specific to this batch get their own
tests because they are where the surface can go quietly wrong:

  * **`POST /faucet` is testnet/local only.** It carries the same
    `x-nexus-network-availability: [testnet, local]` marker as
    `POST /account/credit`, so it gets the same client-side guard — a real-funds
    host must not receive a signed faucet request.
  * **`POST /keys` is the one bearer-authenticated operation.** It mints HMAC
    credentials, so it cannot require them. That is a second auth path through
    `_send`, and the tests pin that it carries a bearer token, carries *no*
    signature, and refuses a token that could forge a header.
  * **Required fields decode strictly.** `BridgeWallet` and
    `BridgeWalletChallenge` have spec-`required` fields whose absence must fail
    rather than default — a fabricated `verified=True` or an empty challenge
    `message` is worse than a `DecodeError`.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal

import pytest

from nexus_exchange import (
    ACCOUNT_FUNDING_LIMIT_MAX,
    DEPOSITS_LIMIT_MAX,
    FUNDING_SAMPLES_LIMIT_MAX,
    ApiKeyCreated,
    Client,
    DecodeError,
    Network,
    OrderRequest,
)

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_BASE = "http://localhost:9090"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _public() -> Client:
    """A client with no credentials, so an unsigned route is proven unsigned:
    a method that started signing would raise `MissingCredentialsError` here
    rather than quietly passing."""
    return Client(Network.LOCAL)


def _assert_signed(req, method: str, path: str) -> None:
    """Same reference-HMAC check `test_endpoint_surface.py` uses: a signing bug
    fails here instead of passing on header presence alone."""
    assert req.method == method
    assert req.url.raw_path.decode().split("?")[0] == path
    assert req.headers["x-api-key"] == "nx_test"
    ts = req.headers["x-timestamp"]
    assert ts
    query = req.url.query.decode()
    body_hash = hashlib.sha256(req.content).hexdigest()
    canonical = "\n".join([ts, method.upper(), path, query, body_hash])
    expected = hmac.new(bytes.fromhex(_SECRET), canonical.encode(), hashlib.sha256).hexdigest()
    assert req.headers["x-signature"] == expected


# -- public market data --------------------------------------------------------


def test_fetch_market_risk_params_hits_the_gateway_route(httpx_mock) -> None:
    # No /api/v1 spelling in the pinned spec, so this one stays on the gateway.
    httpx_mock.add_response(
        url=f"{_BASE}/markets/BTC-USDX-PERP/risk-params",
        json={
            "market_id": "BTC-USDX-PERP",
            "max_leverage": 20,
            "initial_margin_rate": "0.05",
            "maintenance_margin_rate": "0.025",
        },
    )
    with _public() as client:
        params = client.fetch_market_risk_params("BTC-USDX-PERP")

    req = httpx_mock.get_request()
    assert req.method == "GET"
    assert req.url.raw_path.decode() == "/markets/BTC-USDX-PERP/risk-params"
    assert "x-signature" not in req.headers
    assert params.max_leverage == 20
    # Ratios, not percentages, and exact rather than float-rounded.
    assert params.initial_margin_rate == Decimal("0.05")
    assert params.maintenance_margin_rate == Decimal("0.025")


def test_market_risk_params_absent_fields_stay_none(httpx_mock) -> None:
    # Nothing is spec-required here, and a defaulted 0 maintenance-margin rate
    # would read as "no margin required" — the dangerous direction to guess in.
    httpx_mock.add_response(url=f"{_BASE}/markets/BTC-USDX-PERP/risk-params", json={})
    with _public() as client:
        params = client.fetch_market_risk_params("BTC-USDX-PERP")
    assert params.maintenance_margin_rate is None
    assert params.max_leverage is None


def test_fetch_funding_samples_is_a_direct_route_with_a_limit(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/markets/BTC-USDX-PERP/funding-samples?limit=10",
        json=[{"timestamp": 1750000000000, "premium_index": "0.0001"}],
    )
    with _public() as client:
        samples = client.fetch_funding_samples("BTC-USDX-PERP", limit=10)

    req = httpx_mock.get_request()
    assert (
        req.url.raw_path.decode().split("?")[0] == "/api/v1/markets/BTC-USDX-PERP/funding-samples"
    )
    assert samples[0].premium_index == Decimal("0.0001")
    assert samples[0].timestamp == 1750000000000


def test_funding_samples_requires_both_spec_required_fields(httpx_mock) -> None:
    # `timestamp` and `premium_index` are spec-required. A sample missing the
    # premium is not a zero premium — a run of fabricated zeros would read as
    # "the market is at parity with spot", which is a real trading signal.
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/markets/BTC-USDX-PERP/funding-samples",
        json=[{"timestamp": 1750000000000}],
    )
    with _public() as client, pytest.raises(DecodeError, match="premium_index"):
        client.fetch_funding_samples("BTC-USDX-PERP")


def test_fetch_stats_and_history_are_direct_routes(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/stats",
        json={"fills_total": 42, "connected": True, "events_per_sec": 12.5, "health": "Healthy"},
    )
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/stats/history",
        json=[{"timestamp": 1750000000, "fills": 7}],
    )
    with _public() as client:
        stats = client.fetch_stats()
        history = client.fetch_stats_history()

    assert stats.fills_total == 42
    assert stats.connected is True
    # A JSON *number* decoded through Decimal(str(x)), so it carries the text
    # that arrived rather than an f64 re-rendering.
    assert stats.events_per_sec == Decimal("12.5")
    # Unix SECONDS on this one model, unlike every other timestamp on the surface.
    assert history[0].timestamp == 1750000000
    assert history[0].fills == 7


def test_fetch_service_health_hits_status_not_health(httpx_mock) -> None:
    # `GET /health` was the legacy gateway probe, never a contract operation,
    # and was deleted by ENG-8618 rather than repointed. `/status` is the
    # contract's health operation and is what this must call.
    httpx_mock.add_response(
        url=f"{_BASE}/status",
        json={"status": "degraded", "timestamp_ms": 1750000000000, "services": {"engine": "ok"}},
    )
    with _public() as client:
        health = client.fetch_service_health()

    assert httpx_mock.get_request().url.raw_path.decode() == "/status"
    assert health.status == "degraded"
    assert health.services == {"engine": "ok"}


def test_service_health_services_is_copied_not_aliased(httpx_mock) -> None:
    # `services` is explicitly informational and free to evolve, so it stays an
    # untyped mapping — but a caller mutating it must not reach into `raw`.
    httpx_mock.add_response(url=f"{_BASE}/status", json={"status": "ok", "services": {"a": 1}})
    with _public() as client:
        health = client.fetch_service_health()
    health.services["a"] = 999
    assert health.raw["services"] == {"a": 1}


# -- signed account reads ------------------------------------------------------


def test_fetch_deposits_signs_and_parses_the_funds_ledger(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/deposits?limit=5",
        json=[
            {
                "id": 1,
                "kind": "faucet",
                "amount": "500",
                "asset": "USDX",
                "status": "confirmed",
                "tx_hash": None,
            }
        ],
    )
    with _authed() as client:
        entries = client.fetch_deposits(limit=5)

    _assert_signed(httpx_mock.get_request(), "GET", "/deposits")
    # Despite the path, the ledger carries withdrawals and faucet credits too.
    assert entries[0].kind == "faucet"
    assert entries[0].amount == Decimal("500")
    assert entries[0].tx_hash is None


def test_fetch_account_funding_signs_and_keeps_the_amount_signed(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/funding?limit=3",
        json=[{"market_id": "BTC-USDX-PERP", "amount": "-1.25", "direction": "paid"}],
    )
    with _authed() as client:
        funding = client.fetch_account_funding(limit=3)

    _assert_signed(httpx_mock.get_request(), "GET", "/funding")
    # The sign is the authoritative half; `direction` restates it and can drift.
    assert funding[0].amount == Decimal("-1.25")
    assert funding[0].direction == "paid"


@pytest.mark.parametrize(
    ("call", "maximum"),
    [
        ("deposits", DEPOSITS_LIMIT_MAX),
        ("funding", ACCOUNT_FUNDING_LIMIT_MAX),
        ("funding_samples", FUNDING_SAMPLES_LIMIT_MAX),
    ],
)
def test_limits_are_validated_before_the_request(call: str, maximum: int) -> None:
    # Each endpoint's `maximum` is a constraint on the REQUEST, so a conforming
    # client raises before spending one (and, on the signed routes, before
    # signing) rather than relying on the server to clamp. No httpx_mock here:
    # if a request were issued, the test would fail on the missing mock.
    with _authed() as client:
        for bad in (0, maximum + 1):
            with pytest.raises(ValueError, match="between 1 and"):
                if call == "deposits":
                    client.fetch_deposits(limit=bad)
                elif call == "funding":
                    client.fetch_account_funding(limit=bad)
                else:
                    client.fetch_funding_samples("BTC-USDX-PERP", limit=bad)


def test_the_three_new_limits_are_not_interchangeable() -> None:
    # They came in together and are easy to collapse into one shared constant.
    # The spec gives each its own maximum, so the values must stay distinct.
    assert DEPOSITS_LIMIT_MAX == 100
    assert ACCOUNT_FUNDING_LIMIT_MAX == 1000
    assert FUNDING_SAMPLES_LIMIT_MAX == 480


# -- bridge withdrawal wallets -------------------------------------------------


def test_list_bridge_wallets_signs_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/bridge/wallets",
        json={"wallets": [{"address": "0xabc", "verified": True, "is_default": True}]},
    )
    with _authed() as client:
        wallets = client.list_bridge_wallets()

    _assert_signed(httpx_mock.get_request(), "GET", "/api/v1/bridge/wallets")
    assert wallets.wallets[0].address == "0xabc"
    assert wallets.wallets[0].verified is True


def test_missing_wallets_array_is_a_decode_error_not_an_empty_account(httpx_mock) -> None:
    # `wallets` is spec-required. "No wallets registered" and "the server did not
    # send the field" are different facts, and only the first may read as empty —
    # an account that silently looks wallet-less cannot be paid a withdrawal.
    httpx_mock.add_response(url=f"{_BASE}/api/v1/bridge/wallets", json={})
    with _authed() as client, pytest.raises(DecodeError, match="wallets"):
        client.list_bridge_wallets()


def test_bridge_wallet_flags_must_be_real_booleans(httpx_mock) -> None:
    # `bool("false")` is True. A wallet that reports `verified` when the payload
    # says otherwise is undetectable downstream, so a non-boolean is a decode
    # failure rather than a coercion.
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/bridge/wallets",
        json={"wallets": [{"address": "0xabc", "verified": "false", "is_default": True}]},
    )
    with _authed() as client, pytest.raises(DecodeError, match="verified"):
        client.list_bridge_wallets()


def test_the_wallet_registration_round_trip(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/bridge/wallets/challenge",
        json={
            "address": "0xabc",
            "nonce": "n1",
            "message": "Nexus wants you to sign in...\nNonce: n1",
            "expires_at": 1750000060000,
        },
    )
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/bridge/wallets",
        json={"address": "0xabc", "verified": True, "is_default": True},
    )
    with _authed() as client:
        challenge = client.create_bridge_wallet_challenge("0xabc")
        wallet = client.register_bridge_wallet("0xabc", challenge.message, "0xsig")

    challenge_req, register_req = httpx_mock.get_requests()
    _assert_signed(challenge_req, "POST", "/api/v1/bridge/wallets/challenge")
    _assert_signed(register_req, "POST", "/api/v1/bridge/wallets")
    # The server keeps no state between the two calls and re-derives the signed
    # bytes from what comes back, so the message must survive byte for byte —
    # including the embedded newline a helpful `.strip()` would eat.
    assert b'"Nexus wants you to sign in...\\nNonce: n1"' in register_req.content
    assert wallet.verified is True


def test_a_challenge_missing_its_message_fails_to_decode(httpx_mock) -> None:
    # Defaulting `message` to "" would have the caller sign fabricated bytes and
    # send a signature that can never verify.
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/bridge/wallets/challenge",
        json={"address": "0xabc", "nonce": "n1", "expires_at": 1750000060000},
    )
    with _authed() as client, pytest.raises(DecodeError, match="message"):
        client.create_bridge_wallet_challenge("0xabc")


@pytest.mark.parametrize("args", [("", "m", "s"), ("0xabc", "", "s"), ("0xabc", "m", "")])
def test_register_bridge_wallet_rejects_blank_arguments(args: tuple[str, str, str]) -> None:
    with _authed() as client, pytest.raises(ValueError, match="required"):
        client.register_bridge_wallet(*args)


# -- signed account writes -----------------------------------------------------


def test_create_deposit_is_a_different_operation_from_deposit(httpx_mock) -> None:
    # `POST /deposits` and `POST /account/deposit` are two spec operations with
    # two response schemas. Wrapping each at its own path is deliberate; a
    # future "consolidation" that repoints one at the other breaks this.
    httpx_mock.add_response(url=f"{_BASE}/deposits", json={"balance": "1500.25"})
    httpx_mock.add_response(url=f"{_BASE}/account/deposit", json={"balance": "1500.25"})
    with _authed() as client:
        created = client.create_deposit("100")
        deposited = client.deposit("100")

    first, second = httpx_mock.get_requests()
    _assert_signed(first, "POST", "/deposits")
    _assert_signed(second, "POST", "/account/deposit")
    assert created.balance == Decimal("1500.25")
    assert deposited.balance == Decimal("1500.25")
    assert type(created) is not type(deposited)


def test_create_deposit_omits_asset_so_the_server_default_applies(httpx_mock) -> None:
    # The spec defaults `asset` to USDX. Sending our own copy of that default
    # means tracking it forever; omitting it lets the server own it.
    httpx_mock.add_response(url=f"{_BASE}/deposits", json={})
    with _authed() as client:
        client.create_deposit("100")
    assert b"asset" not in httpx_mock.get_request().content


def test_create_deposit_sends_asset_when_given(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/deposits", json={})
    with _authed() as client:
        client.create_deposit("100", asset="USDC")
    assert b'"asset": "USDC"' in httpx_mock.get_request().content


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "-Infinity", "abc", ""])
def test_create_deposit_rejects_an_amount_that_is_not_a_positive_decimal(amount: str) -> None:
    # One exception type for every "bad amount", including the unparseable case:
    # `Decimal("abc")` raises InvalidOperation, an ArithmeticError rather than a
    # ValueError, so without the catch a typo would surface as decimal internals.
    # NaN and Infinity are here because neither is caught by `<= 0` alone.
    with _authed() as client, pytest.raises(ValueError, match="positive decimal"):
        client.create_deposit(amount)


def test_claim_faucet_signs_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/faucet", json={"amount": "500", "available_at_ms": 1750086400000}
    )
    with _authed() as client:
        result = client.claim_faucet()

    _assert_signed(httpx_mock.get_request(), "POST", "/faucet")
    # Takes no arguments: the amount is the server's, not the caller's.
    assert httpx_mock.get_request().content == b""
    assert result.amount == Decimal("500")
    assert result.available_at_ms == 1750086400000


def test_claim_faucet_is_refused_on_a_faucet_less_network() -> None:
    # Same marker as POST /account/credit (`x-nexus-network-availability:
    # [testnet, local]`), so it gets the same guard. Failing here beats spending
    # a signed request against a real-funds host — and no httpx_mock is
    # registered, so an escaping request would fail the test too.
    with Client(Network.MAINNET, base_url="https://api.nexus.xyz") as client:
        with pytest.raises(ValueError, match="has no faucet"):
            client.claim_faucet()


def test_both_faucet_style_operations_are_guarded_the_same_way() -> None:
    # `claim_faucet` and `claim_credit` mint synthetic funds by different rules
    # (fixed amount + cooldown vs. a daily allowance). Whichever is added next,
    # the network guard must not be the thing that gets forgotten.
    with Client(Network.MAINNET, base_url="https://api.nexus.xyz") as client:
        for call in (client.claim_faucet, client.claim_credit):
            with pytest.raises(ValueError, match="has no faucet"):
                call()


# -- trading -------------------------------------------------------------------


def test_preview_order_sends_the_same_body_as_create_order(httpx_mock) -> None:
    # A preview that serialized differently from the order it previews would be
    # projecting something the caller never intends to send.
    order = OrderRequest.limit("BTC-USDX-PERP", "buy", Decimal("50000"), Decimal("1"))
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/orders/preview",
        json={"accepted": True, "projected_fees": "2.5", "expected_fill_vwap": None},
    )
    httpx_mock.add_response(url=f"{_BASE}/api/v1/orders", json={"order": {}, "fills": []})
    with _authed() as client:
        preview = client.preview_order(order)
        client.create_order(order)

    preview_req, order_req = httpx_mock.get_requests()
    _assert_signed(preview_req, "POST", "/api/v1/orders/preview")
    assert preview_req.content == order_req.content
    assert preview.accepted is True
    assert preview.projected_fees == Decimal("2.5")
    # Explicitly nullable: no resting liquidity means there is no VWAP to
    # project, which is not the same as a zero VWAP.
    assert preview.expected_fill_vwap is None


def test_a_rejected_preview_carries_its_reason(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/api/v1/orders/preview",
        json={"accepted": False, "reject_reason": "insufficient_margin"},
    )
    order = OrderRequest.market("BTC-USDX-PERP", "buy", Decimal("1"))
    with _authed() as client:
        preview = client.preview_order(order)
    assert preview.accepted is False
    assert preview.reject_reason == "insufficient_margin"


# -- websocket token -----------------------------------------------------------


def test_create_ws_token_and_the_legacy_mint_are_separate_routes(httpx_mock) -> None:
    # Two published operations: `createWsToken` (/ws/token) and
    # `createWsTokenLegacy` (/ws-tokens). Both stay wrapped, at their own paths.
    httpx_mock.add_response(url=f"{_BASE}/ws/token", json={"token": "t-new"})
    httpx_mock.add_response(url=f"{_BASE}/ws-tokens", json={"token": "t-legacy"})
    with _authed() as client:
        new = client.create_ws_token()
        legacy = client.mint_web_socket_token()

    new_req, legacy_req = httpx_mock.get_requests()
    _assert_signed(new_req, "POST", "/ws/token")
    _assert_signed(legacy_req, "POST", "/ws-tokens")
    assert new.token == "t-new"
    assert legacy.token == "t-legacy"


# -- POST /keys: the one bearer-authenticated operation ------------------------


def test_create_api_key_uses_a_bearer_token_and_no_signature(httpx_mock) -> None:
    # It mints HMAC credentials, so it cannot require them: the client here has
    # no api_key/api_secret at all, and a method that tried to sign would raise
    # MissingCredentialsError instead of reaching the transport.
    httpx_mock.add_response(url=f"{_BASE}/keys", json={"key_id": "nx_abc", "secret": "deadbeef"})
    with _public() as client:
        created = client.create_api_key("0" * 64)

    req = httpx_mock.get_request()
    assert req.method == "POST"
    assert req.url.raw_path.decode() == "/keys"
    assert req.headers["authorization"] == f"Bearer {'0' * 64}"
    assert "x-signature" not in req.headers
    assert "x-api-key" not in req.headers
    assert created.key_id == "nx_abc"
    assert created.secret == "deadbeef"


def test_the_api_key_secret_is_redacted_in_repr() -> None:
    # The secret is returned once and never again. An incidental log, traceback
    # frame or debugger dump must not be what leaks a live credential.
    created = ApiKeyCreated.from_dict({"key_id": "nx_abc", "secret": "s3cr3t"})
    assert "s3cr3t" not in repr(created)
    assert "<redacted>" in repr(created)
    assert "nx_abc" in repr(created)
    # Still readable when actually asked for.
    assert created.secret == "s3cr3t"


@pytest.mark.parametrize("token", ["", "   ", "\t"])
def test_create_api_key_rejects_a_blank_session_token(token: str) -> None:
    # Would otherwise go out as a well-formed Bearer header carrying no
    # credential and come back as a 401 indistinguishable from a wrong token.
    with _public() as client, pytest.raises(ValueError, match="empty"):
        client.create_api_key(token)


@pytest.mark.parametrize("token", ["abc\r\nX-Admin: 1", "abc def", "abc\x00", "abc\n"])
def test_create_api_key_refuses_a_header_injecting_token(token: str) -> None:
    # A token carrying CR/LF could terminate the Authorization header and start
    # another. httpx rejects such values itself, but that is downstream of us.
    # Checked across the whole string, never stripped: silently repairing a
    # credential is its own bug.
    with _public() as client, pytest.raises(ValueError, match="whitespace or control"):
        client.create_api_key(token)


def test_a_request_cannot_be_both_signed_and_bearer_authenticated() -> None:
    # Two identities for one call, left to the server to disambiguate. Not
    # reachable from the public surface — this guards the plumbing so it stays
    # that way.
    with _authed() as client:
        with pytest.raises(ValueError, match="both HMAC-signed and bearer"):
            client._request("POST", "/keys", signed=True, bearer="0" * 64)
