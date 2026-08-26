"""Shared config for the runnable examples — no secrets in source.

Every example builds its ``Client`` from environment variables so the same
program runs against a local gateway, testnet, or mainnet with no code change:

    NEXUS_BASE_URL   URL override for the network named below — a modifier,
                     not a selector (e.g. http://localhost:9090)
    NEXUS_NETWORK    named network: mainnet | testnet (default) | local
    NEXUS_API_KEY    HMAC key id   (signed examples only)
    NEXUS_API_SECRET HMAC secret   (signed examples only; hex)

**A base URL does not declare whose money is behind it** (ENG-10095). The
factories below always pass ``NEXUS_BASE_URL`` *alongside* a named network, so
the client keeps that network's funds, faucet and signing domain and only sends
somewhere else — an override with ``NEXUS_NETWORK`` unset still reports
testnet's play-funds guardrails, whatever is actually at the far end. That is
the supported shape: a URL passed with a declared network is a modifier and
stays, while a URL *on its own* is the deprecated selector that resolves to
undeclared funds (ENG-10955).

So when the funds semantics have to be right, name them rather than implying
them with a URL::

    from nexus_exchange import Client, Funds, NetworkConfig

    beta = NetworkConfig.custom(
        label="beta",
        funds=Funds.UNKNOWN,  # that deploy's funds are not ours to assert
        base_url="https://beta.exchange.nexus.xyz/api/exchange",
    )
    Client(beta)

``beta`` is no longer a network: it named a release *channel*, and ENG-6454
replaced that axis with a network axis — which chain, and whose money. The
config above is what it became. Pointing ``NEXUS_BASE_URL`` at the same host
still reaches it from these examples, but under the named network's funds
rather than its own, which is the distinction this section exists to make.

The signed examples call ``make_signed_client`` which exits early with a hint
when credentials are absent, so they stay copy-pasteable and never hardcode a
key.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from nexus_exchange import Client, Funds, Network

#: Hosts a *signed* example is allowed to point at. Loopback, plus every host
#: reachable from a network that declares **play** funds.
#:
#: Derived from the ``Network`` enum rather than written out, so a new play-funds
#: network is allowed automatically and a new real-funds one is not. That
#: direction matters: the failure mode worth designing against is someone adding
#: a network and forgetting to update a list, and here forgetting refuses rather
#: than permits.
#:
#: Tested positively against :attr:`Funds.PLAY`, never by negating
#: :attr:`Funds.REAL` (ENG-9826 made funds tri-state). A network whose funds are
#: ``UNKNOWN`` contributes no host: undeclared is not the same as safe, and
#: negating ``REAL`` would have let it through — the exact inversion the
#: tri-state exists to prevent, and the one that costs money here.
#:
#: Deliberately excludes ``""``. ``urlparse(base_url).hostname`` falls back to
#: ``""`` below for a URL the guard cannot make sense of — e.g. one with no
#: netloc — and this set exists to refuse hosts it does not recognise, not to
#: wildcard the one it could not parse (PR #18 review, @Luc-Campos). Not
#: reachable through `Client` today, which rejects a schemeless
#: ``NEXUS_BASE_URL`` before this guard runs, but the guard should not depend on
#: that to stay closed.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _play_funds_hosts() -> frozenset[str]:
    hosts = set(_LOOPBACK_HOSTS)
    for network in Network:
        if network.funds is not Funds.PLAY:
            continue
        for url in (network.config.base_url, network.config.direct_base_url):
            if url:
                host = urlparse(url).hostname
                if host:
                    hosts.add(host)
    # `beta` is not a Network any more (ENG-6454), but this module documents it as
    # a NEXUS_BASE_URL override and networks.py's own deprecation note records that
    # it serves testnet. Allowed explicitly so the documented invocation keeps
    # working; it is a play-funds deploy, not a fourth network.
    hosts.add("beta.exchange.nexus.xyz")
    return frozenset(hosts)


def _network_from_env(default: Network) -> Network:
    """Resolve ``NEXUS_NETWORK``, failing with the valid set rather than a raw enum error.

    ENG-4107: this used to read ``Network.STABLE.value``, which ENG-6454 deleted —
    so every example raised ``AttributeError: type object 'Network' has no
    attribute 'STABLE'`` on import of the first client. Worth recording *why* it
    survived a merge, because the same hole is still open for the next rename
    (@Luc-Campos in review): ``git merge origin/main`` reported zero conflicts
    since ``_shared.py`` is a new file with no competing side, no test imports
    ``examples/``, and ``mypy`` is pointed at ``src`` only. Three guards, all
    silent. `tests/test_examples_import.py` is the one that now speaks.

    A bad value gets the valid set rather than ``ValueError: 'stable' is no
    longer a Network value``, because the person hitting this is following a
    README that may itself be stale.
    """
    raw = os.environ.get("NEXUS_NETWORK")
    if raw is None:
        return default
    try:
        return Network(raw)
    except ValueError:
        valid = ", ".join(n.value for n in Network)
        print(
            f"NEXUS_NETWORK={raw!r} is not a network. Valid values: {valid}.\n"
            "If you meant the retired 'beta' channel, it is now an explicit base URL: "
            "NEXUS_BASE_URL=https://beta.exchange.nexus.xyz/api/exchange",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


def make_client() -> Client:
    """Unauthenticated client for public market-data examples."""
    # `or None` treats a set-but-blank NEXUS_BASE_URL as unset, matching how the
    # client itself treats an empty base URL -- otherwise `NEXUS_BASE_URL=`
    # (no value) silently pins the empty string as an explicit override instead
    # of falling through to the network default.
    base_url = os.environ.get("NEXUS_BASE_URL") or None
    network = _network_from_env(Network.TESTNET)
    print(f"-> {base_url or network.base_url}")
    return Client(network=network, base_url=base_url)


def make_signed_client() -> Client:
    """HMAC-signed client; exits with a hint if credentials are not set."""
    api_key = os.environ.get("NEXUS_API_KEY")
    api_secret = os.environ.get("NEXUS_API_SECRET")
    if not (api_key and api_secret):
        print(
            "this example needs credentials; set NEXUS_API_KEY and NEXUS_API_SECRET "
            "(and usually NEXUS_BASE_URL=http://localhost:9090 for per-account auth).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    # See make_client's comment on `or None` -- same reasoning, same fix.
    base_url = os.environ.get("NEXUS_BASE_URL") or None
    # NEXUS_NETWORK is honored; the default differs from make_client (LOCAL, not
    # TESTNET) on purpose — a signed/trading example should default to local so it
    # can't accidentally place orders against a shared network.
    network = _network_from_env(Network.LOCAL)

    # ENG-4107: refuse real funds outright. The default above is the first line of
    # defence, but `NEXUS_NETWORK=mainnet` walks straight past it into
    # `place_and_cancel_order.py`, which places live orders.
    #
    # This is a guard rather than a comment because the situation changed: when
    # that default was chosen the worst case was "beta", and mainnet was not
    # nameable at all. It is now. `Network.MAINNET.base_url` is currently `None`
    # so construction happens to raise today — but that is DNS not having landed,
    # not a control, and it disappears the moment it does.
    #
    # Deliberately not overridable by another env var. An example is a teaching
    # artifact someone runs while reading; a real-funds trading script should be
    # written on purpose, not reached by exporting one variable.
    # Positive test on PLAY rather than `is Funds.REAL` (ENG-9826): funds are
    # tri-state now, and a target that never declared them must refuse here. The
    # negated form reads as "only mainnet is dangerous", which is how an
    # UNKNOWN-funds target would have walked through a guard that exists to stop
    # exactly that.
    if network.funds is not Funds.PLAY:
        reason = (
            "moves real funds"
            if network.funds is Funds.REAL
            else f"does not declare whose funds it moves (funds={network.funds.value!r})"
        )
        print(
            f"refusing to run a signed example against {network.value!r}, which "
            f"{reason}. Use NEXUS_NETWORK=local (default) or testnet.\n"
            "If you genuinely mean to trade real funds, write a script that says so "
            "rather than pointing an example at mainnet.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # ENG-4107 review (bitfalt, P1): the check above reads the *enum*, and
    # NEXUS_BASE_URL overrides the URL the enum would have supplied. So
    #
    #   NEXUS_API_KEY=... NEXUS_API_SECRET=... NEXUS_BASE_URL=https://api.nexus.xyz/v1
    #
    # with no NEXUS_NETWORK resolved to `Network.LOCAL`, passed the refusal above,
    # and pointed a live order at the mainnet host. The guard was checking the label
    # while the override decided the destination.
    #
    # Fails closed on the host rather than deny-listing mainnet's: mainnet's
    # base_url is still `None` pending DNS, so there is no real-funds URL to
    # deny-list, and a deny-list would have to be updated in lockstep with every
    # new host to keep working. An allowlist derived from the play-funds networks
    # refuses anything it does not recognise, which is the correct default for a
    # guard whose whole job is to not move real money by accident.
    if base_url is not None:
        host = urlparse(base_url).hostname or ""
        allowed = _play_funds_hosts()
        if host not in allowed:
            print(
                f"refusing to run a signed example against NEXUS_BASE_URL={base_url!r}.\n"
                f"{host!r} is not a known play-funds host, and a signed example places "
                "real orders.\n"
                f"Allowed hosts: {', '.join(sorted(h for h in allowed if h))}.\n"
                "If you genuinely mean to trade real funds, write a script that says so "
                "rather than pointing an example at a production host.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    print(f"-> {base_url or network.base_url}")
    return Client(network=network, base_url=base_url, api_key=api_key, api_secret=api_secret)
