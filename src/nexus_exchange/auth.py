"""EVM wallet signing for the two wallet-authorized auth flows.

This mirrors the Rust SDK's ``EthSigner`` (``nexus-exchange-rs``): a pure,
deterministic, side-effect-free signer that produces the *signed request bodies*
for the two unauthenticated, wallet-authorized endpoints:

- :meth:`EthSigner.sign_in` — EIP-191 ``personal_sign`` over a fixed message,
  the body for ``POST /auth/login``.
- :meth:`EthSigner.register_agent` — EIP-712 typed-data over
  ``RegisterAgent(address agent, uint64 expiresAt, uint64 nonce)``, the body for
  ``POST /agents/register``.

The signer is ignorant of the network: it never sends anything, never stores a
session, and carries no clock — nonces and expiries are caller-supplied. Hand
the returned body to :class:`~nexus_exchange.Client` to send it.

This is a *library* pattern: the caller supplies the private key. There is no
key-input prompt and no key file handling here — that is an application/CLI
concern, deliberately out of scope.

The digests are implemented by hand (rather than via ``eth_account``'s
``encode_typed_data``) so they pin the exact bytes the server's ``alloy``
``register_agent_digest`` verifies: domain ``{name: "Nexus Exchange",
version: "1", chainId}`` with **no** ``verifyingContract``. Known-answer tests
cross-check the digests and signatures against the Rust SDK's pinned vectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys.datatypes import PrivateKey
from eth_utils.address import to_checksum_address
from eth_utils.conversions import to_bytes
from eth_utils.crypto import keccak

from .errors import AuthError

__all__ = [
    "EthSigner",
    "LoginRequest",
    "AgentRegistration",
    "LoginResponse",
    "AgentRegistered",
    "SIGN_IN_MESSAGE",
]

#: The exact, fixed message the API requires for EIP-191 session login.
SIGN_IN_MESSAGE = "Sign in to Nexus Exchange"

#: EIP-712 domain ``name``, per the ``/agents/register`` spec.
_EIP712_DOMAIN_NAME = "Nexus Exchange"
#: EIP-712 domain ``version``, per the ``/agents/register`` spec.
_EIP712_DOMAIN_VERSION = "1"


@dataclass(frozen=True)
class LoginRequest:
    """Signed body for ``POST /auth/login`` (EIP-191 session login).

    Produced by :meth:`EthSigner.sign_in`; hand it to
    :meth:`~nexus_exchange.Client.sign_in`.
    """

    #: The signed message — always :data:`SIGN_IN_MESSAGE`.
    message: str
    #: EIP-191 ``personal_sign`` signature, ``0x``-prefixed (65 bytes).
    signature: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the JSON body the endpoint expects."""
        return {"message": self.message, "signature": self.signature}


