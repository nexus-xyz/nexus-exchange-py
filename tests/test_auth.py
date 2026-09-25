"""Wallet-signed auth tests.

The sign-in vectors are copied verbatim from the Rust SDK
(``nexus-exchange-rs`` ``src/auth/eth.rs``), which pins them against an
independent ethers v6 implementation. The ``RegisterAgent`` digest is the
server's own pinned vector (``agent_store::tests::eip712_register_agent_digest_pinned``,
alloy, salted with ``Network::Testnet`` since ENG-15643), with the same inputs.
Matching it proves the Python signer builds the exact domain the server
verifies: a wrong-but-self-consistent domain separator, type string, salt or
field order would fail. Auth correctness is critical, so these are the
load-bearing tests.
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
    Funds,
    LoginResponse,
    Network,
    NetworkConfig,
)
from nexus_exchange.auth import _parse_address, _register_agent_digest, _u64

# Canonical Hardhat/ethers account #0: a published, externally verifiable
# keypair. Pins keccak + pubkey-to-address derivation against a known vector.
TEST_KEY = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"

# The server's RegisterAgent digest inputs, verbatim.
KAT_AGENT = "0xaaaaaaaaaaaaaaaaaaaabbbbbbbbbbbbbbbbbbbb"
KAT_EXPIRES_MS = 1_700_000_000
KAT_NONCE = 1
KAT_CHAIN_ID = 20056
KAT_NETWORK = Network.TESTNET

SIGN_IN_DIGEST = "0x99efa412eaa32f8d4ad2be2cad8835efc063776eff7834ddd3a8e34da9cd6268"
SIGN_IN_SIG = (
    "0xff4ddf3b1af438fe00d02368ad8fa5fc5e57667e6826dbda3ddddc395a5287bb"
    "6eab0bc97652f6e7e1f08f665b868ca143da79e18dae8021799cdafc4af670ea1b"
)
# The server's pinned digest. The signature over it by TEST_KEY is pinned
# identically in the Rust and TypeScript SDKs (RFC 6979, three implementations).
REGISTER_DIGEST = "0x5a52159bdde9c9ba6c1880598078c3326e8e32ea39c93425baafc76590d2a902"
REGISTER_SIG = (
    "0x40cc533ba443982d33463c30426a3e81569d07d68be841daefb2bf6baf4c8904"
    "03efb48f19c76ab06bceec7530b149a5c91d71688f6c7009de47a99d2e68af951c"
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
        _parse_address(KAT_AGENT),
        KAT_EXPIRES_MS,
        KAT_NONCE,
        KAT_CHAIN_ID,
        KAT_NETWORK.signing_domain.salt,  # type: ignore[arg-type]
    )
    assert "0x" + digest.hex() == REGISTER_DIGEST


def test_register_agent_signature_matches_known_answer() -> None:
    req = signer().register_agent(
        KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, network=KAT_NETWORK
    )
    assert req.signature == REGISTER_SIG


def test_register_agent_recovers_to_wallet() -> None:
    req = signer().register_agent(
        KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, label="my-bot", network=KAT_NETWORK
    )
    assert req.wallet == TEST_ADDR
    assert req.agent == KAT_AGENT
    assert req.expires_at == KAT_EXPIRES_MS
    assert req.nonce == KAT_NONCE
    assert req.label == "my-bot"

    # Recover the wallet from the 65-byte r||s||v signature over the prehash,
    # via eth_keys directly — this proves the signature is over our digest.
    digest = _register_agent_digest(
        _parse_address(KAT_AGENT),
        KAT_EXPIRES_MS,
        KAT_NONCE,
        KAT_CHAIN_ID,
        KAT_NETWORK.signing_domain.salt,  # type: ignore[arg-type]
    )
    raw = bytes.fromhex(req.signature[2:])
    sig = keys.Signature(
        vrs=(raw[64] - 27, int.from_bytes(raw[0:32], "big"), int.from_bytes(raw[32:64], "big"))
    )
    pub = sig.recover_public_key_from_msg_hash(digest)
    assert "0x" + pub.to_canonical_address().hex() == TEST_ADDR


# Published in the spec's x-nexus-networks[*].signing_domain.salt.
SPEC_SALTS = {
    Network.TESTNET: "d992b760ba3914309086be769796784454b6684e49ebfe3005bb9455433b7c8e",
    Network.MAINNET: "7beafa94c8bfb8f1c1a43104a34f72c524268aafbfe83bff17485539345c66ff",
    Network.LOCAL: "98591f89798185a27bc859ebabeeae88a1ed96bfbdf2f01b32ac97474b024894",
}


@pytest.mark.parametrize("network", list(Network))
def test_network_salt_matches_the_spec(network: Network) -> None:
    salt = network.signing_domain.salt
    assert salt is not None
    assert salt.hex() == SPEC_SALTS[network]


def test_register_agent_signature_is_network_scoped() -> None:
    sigs = {
        n: signer().register_agent(KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, network=n)
        for n in Network
    }
    assert len(set(r.signature for r in sigs.values())) == len(Network)
    # A plain network name resolves to the same domain as the enum member.
    by_name = signer().register_agent(
        KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, network="testnet"
    )
    assert by_name.signature == REGISTER_SIG


def test_register_agent_refuses_a_target_with_no_salt() -> None:
    # A custom target names no network, so there is no salt; signing unsalted
    # would only produce a registration the server refuses.
    custom = NetworkConfig.custom(label="dev", funds=Funds.PLAY, base_url="http://localhost:1")
    assert custom.signing_domain.salt is None
    with pytest.raises(AuthError, match="no RegisterAgent signing salt"):
        signer().register_agent(KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, network=custom)


def test_register_agent_rejects_bad_agent_address() -> None:
    with pytest.raises(AuthError):
        signer().register_agent("0x1234", 1, 1, 1, network=KAT_NETWORK)


# -- uint64 field bounds (expiresAt / nonce) ------------------------------


def test_u64_encodes_full_32_byte_word() -> None:
    # uint64 is ABI-encoded into a full 32-byte word, so in-range values match
    # the uint256 encoding byte-for-byte (the digest KATs above stay valid).
    assert _u64(KAT_EXPIRES_MS) == KAT_EXPIRES_MS.to_bytes(32, "big")
    assert len(_u64(1)) == 32


def test_u64_rejects_out_of_range() -> None:
    # expiresAt / nonce are uint64 in the EIP-712 type; values >= 2**64 must be
    # rejected at the library boundary rather than silently truncated.
    with pytest.raises(AuthError):
        _u64(1 << 64)
    with pytest.raises(AuthError):
        _u64(-1)


def test_register_agent_rejects_out_of_range_expiry_or_nonce() -> None:
    with pytest.raises(AuthError):
        signer().register_agent(KAT_AGENT, 1 << 64, KAT_NONCE, KAT_CHAIN_ID, network=KAT_NETWORK)
    with pytest.raises(AuthError):
        signer().register_agent(
            KAT_AGENT, KAT_EXPIRES_MS, 1 << 64, KAT_CHAIN_ID, network=KAT_NETWORK
        )


# -- credential-safe reprs ------------------------------------------------


def test_eth_signer_repr_hides_key_material() -> None:
    r = repr(signer())
    assert r == f"EthSigner(address={TEST_ADDR!r})"
    # The private key must never appear in the repr.
    assert TEST_KEY not in r


def test_login_response_repr_redacts_token() -> None:
    resp = LoginResponse(token="a1b2c3d4e5f6deadbeef", address=TEST_ADDR)
    r = repr(resp)
    assert "a1b2c3d4e5f6deadbeef" not in r
    assert "<redacted>" in r
    assert TEST_ADDR in r
    # The token is still accessible when explicitly read.
    assert resp.token == "a1b2c3d4e5f6deadbeef"


def test_label_omitted_when_none() -> None:
    body = signer().register_agent(
        KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, network=KAT_NETWORK
    )
    assert "label" not in body.to_dict()


def test_label_present_when_set() -> None:
    body = (
        signer()
        .register_agent(
            KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, "bot", network=KAT_NETWORK
        )
        .to_dict()
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
        KAT_AGENT, KAT_EXPIRES_MS, KAT_NONCE, KAT_CHAIN_ID, "my-bot", network=KAT_NETWORK
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
