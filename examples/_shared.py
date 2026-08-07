"""Shared config for the runnable examples — no secrets in source.

Every example builds its ``Client`` from environment variables so the same
program runs against a local gateway, testnet, or mainnet with no code change:

    NEXUS_BASE_URL   explicit base URL, overrides NEXUS_NETWORK
                     (e.g. http://localhost:9090)
    NEXUS_NETWORK    named network: mainnet | testnet (default) | local
    NEXUS_API_KEY    HMAC key id   (signed examples only)
    NEXUS_API_SECRET HMAC secret   (signed examples only; hex)

``beta`` is no longer a network. It was a release channel, and ENG-6454 replaced
the channel axis with a network axis — which chain, and whose money. What ``beta``
meant is now an explicit override::

    NEXUS_BASE_URL=https://beta.exchange.nexus.xyz/api/exchange

The signed examples call ``make_signed_client`` which exits early with a hint
when credentials are absent, so they stay copy-pasteable and never hardcode a
key.
"""

from __future__ import annotations

import os
import sys

from nexus_exchange import Client, Network


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
    base_url = os.environ.get("NEXUS_BASE_URL")
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
    base_url = os.environ.get("NEXUS_BASE_URL")
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
    if network.real_funds:
        print(
            f"refusing to run a signed example against {network.value!r}, which moves "
            "real funds. Use NEXUS_NETWORK=local (default) or testnet.\n"
            "If you genuinely mean to trade real funds, write a script that says so "
            "rather than pointing an example at mainnet.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print(f"-> {base_url or network.base_url}")
    return Client(network=network, base_url=base_url, api_key=api_key, api_secret=api_secret)
