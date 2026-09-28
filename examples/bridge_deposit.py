"""Bridge deposit flow: discover assets and inspect deposits.

Poll ``fetch_bridge_deposits`` (or ``fetch_bridge_deposit`` by id) until a
deposit's ``status`` reaches ``credited``. The SDK cannot issue a deposit
address: ``/bridge/deposit-addresses`` is not served (ENG-11460).
Credentials come from the environment so they stay out of source.

    export NEXUS_API_KEY=...      # hex api key
    export NEXUS_API_SECRET=...   # hex api secret
    python examples/bridge_deposit.py
"""

from __future__ import annotations

import os

from nexus_exchange import Client


def main() -> None:
    api_key = os.environ.get("NEXUS_API_KEY")
    api_secret = os.environ.get("NEXUS_API_SECRET")
    if not (api_key and api_secret):
        raise SystemExit("set NEXUS_API_KEY and NEXUS_API_SECRET to run this example")

    with Client(api_key=api_key, api_secret=api_secret) as client:
        # 1. Discover bridgeable chains and assets.
        assets = client.fetch_bridge_assets()
        for chain in assets.chains:
            symbols = [a.symbol for a in chain.deposit_assets]
            print(f"{chain.chain:<10} deposits: {symbols}")

        if not assets.chains:
            raise SystemExit("no bridgeable chains available")

        # 2. Inspect deposits on the first chain.
        chain_name = assets.chains[0].chain
        # Poll this until the newest reaches credited/failed.
        deposits = client.fetch_bridge_deposits(limit=5, chain=chain_name)
        if not deposits:
            print(f"no deposits yet on {chain_name}.")
        for d in deposits:
            confs = (
                f"{d.confirmations}/{d.required_confirmations} confs"
                if d.confirmations is not None and d.required_confirmations is not None
                else "-"
            )
            print(f"{d.id} {d.asset} {d.amount} {d.status} ({confs})")


if __name__ == "__main__":
    main()
