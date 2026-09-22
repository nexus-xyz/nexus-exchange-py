"""Agent-key request signing (``agentAuth``, ENG-17009).

The known-answer vectors are copied verbatim from the ``x-nexus-test-vectors``
extension that nexus-xyz/nexus#12162 adds to ``agentAuth`` in
``eng/apps/exchange/api/openapi.json`` (head ``063aaca7``). They are the same
three vectors ``nexus-exchange-rs`` pins in ``src/auth/agent.rs`` (rs#156),
which came from the exchange terminal's own signer and were reproduced with
``eth-account``. Between them they cover an empty-body GET, a JSON POST with a
lower-case method, a DELETE with a query, and both recovery ids.
"""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from eth_account import Account
from eth_utils.crypto import keccak

from nexus_exchange import (
    AgentKeyRefusedError,
    AgentSigner,
    AuthError,
    Client,
    MissingCredentialsError,
    Network,
    OrderRequest,
    RetryConfig,
)
from nexus_exchange.auth import agent_canonical_string

#: Verbatim copy of ``agentAuth.x-nexus-test-vectors`` from nexus#12162.
SPEC_VECTORS: list[dict[str, str]] = json.loads(
    (Path(__file__).parent / "agent_auth_vectors.json").read_text()
)

_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_KEY = SPEC_VECTORS[1]["private_key"]
_AGENT = SPEC_VECTORS[1]["x-agent"]
_HMAC_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _recover(method: str, path: str, query: str, body: bytes, headers: dict[str, str]) -> str:
    """Independently rebuild the digest from the sent headers and ecrecover it."""
    canonical = agent_canonical_string(
        method, path, query, body, int(headers["x-timestamp"]), int(headers["x-nonce"])
    )
    return str(Account._recover_hash(keccak(text=canonical), signature=headers["x-signature"]))


def _agent_client(**kw) -> Client:
    return Client(Network.LOCAL, agent=AgentSigner.from_hex(_KEY), **kw)


# -- known-answer vectors -------------------------------------------------


def test_the_suite_carries_all_three_spec_vectors_and_both_recovery_ids() -> None:
    assert len(SPEC_VECTORS) == 3
    assert {int(v["x-signature"][-2:], 16) for v in SPEC_VECTORS} == {27, 28}


@pytest.mark.parametrize("v", SPEC_VECTORS, ids=lambda v: f"{v['method']} {v['path']}")
def test_signer_matches_spec_vector_byte_for_byte(v: dict[str, str]) -> None:
    signer = AgentSigner.from_hex(v["private_key"])
    assert signer.address == v["x-agent"]

    body = v["body"].encode()
    ts, nonce = int(v["x-timestamp"]), int(v["x-nonce"])
    canonical = agent_canonical_string(v["method"], v["path"], v["query"], body, ts, nonce)
    assert canonical == v["canonical_string"]
    assert "0x" + keccak(text=canonical).hex() == v["digest"]

    headers = signer.sign_request(v["method"], v["path"], v["query"], body, ts, nonce)
    assert headers == {
        "x-agent": v["x-agent"],
        "x-timestamp": v["x-timestamp"],
        "x-nonce": v["x-nonce"],
        "x-signature": v["x-signature"],
    }


def test_canonical_string_matches_the_servers_own_pinned_vector() -> None:
    # exchange-sec-utils::signing::canonical_string_matches_the_pinned_wire_format
    assert agent_canonical_string("post", "/account/withdraw", "a=1", b"hello", 1_700, 42) == (
        "POST\n/account/withdraw\na=1\n"
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824\n1700\n42"
    )


@pytest.mark.parametrize("v", SPEC_VECTORS, ids=lambda v: f"{v['method']} {v['path']}")
def test_signature_recovers_to_agent_is_low_s_and_has_no_eip191_prefix(v: dict[str, str]) -> None:
    signer = AgentSigner.from_hex(v["private_key"])
    body = v["body"].encode()
    headers = signer.sign_request(
        v["method"], v["path"], v["query"], body, int(v["x-timestamp"]), int(v["x-nonce"])
    )
    assert _recover(v["method"], v["path"], v["query"], body, headers) == (
        Account.from_key(v["private_key"]).address
    )
    sig = bytes.fromhex(headers["x-signature"][2:])
    assert len(sig) == 65
    assert int.from_bytes(sig[32:64], "big") <= _SECP256K1_N // 2, "server refuses high-S"
    assert sig[64] in (27, 28)

    # The whole point of the no-prefix rule: an EIP-191 `personal_sign` over the
    # same canonical string is a *different* signature.
    from eth_account.messages import encode_defunct

    eip191 = Account.sign_message(encode_defunct(text=v["canonical_string"]), v["private_key"])
    assert "0x" + bytes(eip191.signature).hex() != headers["x-signature"]


