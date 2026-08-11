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

A fourth target is reachable without being named here:
:meth:`NetworkConfig.custom` builds a frozen config for a deployment this SDK
ships no hostname for (ENG-9826). It carries the same bundle as a named network
rather than a bare URL, because a URL alone is what lets a client report
play-funds guardrails while pointed at a real-funds host. Custom configs are
never inserted into :data:`_CONFIGS` and are not addressable by name, so the map
above stays the complete list of hosts this package ships.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from urllib.parse import urlsplit

__all__ = ["Funds", "Network", "NetworkConfig", "SigningDomain"]


class Funds(str, Enum):
    """Whose money a target moves. Three states, and ``UNKNOWN`` is a real one.

    A boolean cannot express a target whose funds were never declared, and that
    state has to exist: a caller-supplied base URL says nothing about what is
    behind it. Modelling it as ``False`` would make every guardrail lie in the
    direction that costs money.

    **Guard on** :attr:`PLAY` **positively — never negate** :attr:`REAL`::

        if config.funds is not Funds.PLAY:  # correct: UNKNOWN fails closed
            refuse()
        if config.funds is Funds.REAL:      # WRONG: UNKNOWN falls through as safe
            refuse()

    Whether a faucet exists is tracked separately (:attr:`NetworkConfig.has_faucet`):
    "not real money" does not imply "can mint more of it".
    """

    #: Orders here move real money.
    REAL = "real"
    #: Synthetic funds; losing them costs nothing.
    PLAY = "play"
    #: Undeclared. Treated as unsafe by every guard — never as play money.
    UNKNOWN = "unknown"


#: Characters allowed in a custom label. The label is a *key*: it namespaces
#: stored credentials, so it can reach a keyring entry or a path component. The
#: charset is what keeps one target's label from addressing another's secrets.
#:
#: ``\A``/``\Z`` rather than ``^``/``$`` is load-bearing — ``$`` also matches
#: before a trailing newline, so ``"dev\n"`` would pass an anchored ``^...$``
#: and carry the newline into whatever consumes the key.
_LABEL_PATTERN = re.compile(r"\A[A-Za-z0-9._-]+\Z")

#: Upper bound on a custom label, so it cannot overflow a filesystem or keyring
#: key. Chosen to match the sibling SDKs (ENG-9823) rather than any local limit.
_LABEL_MAX_LEN = 64

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


def _clean_label(label: object) -> str:
    """Normalize and validate a custom label, or explain exactly what is wrong.

    Surrounding whitespace is trimmed rather than rejected — it is invisible and
    almost always a copy-paste artefact. Everything else is refused, because this
    value keys stored credentials: ``../other`` escapes a directory, ``one/two``
    and ``one:two`` split a path or a keyring key, whitespace and control
    characters are ambiguous once written, and non-ASCII normalizes to different
    byte sequences on different systems — so two labels that look identical can
    address the same secret, or one target's label can address another's.
    """
    if not isinstance(label, str):
        raise TypeError(f"label must be a string (got {type(label).__name__})")
    cleaned = label.strip()
    if not cleaned:
        raise ValueError("label must not be empty: it is the key credentials are stored under")
    if len(cleaned) > _LABEL_MAX_LEN:
        raise ValueError(f"label must be at most {_LABEL_MAX_LEN} characters (got {len(cleaned)})")
    # Refused ahead of the charset test purely so the error names the real
    # problem: both are legal characters but neither is a usable path component.
    if cleaned in (".", ".."):
        raise ValueError(f"label must not be {cleaned!r}: it does not name a directory entry")
    if not _LABEL_PATTERN.match(cleaned):
        raise ValueError(
            f"label {label!r} must contain only ASCII letters, digits, '.', '_' or '-'. "
            f"It keys stored credentials, so a path separator, whitespace, a control "
            f"character or a non-ASCII character could let one target address another's."
        )
    return cleaned


