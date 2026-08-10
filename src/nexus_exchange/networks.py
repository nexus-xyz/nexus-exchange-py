"""The network axis: ``mainnet`` (real funds), ``testnet`` (play funds), ``local``.

This module is the SDK's copy of the spec's ``x-nexus-networks`` map (ENG-6442),
and it is deliberately the *only* copy. Everything network-shaped — hosts, the
WebSocket bases, the EIP-712 signing domain, whether a faucet exists — hangs off
:class:`NetworkConfig` here, so re-deciding a hostname (ENG-7809 may do exactly
that) is a one-file change.

Three rules are carried over from the spec verbatim, because each one exists to
stop a specific way of losing real money:

**Never derive a host by interpolating the network name.** Mainnet is off-pattern
on purpose — ``api.nexus.xyz``, not ``api.mainnet.nexus.xyz``. A template like
``api.{network}.nexus.xyz`` resolves for every environment that *can* be tested
and breaks only on real funds, the one environment nobody can rehearse. The map
below spells every host out, with mainnet as a named case.

**Credentials never cross networks.** Session tokens, HMAC API keys and agent
keys are minted per network and are invalid anywhere else, so a key leaked on
testnet cannot sign for real funds. One :class:`~nexus_exchange.Client` targets
one network; switching network means a new client *and* new credentials, never a
signature, nonce or agent registration carried across.

**An unrecognized network is real funds until proven otherwise.** The fail-safe
direction is to demand confirmation, never to assume play money — so an unknown
identifier raises here instead of resolving to something convenient.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

__all__ = ["Network", "NetworkConfig", "SigningDomain"]

#: EIP-712 domain ``name`` and ``version`` for ``POST /agents/register``. These
#: two have been stable across every published contract; ``chain_id`` is the one
#: that is per-network and server-authoritative.
_DOMAIN_NAME = "Nexus Exchange"
_DOMAIN_VERSION = "1"


@dataclass(frozen=True, slots=True)
class SigningDomain:
    """EIP-712 domain for one network.

    A distinct domain per network is what makes an action signed for one network
    invalid on another, so this is a security boundary rather than bookkeeping.

    ``chain_id`` is ``None`` whenever the value has not been published. That
    means *unknown*, *not* zero, and it is not licence to fall back to a default
    or to a value cached from another network: a client that cannot obtain a
    chain id must refuse to sign. A wrong domain either fails verification or —
    far worse — produces a signature that is valid somewhere else.

    Mainnet is the real-funds exchange running against **Ethereum Mainnet** via
    the USDX bridge, not a Nexus L1 chain, so a Nexus L1 chain id is never
    correct there. Read the live value from the edge's ``/metadata`` payload for
    the network you are connected to.
    """

    name: str = _DOMAIN_NAME
    version: str = _DOMAIN_VERSION
    chain_id: int | None = None


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """Everything bundled with one network: targets, funds semantics, signing.

    Frozen, and every instance is created once at import, so the map is safe to
    read from any thread and cannot be mutated out from under a live client.
    """

    #: Human-readable name, e.g. ``"Mainnet"``.
    label: str

    #: ``True`` when orders here move real money. The single flag to branch on
    #: before doing anything irreversible; never infer it from the host string.
    real_funds: bool

    #: Whether synthetic funds can be minted (``POST /account/credit``). Mainnet
    #: has no faucet, and the spec marks that operation testnet/local-only.
    has_faucet: bool

    #: The durable per-network REST base published by the spec's
    #: ``x-nexus-networks``. Informational: the hosted entries are **not
    #: resolvable yet** (DNS/TLS is separate infra, ENG-8155), and how this
    #: SDK's two request surfaces map onto it is not settled. Recorded so the
    #: published target is not lost; :attr:`base_url` is what actually gets used.
    published_rest_base: str

    #: The spec's WebSocket base for public market data (``/stream``).
    #:
    #: Informational, on the same terms as :attr:`published_rest_base`: these are
    #: the durable per-network hosts, and the hosted ones do **not resolve yet**
    #: (ENG-8155). This SDK ships no WebSocket client, so nothing here is dialled
    #: on your behalf — the value is published so it lives in one place, not
    #: because it can be connected to today. Both hosted entries are unreachable,
    #: mainnet included, which is why it is not ``None`` the way its REST bases
    #: are: there is no reachable predecessor to prefer, so there is no choice
    #: being hidden.
    ws_market_data_url: str

    #: The spec's WebSocket base for the authenticated stream (``/ws``), which
    #: takes a token minted over REST by ``POST /ws-tokens``. Informational and
    #: not yet resolvable — see :attr:`ws_market_data_url`.
    ws_authenticated_url: str

    #: EIP-712 domain for this network.
    signing_domain: SigningDomain

    #: Legacy ``/api/exchange`` gateway base the client sends to today, or
    #: ``None`` when this network has no working default yet. ``None`` is
    #: deliberate absence rather than a guess — see :class:`Network`.
    base_url: str | None

    #: Base the direct ``/api/v1`` surface is mounted under, or ``None`` as above.
    #: The client appends ``/api/v1`` itself, so this is the value *before* that
    #: prefix — **not** necessarily the host root.
    #:
    #: Where the mount lives is a property of the deploy and has to be measured,
    #: not derived from the prefix's name. On testnet it is under the gateway
    #: (``…/api/exchange/api/v1/…`` answers; the host-root form is caught by the
    #: web frontend and returns 404 *HTML*), which is the opposite of what this
    #: SDK assumed until ENG-9200. Locally there is no gateway in front of the
    #: service, so the host root is right there. An HTML 404 body is the tell that
    #: this value is wrong: it means the request never reached the API.
    direct_base_url: str | None


# The map. Spelled out, mainnet a named case, no interpolation anywhere.
#
# On the transitional REST bases: the hosted per-network hosts do not resolve
# yet, and the spec is explicit that clients should keep using the legacy base
# until they are live. So testnet keeps pointing at the gateway it has always
# used — the same URL the old `Network.STABLE` produced, which the spec now
# documents as serving testnet. Mainnet has no such predecessor: `exchange.
# nexus.xyz` is testnet, so there is nothing to fall back to and nothing safe to
# invent. Its bases are None, and the client says so plainly instead of
# resolving to a host that would quietly be the wrong network.
#
# On where `/api/v1` is mounted: it is under the gateway base on testnet, not at
# the host root. This was measured against the live deploy (ENG-9200), and the
# earlier host-root assumption 404'd 32 of this SDK's 53 operations — including
# order placement — with an HTML body, because the web frontend answered instead
# of the API. The prefix's *name* suggests a separate service; the deploy decides
# where it actually answers, so treat this as a per-network fact to verify rather
# than derive. Local runs the service with no gateway in front, so there the host
# root is correct and the two bases coincide for a different reason.
#
# The WebSocket bases are the spec's values as published and are not reachable
# yet either (there is no legacy WS base to keep using, and no WS client here to
# use one). They are recorded, not dialled — see `NetworkConfig`.
_CONFIGS: Mapping[str, NetworkConfig] = MappingProxyType(
    {
        "mainnet": NetworkConfig(
            label="Mainnet",
            real_funds=True,
            has_faucet=False,
            published_rest_base="https://api.nexus.xyz/v1",
            ws_market_data_url="wss://api.nexus.xyz/stream",
            ws_authenticated_url="wss://api.nexus.xyz/ws",
            signing_domain=SigningDomain(),
            base_url=None,
            direct_base_url=None,
        ),
        "testnet": NetworkConfig(
            label="Testnet",
            real_funds=False,
            has_faucet=True,
            published_rest_base="https://api.testnet.nexus.xyz/v1",
            ws_market_data_url="wss://api.testnet.nexus.xyz/stream",
            ws_authenticated_url="wss://api.testnet.nexus.xyz/ws",
            signing_domain=SigningDomain(),
            base_url="https://exchange.nexus.xyz/api/exchange",
            # Same base as the gateway: testnet mounts the direct /api/v1 surface
            # *under* /api/exchange. Measured, not assumed — see the comment above
            # the map and `NetworkConfig.direct_base_url`.
            direct_base_url="https://exchange.nexus.xyz/api/exchange",
        ),
        "local": NetworkConfig(
            label="Local",
            real_funds=False,
            has_faucet=True,
            published_rest_base="http://localhost:9090",
            ws_market_data_url="ws://localhost:9090/stream",
            ws_authenticated_url="ws://localhost:9090/ws",
            signing_domain=SigningDomain(),
            base_url="http://localhost:9090",
            direct_base_url="http://localhost:9090",
        ),
    }
)

# Values the old release-channel enum accepted, mapped to what to say now. Beta
# was never a network — it was a deploy of testnet — so it becomes a base_url
# override rather than a renamed member.
_RETIRED: Mapping[str, str] = MappingProxyType(
    {
        "stable": (
            "`stable` was a release channel, not a network. The base it targeted "
            "serves testnet, so `Network.TESTNET` is the direct replacement and "
            "keeps the same target."
        ),
        "beta": (
            "`beta` was a release channel, not a network, and is no longer a "
            "Network value. Target it explicitly instead:\n"
            '    Client(base_url="https://beta.exchange.nexus.xyz/api/exchange",\n'
            '           direct_base_url="https://beta.exchange.nexus.xyz")'
        ),
    }
)


class Network(str, Enum):
    """Which Nexus network to talk to.

    This is the *network* axis — which chain and whose money — and nothing else.
    It used to be ``{STABLE, BETA, LOCAL}``, which conflated a release channel
    with a network and left no way to name mainnet at all.

    ``TESTNET`` is the default everywhere in this SDK: defaulting to real funds
    is not a mistake worth being one keystroke away from.

    :attr:`MAINNET` currently has no default REST base. Its host is published but
    not yet resolvable, and inventing one would mean guessing a real-funds target
    that cannot be tested, so constructing a mainnet client without an explicit
    ``base_url`` raises instead. Filling the value in later is additive; changing
    a wrong default would not be.
    """

    MAINNET = "mainnet"
    TESTNET = "testnet"
    LOCAL = "local"

    @property
    def config(self) -> NetworkConfig:
        """The bundled config for this network."""
        return _CONFIGS[self.value]

    # Conveniences, so callers rarely need to reach through `.config`.
    @property
    def label(self) -> str:
        return self.config.label

    @property
    def real_funds(self) -> bool:
        """``True`` if orders on this network move real money."""
        return self.config.real_funds

    @property
    def has_faucet(self) -> bool:
        return self.config.has_faucet

    @property
    def base_url(self) -> str | None:
        """Legacy gateway base, or ``None`` when none is published yet."""
        return self.config.base_url

    @property
    def direct_base_url(self) -> str | None:
        """``/api/v1`` host root, or ``None`` when none is published yet."""
        return self.config.direct_base_url

    @property
    def ws_market_data_url(self) -> str:
        return self.config.ws_market_data_url

    @property
    def ws_authenticated_url(self) -> str:
        return self.config.ws_authenticated_url

    @property
    def signing_domain(self) -> SigningDomain:
        return self.config.signing_domain

    @classmethod
    def _missing_(cls, value: object) -> None:
        """Turn an unusable network identifier into a loud, specific error.

        Two cases, both of which used to resolve to something and must not now:
        a retired release channel gets migration instructions, and anything else
        is refused outright. The refusal is the point — an unrecognized network
        is treated as real funds, so the fail-safe is to stop rather than pick
        the friendliest-looking match.
        """
        if isinstance(value, str):
            hint = _RETIRED.get(value.strip().lower())
            if hint is not None:
                raise ValueError(f"{value!r} is no longer a Network value. {hint}")
        allowed = ", ".join(repr(n.value) for n in cls)
        raise ValueError(
            f"unknown network {value!r}; expected one of {allowed}. An unrecognized "
            f"network is treated as real funds, so this is refused rather than "
            f"resolved to a default."
        )
