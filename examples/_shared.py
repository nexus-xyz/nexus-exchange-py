"""Shared config for the runnable examples — no secrets in source.

Every example builds its ``Client`` from environment variables so the same
program runs against a local gateway, beta, or the public stable gateway with no
code change:

    NEXUS_BASE_URL   explicit base URL, overrides NEXUS_NETWORK
                     (e.g. http://localhost:9090)
    NEXUS_NETWORK    named environment: stable (default) | beta | local
    NEXUS_API_KEY    HMAC key id   (signed examples only)
    NEXUS_API_SECRET HMAC secret   (signed examples only; hex)

The signed examples call ``make_signed_client`` which exits early with a hint
when credentials are absent, so they stay copy-pasteable and never hardcode a
key.
"""

from __future__ import annotations

import os
import sys

from nexus_exchange import Client, Network


def make_client() -> Client:
    """Unauthenticated client for public market-data examples."""
    base_url = os.environ.get("NEXUS_BASE_URL")
    network = Network(os.environ.get("NEXUS_NETWORK", Network.STABLE.value))
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
    # STABLE) on purpose — a signed/trading example should default to local so it
    # can't accidentally place real orders against a public network.
    network = Network(os.environ.get("NEXUS_NETWORK", Network.LOCAL.value))
    print(f"-> {base_url or network.base_url}")
    return Client(network=network, base_url=base_url, api_key=api_key, api_secret=api_secret)
