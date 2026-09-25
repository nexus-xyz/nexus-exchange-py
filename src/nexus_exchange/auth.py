"""EVM signing: the two wallet-authorized auth flows, and agent-key requests.

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

:class:`AgentSigner` is the third piece: once a wallet has registered an agent
key, the agent signs each *request* itself with the ``x-agent`` /
``x-timestamp`` / ``x-nonce`` / ``x-signature`` headers. Unlike
:class:`EthSigner` it is stateful — it issues nonces — and it is installed on a
:class:`~nexus_exchange.Client` (``Client(agent=...)``) rather than producing a
body.

This is a *library* pattern: the caller supplies the private key. There is no
key-input prompt and no key file handling here — that is an application/CLI
concern, deliberately out of scope.

The digests are implemented by hand (rather than via ``eth_account``'s
``encode_typed_data``) so they pin the exact bytes the server's ``alloy``
``register_agent_digest`` verifies: domain ``{name: "Nexus Exchange",
version: "1", chainId, salt}`` with **no** ``verifyingContract``, where ``salt``
is ``keccak256(network name)`` (ENG-15643). The known-answer test pins the
server's own digest vector.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys.datatypes import PrivateKey
from eth_utils.address import to_checksum_address
from eth_utils.conversions import to_bytes
from eth_utils.crypto import keccak

from .errors import AuthError
from .networks import Network, NetworkConfig

__all__ = [
    "EthSigner",
    "AgentSigner",
    "agent_canonical_string",
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

    The :meth:`__repr__` redacts ``token`` (shown as ``<redacted>``) so the
    bearer token can't leak into logs or tracebacks if the object is printed.
    Read ``.token`` explicitly to use it.
    """

    #: Session bearer token (64-char hex). Redacted in ``repr``.
    token: str
    #: Ethereum address recovered from the login signature (``0x``-prefixed).
    address: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LoginResponse:
        return cls(token=str(d.get("token", "")), address=str(d.get("address", "")))

    def __repr__(self) -> str:
        # Never render the session token. Show a redaction marker (rather than
        # omitting the field) so it's clear a token is present but withheld.
        token = "<redacted>" if self.token else "''"
        return f"LoginResponse(token={token}, address={self.address!r})"


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
    """Left-pad a non-negative integer into a 32-byte big-endian ABI word.

    For the ``uint256`` ABI type (here, the domain ``chainId``).
    """
    if value < 0 or value >= 1 << 256:
        raise AuthError("value out of uint256 range")
    return value.to_bytes(32, "big")


def _u64(value: int) -> bytes:
    """Encode a ``uint64`` value as a 32-byte big-endian ABI word.

    ABI-encodes integers in a full 32-byte word regardless of declared width, so
    the wire bytes match ``_u256`` for in-range values. The tighter ``uint64``
    bound is enforced here so an out-of-range ``expiresAt``/``nonce`` is caught
    at the library boundary (matching the EIP-712 ``uint64`` field types) rather
    than being silently truncated by the server.
    """
    if value < 0 or value >= 1 << 64:
        raise AuthError("value out of uint64 range")
    return value.to_bytes(32, "big")


def _address_word(addr: bytes) -> bytes:
    """Right-align a 20-byte address into a 32-byte ABI word."""
    return b"\x00" * 12 + addr


def _require_chain_id(chain_id: object) -> None:
    """Refuse to sign without a real, positive chain id.

    Mandated by the spec's ``SigningDomain``: a null or absent ``chain_id``
    means the server has not published one, *not* zero, and is not an invitation
    to fall back to a default or to a value cached from another network. The
    domain is the only thing making a signature network-specific, so signing
    under a guessed one either fails verification or — the case worth blocking —
    yields a signature that is valid somewhere it was never meant to be.

    ``bool`` is rejected explicitly: it is an ``int`` subclass, so ``True``
    would otherwise sign under chain id 1 (Ethereum Mainnet).
    """
    if chain_id is None:
        raise AuthError(
            "chain_id is required to sign: the signing domain is per-network and "
            "server-authoritative. Read `signing_domain.chain_id` from the edge's "
            "/metadata for the network you are on; do not guess or reuse another "
            "network's value."
        )
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        raise AuthError("chain_id must be an integer")
    if chain_id < 1:
        raise AuthError(f"chain_id must be a positive integer (got {chain_id})")


def _register_salt(network: Network | NetworkConfig | str) -> bytes:
    """The network's ``RegisterAgent`` domain salt, or refuse to sign.

    The server verifies ``RegisterAgent`` under a domain salted with its own
    network name, with no unsalted fallback (ENG-15643). A custom target names
    no network, so there is no salt to sign under, and an unsalted signature
    would only be refused by the server as ``signer_mismatch``.
    """
    config = Network(network).config if isinstance(network, str) else network
    salt = config.signing_domain.salt
    if salt is None:
        raise AuthError(
            f"no RegisterAgent signing salt is known for network {config.label!r}: "
            "the server binds agent registrations to its network name "
            "(salt = keccak256(network)), and a custom target names none. Pass "
            "network=Network.MAINNET, Network.TESTNET or Network.LOCAL, whichever "
            "the target server runs as. The salt only names the network; the "
            "client you send the registration through still picks the host."
        )
    return salt