def test_repr_never_shows_the_key() -> None:
    signer = AgentSigner.from_hex(_KEY)
    assert repr(signer) == f"AgentSigner(address={_AGENT!r})"
    assert _KEY[2:] not in repr(signer)


def test_bad_keys_and_out_of_range_inputs_are_auth_errors() -> None:
    with pytest.raises(AuthError):
        AgentSigner.from_hex("0xzz")
    with pytest.raises(AuthError):
        AgentSigner.from_hex("0x01")
    signer = AgentSigner.from_hex(_KEY)
    with pytest.raises(AuthError):
        signer.sign_request("GET", "/x", "", b"", 1, -1)
    with pytest.raises(AuthError):
        signer.sign_request("GET", "/x", "", b"", 1, 1 << 64)
    with pytest.raises(AuthError):
        signer.sign_request("GET", "/x", "", b"", True, 1)  # type: ignore[arg-type]


# -- nonce issuance -------------------------------------------------------


def test_nonce_is_max_of_last_plus_one_and_timestamp() -> None:
    signer = AgentSigner.from_hex(_KEY)
    assert signer.next_nonce(1_000) == 1_000
    assert signer.next_nonce(1_000) == 1_001, "same ms: last + 1"
    assert signer.next_nonce(900) == 1_002, "clock went backwards: still increasing"
    assert signer.next_nonce(5_000) == 5_000, "clock jumped ahead: follows it"


def test_nonces_are_unique_and_increasing_across_threads() -> None:
    signer = AgentSigner.from_hex(_KEY)
    per_thread: list[list[int]] = [[] for _ in range(8)]

    def issue(out: list[int]) -> None:
        for _ in range(500):
            out.append(signer.next_nonce(1_000))

    threads = [threading.Thread(target=issue, args=(out,)) for out in per_thread]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    every = [n for out in per_thread for n in out]
    assert len(set(every)) == len(every) == 4_000
    assert all(a < b for out in per_thread for a, b in zip(out, out[1:], strict=False))


# -- on the wire ----------------------------------------------------------


def test_exact_headers_on_the_wire_match_spec_vector_one(httpx_mock) -> None:
    # Vector 1 is a GET whose nonce equals its timestamp — exactly what a fresh
    # signer issues at that clock reading — so the client reproduces it whole.
    v = SPEC_VECTORS[0]
    httpx_mock.add_response(url="http://localhost:9090/account/summary", json={})
    with Client(Network.LOCAL, agent=AgentSigner.from_hex(v["private_key"])) as client:
        client._now_ms = lambda: int(v["x-timestamp"])
        client._request("GET", "/account/summary", signed=True)
    (req,) = httpx_mock.get_requests()
    assert req.headers["x-agent"] == v["x-agent"]
    assert req.headers["x-timestamp"] == v["x-timestamp"]
    assert req.headers["x-nonce"] == v["x-nonce"]
    assert req.headers["x-signature"] == v["x-signature"]
    assert "x-api-key" not in req.headers
    assert "authorization" not in req.headers


def test_typed_write_signs_the_full_direct_path_and_exact_body(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/api/v1/orders",
        json={"order_id": "o1", "status": "open"},
    )
    with _agent_client() as client:
        client._now_ms = lambda: 1_776_033_900_123
        client.create_order(
            OrderRequest.limit("BTC-USDX-PERP", "Buy", Decimal("50000"), Decimal("0.1"))
        )
    (req,) = httpx_mock.get_requests()
    headers = {k: req.headers[k] for k in ("x-agent", "x-timestamp", "x-nonce", "x-signature")}
    assert headers["x-agent"] == _AGENT
    assert headers["x-timestamp"] == "1776033900123"
    assert headers["x-nonce"] == "1776033900123"
    # Recovered from what was actually sent: the /api/v1 path and the body bytes.
    assert _recover("POST", "/api/v1/orders", "", req.content, headers) == (
        Account.from_key(_KEY).address
    )


def test_each_retry_attempt_gets_a_fresh_timestamp_and_nonce(httpx_mock) -> None:
    url = "http://localhost:9090/api/v1/orders"
    httpx_mock.add_response(url=url, status_code=503)
    httpx_mock.add_response(url=url, status_code=502)
    httpx_mock.add_response(url=url, json=[])
    with _agent_client(retry=RetryConfig(min_delay=0.0, jitter=False)) as client:
        client._sleep = lambda _s: None
        ticks = iter([2_000_000, 2_000_000, 2_001_000])
        client._now_ms = lambda: next(ticks)
        client.fetch_open_orders()
    reqs = httpx_mock.get_requests()
    assert len(reqs) == 3
    nonces = [int(r.headers["x-nonce"]) for r in reqs]
    assert nonces == [2_000_000, 2_000_001, 2_001_000], "fresh, strictly increasing per attempt"
    assert len({r.headers["x-signature"] for r in reqs}) == 3
    for r in reqs:
        assert _recover("GET", "/api/v1/orders", "", b"", dict(r.headers)) == (
            Account.from_key(_KEY).address
        )


