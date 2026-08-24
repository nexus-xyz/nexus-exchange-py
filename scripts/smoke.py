#!/usr/bin/env python3
"""Opt-in live smoke test against a real Nexus Exchange gateway.

Unlike ``tests/test_integration_smoke.py`` (which serves canned responses over a
loopback socket and runs in CI), this hits a **real** gateway over the network,
so it is *not* run in CI by default — it is a manual / scheduled check that the
public API still answers the shapes the SDK expects.

Usage::

    python scripts/smoke.py                  # default: testnet, play funds
    python scripts/smoke.py --network local
    python scripts/smoke.py --base-url http://localhost:9090

It is read-only and unauthenticated: it lists markets, then fetches a ticker and
an order book for the first market. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import sys

from nexus_exchange import Client, Funds, Network


def run(network: Network, base_url: str | None) -> int:
    # `--base-url` overrides the network, so the bundled label and funds stop
    # describing what is actually being hit: a caller-supplied URL declares
    # nothing about what is behind it. That is `Funds.UNKNOWN`, not play money —
    # naming the network here would print the one wrong answer that costs money.
    if base_url:
        label, funds = "explicit base URL", Funds.UNKNOWN
    else:
        label, funds = network.label, network.funds
    target = base_url or network.base_url
    # Say REAL FUNDS positively and let every other state name itself, so an
    # UNKNOWN target is never displayed as play.
    described = "REAL FUNDS" if funds is Funds.REAL else f"{funds.value} funds"
    print(f"smoke: hitting {target} ({label}, {described})")
    with Client(network=network, base_url=base_url) as client:
        markets = client.fetch_markets()
        print(f"  fetch_markets: {len(markets)} markets")
        if not markets:
            print("  WARNING: no markets returned; skipping ticker check")
        else:
            first = markets[0].market_id
            ticker = client.fetch_ticker(first)
            print(f"  fetch_ticker({first}): last={ticker.last} mark={ticker.mark_price}")

            book = client.fetch_order_book(first)
            print(f"  fetch_order_book({first}): {len(book.bids)} bids / {len(book.asks)} asks")
    print("smoke: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--network",
        choices=[n.value for n in Network],
        default=Network.TESTNET.value,
        help="network to target (default: testnet — play funds)",
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