def _clean_base_url(url: object, param: str) -> str:
    """Normalize and validate a caller-supplied base URL.

    Rejects what would otherwise fail late and confusingly: a missing scheme
    (``httpx`` raises at request time, long after construction), and a query or
    fragment, which would be *concatenated* with the request path — producing
    ``https://host?a=1/api/v1/orders`` and, worse, signing that string.

    Deliberately does **not** reject a path. A base under ``/api/exchange`` is a
    real, working topology (see :meth:`Client._resolve_base`), and refusing it is
    what made the gateway deploy unreachable (ENG-10095).
    """
    if not isinstance(url, str):
        raise TypeError(f"{param} must be a string (got {type(url).__name__})")
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        raise ValueError(f"{param} must be a non-empty URL")
    parts = urlsplit(cleaned)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"{param} must start with http:// or https:// (got {cleaned!r})")
    if not parts.netloc:
        raise ValueError(f"{param} must include a host (got {cleaned!r})")
    # Tested on the literal characters, not on `parts.query` / `parts.fragment`:
    # a trailing "?" parses as *no* query, so the parsed form would accept
    # "https://host/p?" and then build "https://host/p?/api/v1/orders".
    if "?" in cleaned or "#" in cleaned:
        raise ValueError(
            f"{param} must not carry a query or fragment (got {cleaned!r}): the request "
            f"path is appended to it, which would both send and sign a mangled URL."
        )
    return cleaned