# -- credential precedence ------------------------------------------------


@pytest.mark.parametrize(
    "hmac",
    [
        {"api_key": "nx_test", "api_secret": _HMAC_SECRET},
        {"api_key": "nx_test"},
        {"api_secret": _HMAC_SECRET},
    ],
)
def test_agent_and_any_hmac_field_together_is_refused(hmac: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="not both"):
        Client(Network.LOCAL, agent=AgentSigner.from_hex(_KEY), **hmac)


def test_agent_must_be_an_agent_signer() -> None:
    with pytest.raises(TypeError, match="AgentSigner"):
        Client(Network.LOCAL, agent=_KEY)  # type: ignore[arg-type]


def test_agent_client_reports_credentials() -> None:
    signer = AgentSigner.from_hex(_KEY)
    client = Client(Network.LOCAL, agent=signer)
    assert client.has_credentials
    assert client.agent is signer
    assert Client(Network.LOCAL).agent is None


def test_keyless_signed_request_names_both_credentials() -> None:
    with pytest.raises(MissingCredentialsError, match="agent key"):
        Client(Network.LOCAL).fetch_open_orders()


def test_bearer_call_on_an_agent_client_sends_only_the_bearer(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/keys", json={"key_id": "k", "api_key": "a", "secret": "s"}
    )
    with _agent_client() as client:
        client.create_api_key("ab" * 32)
    (req,) = httpx_mock.get_requests()
    assert req.headers["authorization"] == "Bearer " + "ab" * 32
    for name in ("x-agent", "x-nonce", "x-signature", "x-timestamp", "x-api-key"):
        assert name not in req.headers


def test_public_call_on_an_agent_client_sends_no_credential(httpx_mock) -> None:
    httpx_mock.add_response(url="http://localhost:9090/api/v1/markets/summary", json=[])
    with _agent_client() as client:
        client.fetch_market_summaries()
    (req,) = httpx_mock.get_requests()
    assert "x-agent" not in req.headers
    assert "x-signature" not in req.headers


# -- trade-only: local refusals -------------------------------------------


@pytest.mark.parametrize(
    ("call", "method", "path"),
    [
        (lambda c: c.fetch_agents(), "GET", "/agents"),
        (lambda c: c.revoke_agent("0xabc"), "DELETE", "/agents/0xabc"),
        (lambda c: c.mint_web_socket_token(), "POST", "/ws-tokens"),
    ],
)
def test_agent_forbidden_operations_are_refused_before_any_request(
    httpx_mock, call, method: str, path: str
) -> None:
    with _agent_client() as client:
        with pytest.raises(AgentKeyRefusedError) as exc:
            call(client)
        assert client.agent is not None
        assert client.agent.next_nonce(0) == 1, "a refused call consumed no nonce"
    assert exc.value.code == "AGENT_KEY_FORBIDDEN"
    assert (exc.value.method, exc.value.path) == (method, path)
    assert isinstance(exc.value, MissingCredentialsError)
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize(
    ("method", "path", "direct"),
    [
        ("POST", "/withdrawals", False),
        ("POST", "/withdrawals/", False),
        ("POST", "/account/withdraw", False),
        ("POST", "/bridge/withdrawals", True),
        ("PUT", "/bridge/withdrawals", True),
    ],
)
def test_withdrawals_are_refused_for_agent_keys(
    httpx_mock, method: str, path: str, direct: bool
) -> None:
    with _agent_client() as client:
        with pytest.raises(AgentKeyRefusedError, match="cannot withdraw") as exc:
            client._request(method, path, body={}, signed=True, direct=direct)
    assert exc.value.code == "AGENT_CANNOT_WITHDRAW"
    assert httpx_mock.get_requests() == []


def test_withdrawal_history_and_other_writes_stay_allowed(httpx_mock) -> None:
    httpx_mock.add_response(url="http://localhost:9090/withdrawals", json=[])
    httpx_mock.add_response(url="http://localhost:9090/api/v1/orders", method="DELETE", json={})
    httpx_mock.add_response(url="http://localhost:9090/ws/token", json={"token": "t"})
    with _agent_client() as client:
        client.fetch_withdrawals()
        client.cancel_all_orders()
        client.create_ws_token()
    assert len(httpx_mock.get_requests()) == 3


def test_hmac_client_is_not_subject_to_the_agent_wall(httpx_mock) -> None:
    httpx_mock.add_response(url="http://localhost:9090/agents", json=[])
    with Client(Network.LOCAL, api_key="nx_test", api_secret=_HMAC_SECRET) as client:
        client.fetch_agents()
    (req,) = httpx_mock.get_requests()
    assert req.headers["x-api-key"] == "nx_test"
    assert "x-agent" not in req.headers