def _register_agent_digest(
    agent: bytes, expires_at: int, nonce: int, chain_id: int, salt: bytes
) -> bytes:
    """EIP-712 digest for ``RegisterAgent{agent, expiresAt, nonce}``.

    ``keccak256(0x1901 || domainSeparator || hashStruct(message))`` under the
    ``Nexus Exchange`` domain with ``salt`` and no ``verifyingContract``. Matches
    the server's ``agent_store::eip712::register_agent_digest``.
    """
    if len(salt) != 32:
        raise AuthError("salt must be 32 bytes")
    domain_type_hash = keccak(
        text="EIP712Domain(string name,string version,uint256 chainId,bytes32 salt)"
    )
    domain_separator = keccak(
        domain_type_hash
        + keccak(text=_EIP712_DOMAIN_NAME)
        + keccak(text=_EIP712_DOMAIN_VERSION)
        + _u256(chain_id)
        + salt
    )

    struct_type_hash = keccak(text="RegisterAgent(address agent,uint64 expiresAt,uint64 nonce)")
    hash_struct = keccak(struct_type_hash + _address_word(agent) + _u64(expires_at) + _u64(nonce))

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

    def __repr__(self) -> str:
        # Explicit, key-free repr: only the public address is shown so the
        # private key can never leak into logs/tracebacks. (eth_keys.PrivateKey
        # already suppresses its own repr, but this makes the intent explicit.)
        return f"EthSigner(address={self.address!r})"

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
        *,
        network: Network | NetworkConfig | str,
    ) -> AgentRegistration:
        """Sign an agent-key registration with EIP-712.

        Yields the ``POST /agents/register`` body.

        ``agent`` is the agent keypair's address (``0x``-prefixed, 20 bytes).
        ``expires_at_ms`` and ``nonce`` are caller-supplied — the spec expects
        the expiry in ``[now+1d, now+90d]`` and suggests the current Unix-ms
        timestamp as a safe starting nonce. ``chain_id`` is the EIP-712 domain
        chain id (the exchange's chain id); it is part of the signed payload, so
        it must match what the server verifies against.

        ``chain_id`` is **per network** and must be the live value for the
        network you are connected to — read it from the edge's ``/metadata``
        payload. :attr:`Network.signing_domain
        <nexus_exchange.Network.signing_domain>` carries ``chain_id=None``
        because the static map does not publish it, and ``None`` (or ``0``) is
        refused here rather than signed: a wrong domain either fails
        verification or produces a signature that is valid on a *different*
        network. Never reuse a chain id observed on another network.

        ``network`` is the network the registration is for, and is required: the
        server salts the ``RegisterAgent`` domain with its network name
        (``salt = keccak256(network)``, ENG-15643), so a registration verifies
        only on the network it was signed for. The salt comes from
        :attr:`SigningDomain.salt <nexus_exchange.SigningDomain.salt>`; a custom
        target has none and is refused.
        """
        _require_chain_id(chain_id)
        salt = _register_salt(network)
        agent_addr = _parse_address(agent)
        digest = _register_agent_digest(agent_addr, expires_at_ms, nonce, chain_id, salt)
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


#: Largest value an ``x-nonce`` / ``x-timestamp`` can carry: the server parses
#: both as ``u64``.
_U64_MAX = (1 << 64) - 1