def _check_chain_id(chain_id: object) -> None:
    """Reject a chain id that could never verify, at construction rather than at signing.

    ``None`` is allowed and means *unknown*: signing refuses later
    (:func:`nexus_exchange.auth._require_chain_id`), which is the documented rule
    for every network here. This only catches values that are wrong on their face,
    so the failure lands next to the typo instead of after an order is built.
    """
    if chain_id is None:
        return
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        raise TypeError(f"chain_id must be an integer or None (got {chain_id!r})")
    if chain_id < 1:
        raise ValueError(f"chain_id must be a positive integer (got {chain_id})")


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """Everything bundled with one network: targets, funds semantics, signing.

    Frozen and hashable-by-value, so an instance is safe to share across threads
    and cannot be mutated out from under a live client. The named configs are
    created once at import; :meth:`custom` returns a new instance and never
    touches the shared map, so there is no mutable global state to race on and
    nothing here needs a lock.

    Construct a custom target with :meth:`custom`, which normalizes its inputs.
    Building one directly is supported but validates without normalizing — the
    invariants below are enforced either way, so a config that exists is a config
    whose label is a safe key and whose funds are a declared :class:`Funds`.
    """

    #: Human-readable name. For a custom target this is also the key stored
    #: credentials are namespaced under, so it is charset-restricted; see
    #: :func:`_clean_label`.
    label: str

    #: Whose money this target moves. Tri-state on purpose — see :class:`Funds`,
    #: and guard on ``is Funds.PLAY`` rather than negating ``REAL``.
    funds: Funds

    #: Whether synthetic funds can be minted (``POST /account/credit``). Mainnet
    #: has no faucet, and the spec marks that operation testnet/local-only.
    has_faucet: bool

    #: The durable per-network REST base published by the spec's
    #: ``x-nexus-networks``. Informational: the hosted entries are **not
    #: resolvable yet** (DNS/TLS is separate infra, ENG-8155), and how this
    #: SDK's two request surfaces map onto it is not settled. Recorded so the
    #: published target is not lost; :attr:`base_url` is what actually gets used.
    #: A custom target has no spec entry, so :meth:`custom` mirrors its
    #: :attr:`base_url` here.
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

    #: Host root for the direct ``/api/v1`` surface, or ``None`` as above.
    direct_base_url: str | None

    def __post_init__(self) -> None:
        """Enforce the invariants on every instance, however it was built.

        :meth:`custom` cannot be the only gate: the dataclass is public, so a
        direct ``NetworkConfig(...)`` would otherwise be a way to smuggle in an
        unvalidated label — the one field whose validation is a security
        boundary rather than tidiness.
        """
        _clean_label(self.label)
        _check_chain_id(self.signing_domain.chain_id)
        # Not `isinstance`: `Funds` subclasses `str`, so a bare "real" would pass
        # an isinstance check against `str` and then fail every `is` comparison.
        if type(self.funds) is not Funds:
            raise TypeError(
                f"funds must be a Funds member, not {self.funds!r}. Pass Funds.REAL / "
                f"Funds.PLAY / Funds.UNKNOWN, or use NetworkConfig.custom(), which "
                f"accepts the string form and normalizes it."
            )
        # A faucet mints *synthetic* funds, so the combination is incoherent — and
        # it is the one incoherence that matters: `claim_credit` gates on
        # `has_faucet` alone, so allowing this would point the faucet helper at a
        # real-funds host. Refused here rather than guarded at every call site.
        if self.funds is Funds.REAL and self.has_faucet:
            raise ValueError(
                f"{self.label!r} declares real funds and a faucet: a faucet mints "
                f"synthetic funds, so it cannot exist on a real-funds target."
            )

    @classmethod
    def custom(
        cls,
        *,
        label: str,
        funds: Funds | str,
        base_url: str,
        direct_base_url: str | None = None,
        has_faucet: bool = False,
        chain_id: int | None = None,
        ws_market_data_url: str | None = None,
        ws_authenticated_url: str | None = None,
    ) -> NetworkConfig:
        """Build a frozen config for a deployment this SDK ships no hostname for.

        The caller supplies the target, so no host has to be published here to
        make it reachable (ENG-9823). The returned config is a first-class
        citizen — pass it as ``Client(network=...)`` and it drives both bases,
        the funds guardrails and the signing domain.

        ``label`` and ``funds`` are **required and have no defaults.**

        ``funds`` has none because there is no safe one. Defaulting to play makes
        every guardrail lie on a real-funds stage, which is the direction that
        costs money; defaulting to real makes a dev target unusable. Pass
        :attr:`Funds.UNKNOWN` when it genuinely is not known — the guards treat
        that as unsafe, which is the honest answer, not as a synonym for play.

        ``has_faucet`` defaults to ``False`` because a faucet is a property of the
        deployment, not of the funds: a play-funds stage need not have one, and
        assuming one routes ``claim_credit`` at an endpoint that is not there.

        ``chain_id`` defaults to ``None``, meaning *unknown*, and signing then
        refuses rather than guessing — the same rule the named networks follow.
        Read the live value from the target's ``/metadata``. The EIP-712 ``name``
        and ``version`` are contract-level constants, identical on every
        deployment, so they are deliberately not overridable here.

        The two WebSocket bases are informational and default to empty — this SDK
        ships no WebSocket client, so nothing is dialled on your behalf either way.

        ``direct_base_url`` falls back to ``base_url`` when omitted *or blank*,
        matching how a lone ``base_url`` covers both surfaces on the client and
        how a blank string is "unset" everywhere else here. Pass it explicitly for
        a deployment that keeps the gateway/direct split.
        """
        cleaned_base = _clean_base_url(base_url, "base_url")
        cleaned_direct = (
            _clean_base_url(direct_base_url, "direct_base_url")
            if (direct_base_url or "").strip()
            else cleaned_base
        )
        try:
            resolved_funds = Funds(funds)
        except ValueError:
            allowed = ", ".join(repr(f.value) for f in Funds)
            raise ValueError(
                f"funds must be one of {allowed} (got {funds!r}). It is required and "
                f"has no default: an undeclared target is {Funds.UNKNOWN.value!r}, "
                f"which every guard treats as unsafe."
            ) from None
        # Not `bool(...)`: this gates `claim_credit`, and coercion would read a
        # non-empty string like "false" as True — failing *open* on the one flag
        # whose whole job is to fail closed.
        if not isinstance(has_faucet, bool):
            raise TypeError(f"has_faucet must be True or False (got {has_faucet!r})")
        _check_chain_id(chain_id)
        return cls(
            label=_clean_label(label),
            funds=resolved_funds,
            has_faucet=has_faucet,
            published_rest_base=cleaned_base,
            ws_market_data_url=(ws_market_data_url or "").strip(),
            ws_authenticated_url=(ws_authenticated_url or "").strip(),
            signing_domain=SigningDomain(chain_id=chain_id),
            base_url=cleaned_base,
            direct_base_url=cleaned_direct,
        )


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
# The WebSocket bases are the spec's values as published and are not reachable
# yet either (there is no legacy WS base to keep using, and no WS client here to
# use one). They are recorded, not dialled — see `NetworkConfig`.
_CONFIGS: Mapping[str, NetworkConfig] = MappingProxyType(
    {
        "mainnet": NetworkConfig(
            label="Mainnet",
            funds=Funds.REAL,
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
            funds=Funds.PLAY,
            has_faucet=True,
            published_rest_base="https://api.testnet.nexus.xyz/v1",
            ws_market_data_url="wss://api.testnet.nexus.xyz/stream",
            ws_authenticated_url="wss://api.testnet.nexus.xyz/ws",
            signing_domain=SigningDomain(),
            base_url="https://exchange.nexus.xyz/api/exchange",
            direct_base_url="https://exchange.nexus.xyz",
        ),
        "local": NetworkConfig(
            label="Local",
            funds=Funds.PLAY,
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
    def funds(self) -> Funds:
        """Whose money this network moves.

        Replaces the former ``real_funds`` bool (ENG-9826). Deliberately not kept
        as a derived boolean: ``real_funds is False`` would read as "play money"
        for a target whose funds are merely undeclared, which is the one wrong
        answer that costs money. Guard with ``is Funds.PLAY``.
        """
        return self.config.funds

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