@dataclass(frozen=True)
class AgentRegistration:
    """Signed body for ``POST /agents/register`` (EIP-712 agent registration).

    Produced by :meth:`EthSigner.register_agent`; hand it to
    :meth:`~nexus_exchange.Client.register_agent`.
    """

    #: Owner wallet address (``0x``-prefixed, lowercase).
    wallet: str
    #: Agent address being registered (``0x``-prefixed, lowercase).
    agent: str
    #: Expiry as Unix milliseconds.
    expires_at: int
    #: Monotonic nonce.
    nonce: int
    #: EIP-712 signature over ``RegisterAgent{agent, expiresAt, nonce}``,
    #: ``0x``-prefixed (65 bytes).
    signature: str
    #: Optional human-readable label.
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the JSON body the endpoint expects.

        ``label`` is omitted entirely when ``None`` (matching the Rust SDK's
        ``skip_serializing_if``).
        """
        body: dict[str, Any] = {
            "wallet": self.wallet,
            "agent": self.agent,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "signature": self.signature,
        }
        if self.label is not None:
            body["label"] = self.label
        return body


@dataclass(frozen=True)
class LoginResponse:
    """Parsed response from ``POST /auth/login``.

    ``token`` is a session bearer token — treat it as a secret. Pass it to a
    future session-authenticated client; this SDK does not store it for you.
    """

    #: Session bearer token (64-char hex).
    token: str
    #: Ethereum address recovered from the login signature (``0x``-prefixed).
    address: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LoginResponse:
        return cls(token=str(d.get("token", "")), address=str(d.get("address", "")))


@dataclass(frozen=True)
class AgentRegistered:
    """Parsed response from ``POST /agents/register``."""

    #: The registered agent's address (``0x``-prefixed).
    agent_address: str
    #: Expiry as Unix milliseconds.
    expires_at: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentRegistered:
        addr = d.get("agent_address") or d.get("agent") or ""
        return cls(agent_address=str(addr), expires_at=int(d.get("expires_at", 0)))


def _strip_0x(s: str) -> str:
    if s[:2] in ("0x", "0X"):
        return s[2:]
    return s


def _parse_address(s: str) -> bytes:
    """Parse a ``0x``-prefixed 20-byte hex address into raw bytes."""
    try:
        raw = bytes.fromhex(_strip_0x(s))
    except ValueError as exc:
        raise AuthError("agent address must be hex") from exc
    if len(raw) != 20:
        raise AuthError("agent address must be 20 bytes")
    return raw


def _u256(value: int) -> bytes:
    """Left-pad a non-negative integer into a 32-byte big-endian ABI word."""
    if value < 0 or value >= 1 << 256:
        raise AuthError("value out of uint256 range")
    return value.to_bytes(32, "big")


def _address_word(addr: bytes) -> bytes:
    """Right-align a 20-byte address into a 32-byte ABI word."""
    return b"\x00" * 12 + addr


def _register_agent_digest(agent: bytes, expires_at: int, nonce: int, chain_id: int) -> bytes:
    """EIP-712 digest for ``RegisterAgent{agent, expiresAt, nonce}``.

    ``keccak256(0x1901 || domainSeparator || hashStruct(message))`` under the
    ``Nexus Exchange`` domain (no ``verifyingContract``). Matches the server's
    ``agent_store::eip712::register_agent_digest``.
    """
    domain_type_hash = keccak(text="EIP712Domain(string name,string version,uint256 chainId)")
    domain_separator = keccak(
        domain_type_hash
        + keccak(text=_EIP712_DOMAIN_NAME)
        + keccak(text=_EIP712_DOMAIN_VERSION)
        + _u256(chain_id)
    )

    struct_type_hash = keccak(text="RegisterAgent(address agent,uint64 expiresAt,uint64 nonce)")
    hash_struct = keccak(struct_type_hash + _address_word(agent) + _u256(expires_at) + _u256(nonce))

    return keccak(b"\x19\x01" + domain_separator + hash_struct)


class EthSigner:
    """An EVM wallet key that authorizes the wallet-signed auth flows.

    Construct from a 32-byte hex private key with :meth:`from_hex`. The key is
    validated and the Ethereum address derived once at construction. The signer
    is deterministic (RFC 6979) and produces ``0x``-prefixed 65-byte ``r||s||v``
    signatures with ``v in {27, 28}`` (Ethereum convention), matching the Rust
    SDK byte-for-byte.

    The caller owns the key material; this class does not read it from the
    environment, a file, or a prompt.
    """

    __slots__ = ("_key", "_address")

    def __init__(self, private_key: PrivateKey, address: bytes) -> None:
        # Prefer EthSigner.from_hex; the constructor takes already-validated
        # parts so the hex-decode path stays in one place.
        self._key = private_key
        self._address = address

    @classmethod
    def from_hex(cls, private_key: str) -> EthSigner:
        """Build a signer from a 32-byte hex private key (``0x`` optional).

        Raises :class:`~nexus_exchange.AuthError` if the key is not 32 bytes of
        valid hex or is not a valid secp256k1 scalar.
        """
        try:
            raw = bytes.fromhex(_strip_0x(private_key))
        except ValueError as exc:
            raise AuthError("private key must be hex") from exc
        if len(raw) != 32:
            raise AuthError("private key must be 32 bytes")
        try:
            key = PrivateKey(raw)
        except Exception as exc:  # eth_keys raises ValidationError on bad scalar
            raise AuthError("invalid secp256k1 private key") from exc
        address = key.public_key.to_canonical_address()
        return cls(key, address)

    @property
    def address(self) -> str:
        """The wallet's Ethereum address, lowercase ``0x``-prefixed hex."""
        return "0x" + self._address.hex()

    @property
    def checksum_address(self) -> str:
        """The wallet's address in EIP-55 mixed-case checksum form."""
        return to_checksum_address(self._address)

    def sign_in(self) -> LoginRequest:
        """Sign the fixed login message with EIP-191 ``personal_sign``.

        Yields the ``POST /auth/login`` body.
        """
        signable = encode_defunct(text=SIGN_IN_MESSAGE)
        signed = Account.sign_message(signable, self._key.to_bytes())
        return LoginRequest(message=SIGN_IN_MESSAGE, signature=_to_0x(signed.signature))

    def register_agent(
        self,
        agent: str,
        expires_at_ms: int,
        nonce: int,
        chain_id: int,
        label: str | None = None,
    ) -> AgentRegistration:
        """Sign an agent-key registration with EIP-712.

        Yields the ``POST /agents/register`` body.

        ``agent`` is the agent keypair's address (``0x``-prefixed, 20 bytes).
        ``expires_at_ms`` and ``nonce`` are caller-supplied — the spec expects
        the expiry in ``[now+1d, now+90d]`` and suggests the current Unix-ms
        timestamp as a safe starting nonce. ``chain_id`` is the EIP-712 domain
        chain id (the exchange's chain id); it is part of the signed payload, so
        it must match what the server verifies against.
        """
        agent_addr = _parse_address(agent)
        digest = _register_agent_digest(agent_addr, expires_at_ms, nonce, chain_id)
        # ``unsafe_sign_hash`` signs a 32-byte prehash directly. It is "unsafe"
        # in the general sense that a raw digest hides what is being signed —
        # but here the digest is a domain-separated EIP-712 hash we computed
        # ourselves, so there is nothing hidden. This is the only way to sign a
        # precomputed EIP-712 digest with the exact bytes the server verifies.
        signed = Account.unsafe_sign_hash(digest, self._key.to_bytes())
        return AgentRegistration(
            wallet=self.address,
            agent="0x" + agent_addr.hex(),
            expires_at=expires_at_ms,
            nonce=nonce,
            signature=_to_0x(signed.signature),
            label=label,
        )


def _to_0x(value: Any) -> str:
    """Render an ``eth_account`` signature (``HexBytes``/bytes) as ``0x`` hex."""
    return "0x" + to_bytes(value).hex()
