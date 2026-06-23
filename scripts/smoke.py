#!/usr/bin/env python3
"""Opt-in live smoke test against a real Nexus Exchange gateway.

Unlike ``tests/test_integration_smoke.py`` (which serves canned responses over a
loopback socket and runs in CI), this hits a **real** gateway over the network,
so it is *not* run in CI by default — it is a manual / scheduled check that the
public API still answers the shapes the SDK expects.

Usage::

    python scripts/smoke.py                  # default: stable public gateway
    python scripts/smoke.py --network beta
    python scripts/smoke.py --base-url http://localhost:9090

It is read-only and unauthenticated: it lists markets, fetches a ticker for the
first market, and checks gateway health. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import sys

from nexus_exchange import Client, Network


def run(network: Network, base_url: str | None) -> int:
    target = base_url or network.base_url
    print(f"smoke: hitting {target}")
    with Client(network=network, base_url=base_url) as client:
        markets = client.fetch_markets()
        print(f"  fetch_markets: {len(markets)} markets")
        if not markets:
            print("  WARNING: no markets returned; skipping ticker check")
        else:
            first = markets[0].market_id
            ticker = client.fetch_ticker(first)
            print(f"  fetch_ticker({first}): {len(ticker.raw)} fields")

        health = client.health_check()
        print(f"  health_check: {health}")
    print("smoke: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--network",
        choices=[n.value for n in Network],
        default=Network.STABLE.value,
        help="named environment to target (default: stable)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="explicit base URL; overrides --network",
    )
    args = parser.parse_args()
    try:
        return run(Network(args.network), args.base_url)
    except Exception as exc:  # noqa: BLE001 — top-level smoke reporter
        print(f"smoke: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
