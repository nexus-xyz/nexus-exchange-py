"""Wallet-signed auth tests.

The known-answer vectors are copied verbatim from the Rust SDK
(``nexus-exchange-rs`` ``src/auth/eth.rs``), which in turn pins them against an
independent ethers v6 implementation and cross-checks the EIP-712 digest against
the server's alloy ``register_agent_digest``. Matching them here proves the
Python signer is byte-for-byte interoperable with the server and the Rust SDK —
a wrong-but-self-consistent domain separator, type string, or field order would
fail. Auth correctness is critical, so these are the load-bearing tests.
"""

from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys import keys
from eth_utils import keccak

from nexus_exchange import (
    SIGN_IN_MESSAGE,
    AgentRegistered,
    AuthError,
    Client,
    EthSigner,
    LoginResponse,
    Network,
)
from nexus_exchange.auth import _parse_address, _register_agent_digest

# Canonical Hardhat/ethers account #0: a published, externally verifiable
# keypair. Pins keccak + pubkey-to-address derivation against a known vector.
TEST_KEY = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"

# Pinned vectors, identical to the Rust SDK's known-answer tests.
KAT_AGENT = "0x1234567890abcdef1234567890abcdef12345678"
KAT_EXPIRES_MS = 1_782_000_000_000
KAT_NONCE = 1
KAT_CHAIN_ID = 393

SIGN_IN_DIGEST = "0x99efa412eaa32f8d4ad2be2cad8835efc063776eff7834ddd3a8e34da9cd6268"
SIGN_IN_SIG = (
    "0xff4ddf3b1af438fe00d02368ad8fa5fc5e57667e6826dbda3ddddc395a5287bb"
    "6eab0bc97652f6e7e1f08f665b868ca143da79e18dae8021799cdafc4af670ea1b"
)
REGISTER_DIGEST = "0x356e6f3d741f48279c78b228d4ed9217eb49ad9179d549c618215be57817bfd6"
REGISTER_SIG = (
    "0x5df263ed6d1b619a72d436a01104f9036af6258cacf56dea973321cbe722a995"
    "50644eea6bf75656d48e982d2ce5db9ef13c4aced4539cf3c2ff87802b0197cc1b"
)


def signer() -> EthSigner:
    return EthSigner.from_hex(TEST_KEY)


# -- construction / address derivation -----------------------------------


def test_derives_known_address() -> None:
    assert signer().address == TEST_ADDR


def test_from_hex_accepts_0x_prefix() -> None:
    assert EthSigner.from_hex("0x" + TEST_KEY).address == TEST_ADDR


def test_checksum_address() -> None:
    # EIP-55 mixed case for the same account #0.
    assert signer().checksum_address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


@pytest.mark.parametrize("bad", ["zz", "00", "0x1234", ""])
def test_rejects_bad_key(bad: str) -> None:
    with pytest.raises(AuthError):
        EthSigner.from_hex(bad)


# -- EIP-191 sign_in known answer -----------------------------------------


def test_sign_in_digest_matches_known_answer() -> None:
    msg = b"\x19Ethereum Signed Message:\n" + str(len(SIGN_IN_MESSAGE)).encode()
    msg += SIGN_IN_MESSAGE.encode()
    assert "0x" + keccak(msg).hex() == SIGN_IN_DIGEST


def test_sign_in_signature_matches_known_answer() -> None:
    req = signer().sign_in()
    assert req.message == SIGN_IN_MESSAGE
    assert req.signature == SIGN_IN_SIG


def test_sign_in_recovers_to_signer() -> None:
    req = signer().sign_in()
    recovered = Account.recover_message(
        encode_defunct(text=SIGN_IN_MESSAGE), signature=req.signature
    )
    assert recovered.lower() == TEST_ADDR


# -- EIP-712 register_agent known answer ----------------------------------


def test_register_agent_digest_matches_known_answer() -> None:
    digest = _register_agent_digest(
        _parse_address(KAT_AGENT), KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID
    )
    assert "0x" + digest.hex() == REGISTER_DIGEST


def test_register_agent_signature_matches_known_answer() -> None:
    req = signer().register_agent(KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID)
    assert req.signature == REGISTER_SIG


def test_register_agent_recovers_to_wallet() -> None:
    req = signer().register_agent(
        KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, label="my-bot"
    )
    assert req.wallet == TEST_ADDR
    assert req.agent == KAT_AGENT
    assert req.expires_at == KAT_EXPIRES_MS
    assert req.nonce == KAT_NONCE
    assert req.label == "my-bot"

    # Recover the wallet from the 65-byte r||s||v signature over the prehash,
    # via eth_keys directly — this proves the signature is over our digest.
    digest = _register_agent_digest(
        _parse_address(KAT_AGENT), KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID
    )
    raw = bytes.fromhex(req.signature[2:])
    sig = keys.Signature(
        vrs=(raw[64] - 27, int.from_bytes(raw[0:32], "big"), int.from_bytes(raw[32:64], "big"))
    )
    pub = sig.recover_public_key_from_msg_hash(digest)
    assert "0x" + pub.to_canonical_address().hex() == TEST_ADDR


def test_register_agent_rejects_bad_agent_address() -> None:
    with pytest.raises(AuthError):
        signer().register_agent("0x1234", 1, 1, 1)


def test_label_omitted_when_none() -> None:
    body = signer().register_agent(KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID).to_dict()
    assert "label" not in body


def test_label_present_when_set() -> None:
    body = (
        signer().register_agent(KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, "bot").to_dict()
    )
    assert body["label"] == "bot"


# -- client wiring (mocked) -----------------------------------------------


def test_sign_in_posts_eip191_body_and_parses_token(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/auth/login",
        method="POST",
        json={"token": "a1b2c3d4e5f6", "address": TEST_ADDR},
    )
    with Client(Network.LOCAL) as client:
        resp = client.sign_in(signer())

    assert isinstance(resp, LoginResponse)
    assert resp.token == "a1b2c3d4e5f6"
    assert resp.address == TEST_ADDR

    req = httpx_mock.get_request()
    import json

    sent = json.loads(req.content)
    assert sent == {"message": SIGN_IN_MESSAGE, "signature": SIGN_IN_SIG}
    assert req.headers["content-type"] == "application/json"


def test_register_agent_posts_eip712_body_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://localhost:9090/agents/register",
        method="POST",
        json={"agent_address": KAT_AGENT, "expires_at": KAT_EXPIRES_MS},
    )
    registration = signer().register_agent(
        KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, "my-bot"
    )
    with Client(Network.LOCAL) as client:
        resp = client.register_agent(registration)

    assert isinstance(resp, AgentRegistered)
    assert resp.agent_address == KAT_AGENT
    assert resp.expires_at == KAT_EXPIRES_MS

    req = httpx_mock.get_request()
    import json

    sent = json.loads(req.content)
    assert sent == {
        "wallet": TEST_ADDR,
        "agent": KAT_AGENT,
        "expires_at": KAT_EXPIRES_MS,
        "nonce": KAT_NONCE,
        "signature": REGISTER_SIG,
        "label": "my-bot",
    }