def agent_canonical_string(
    method: str, path: str, query: str, body: bytes, timestamp_ms: int, nonce: int
) -> str:
    """The exact string an agent key signs (spec ``agentAuth``).

    ``{METHOD}\\n{path}\\n{query}\\n{sha256hex(body)}\\n{timestamp_ms}\\n{nonce}``

    Six LF-joined fields, no trailing newline. The method is upper-cased and
    comes **first** — a different order from the HMAC scheme's
    ``{ts}\\n{METHOD}\\n...``, so the two builders are not interchangeable. An
    empty ``query`` stays in the string as an empty line. Matches the server's
    ``exchange_sec_utils::signing::canonical_string``.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, query, body_hash, str(timestamp_ms), str(nonce)])


class AgentSigner:
    """Signs REST requests with a registered agent key.

    An agent key is a secp256k1 keypair a wallet delegates trading to with
    ``POST /agents/register`` (:meth:`EthSigner.register_agent`, then
    :meth:`Client.register_agent <nexus_exchange.Client.register_agent>`).
    Register :attr:`address`, then install the signer on a client::

        agent = AgentSigner.from_hex("0x<agent-private-key>")
        client = Client(Network.TESTNET, agent=agent)

    Every ``signed`` request that client sends then carries the four
    ``agentAuth`` headers instead of the HMAC ones.

    **Wire format** (a port of the server verifier, pinned by the spec's
    ``x-nexus-test-vectors``): the digest is ``keccak256`` of
    :func:`agent_canonical_string` with **no EIP-191 prefix** — a
    ``personal_sign`` signature recovers to some other address and ``401`` s.
    The signature is deterministic (RFC 6979), low-S, ``0x`` + 65-byte
    ``r||s||v`` with ``v in {27, 28}``. ``x-timestamp`` must be within ±30 s of
    the server clock.

    **Nonces.** On writes the server requires each agent's nonce to be strictly
    greater than the last one it accepted; reads parse the nonce but neither
    check nor consume it. The signer issues ``max(last + 1, timestamp_ms)``
    under a lock, so nonces are unique and increasing per signer even across
    threads, and a restarted process resumes above the nonces it issued before.
    Every attempt of a retried request is re-signed with a fresh timestamp and a
    fresh nonce.

    **Concurrent writes can still be refused as replays** (ENG-17010). Nonces
    are increasing when *issued*, not when they *arrive*: two writes from one
    signer in flight together can reach the server out of order, and the lower
    nonce is then refused with the same opaque ``401`` as a bad signature. This
    SDK does not serialize requests for you. Until ENG-17010 settles the fix,
    keep **one write in flight per agent key**, or register a separate agent
    key per concurrent writer. The same goes for sharing one agent key across
    processes: their nonces can collide, so register one agent per process.

    **Agent keys are trade-only.** They cannot withdraw or move funds off the
    account by any route, and cannot manage agent credentials. A client holding
    an agent key refuses those operations locally with
    :class:`~nexus_exchange.AgentKeyRefusedError` rather than spending a request
    on a guaranteed ``403`` — see :class:`~nexus_exchange.Client`.

    The caller owns the key material; this class does not read it from the
    environment, a file, or a prompt, and its ``repr`` shows only the address.
    """

    __slots__ = ("_key", "_address", "_lock", "_last_nonce")

    def __init__(self, private_key: PrivateKey, address: bytes) -> None:
        # Prefer AgentSigner.from_hex; see EthSigner.__init__.
        self._key = private_key
        self._address = address
        self._lock = threading.Lock()
        self._last_nonce = 0

    @classmethod
    def from_hex(cls, private_key: str) -> AgentSigner:
        """Build a signer from the agent's 32-byte hex private key (``0x`` optional).

        Raises :class:`~nexus_exchange.AuthError` if the key is not 32 bytes of
        valid hex or is not a valid secp256k1 scalar.
        """
        wallet = EthSigner.from_hex(private_key)
        return cls(wallet._key, wallet._address)

    def __repr__(self) -> str:
        return f"AgentSigner(address={self.address!r})"

    @property
    def address(self) -> str:
        """The agent's address, lowercase ``0x`` hex — the value sent as
        ``x-agent`` and the one to register with ``POST /agents/register``."""
        return "0x" + self._address.hex()

    def next_nonce(self, floor: int) -> int:
        """Issue the next nonce, ``max(last + 1, floor)``. Thread-safe.

        ``floor`` is the request's timestamp in unix ms. Concurrent callers
        always receive distinct, strictly increasing values.
        """
        with self._lock:
            nonce = max(self._last_nonce + 1, floor)
            if nonce > _U64_MAX:
                raise AuthError("agent nonce space exhausted (exceeds u64)")
            self._last_nonce = nonce
            return nonce

    def sign_request(
        self,
        method: str,
        path: str,
        query: str,
        body: bytes,
        timestamp_ms: int,
        nonce: int,
    ) -> dict[str, str]:
        """The four ``agentAuth`` headers for one request, with an explicit nonce.

        Deterministic and stateless — it neither issues nor records a nonce —
        which is what lets the known-answer tests pin it. :meth:`headers` is the
        form the client uses.

        ``path`` is the path the server authenticates (including ``/api/v1``
        on the direct surface, excluding the base URL's own path); ``query`` is
        the raw query string exactly as sent, without ``?``.
        """
        for name, value in (("timestamp_ms", timestamp_ms), ("nonce", nonce)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise AuthError(f"{name} must be an integer")
            if not 0 <= value <= _U64_MAX:
                raise AuthError(f"{name} out of u64 range")
        canonical = agent_canonical_string(method, path, query, body, timestamp_ms, nonce)
        digest = keccak(text=canonical)
        # Raw prehash, no EIP-191: see EthSigner.register_agent on why
        # `unsafe_sign_hash` is the right call for a digest we built ourselves.
        # eth_keys emits canonical low-S signatures, which the server requires.
        signed = Account.unsafe_sign_hash(digest, self._key.to_bytes())
        return {
            "x-agent": self.address,
            "x-timestamp": str(timestamp_ms),
            "x-nonce": str(nonce),
            "x-signature": _to_0x(signed.signature),
        }

    def headers(
        self, method: str, path: str, query: str, body: bytes, timestamp_ms: int
    ) -> dict[str, str]:
        """Sign a request, issuing a fresh nonce floored at ``timestamp_ms``."""
        return self.sign_request(
            method, path, query, body, timestamp_ms, self.next_nonce(timestamp_ms)
        )


def _to_0x(value: Any) -> str:
    """Render an ``eth_account`` signature (``HexBytes``/bytes) as ``0x`` hex."""
    return "0x" + to_bytes(value).hex()
