"""Bridge deposit flow: discover assets, get-or-create a deposit address, and
inspect deposits.

Send USDC/USDX to the printed address, then poll ``fetch_bridge_deposits`` (or
``fetch_bridge_deposit`` by id) until the deposit's ``status`` reaches
``credited``. Credentials come from the environment so they stay out of source.

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

        # 2. Get-or-create a deposit address on the first chain (idempotent).
        chain_name = assets.chains[0].chain
        addr = client.create_bridge_deposit_address(chain_name)
        print(f"\nDeposit {addr.accepts} to {addr.address} on {addr.chain}")
        print("Send USDC or USDX there; it will appear as a deposit below.\n")

        # 3. Inspect deposits. Poll this until the newest reaches credited/failed.
        deposits = client.fetch_bridge_deposits(limit=5, chain=chain_name)
        if not deposits:
            print("no deposits yet — send funds to the address above, then re-run.")
        for d in deposits:
            confs = (
                f"{d.confirmations}/{d.required_confirmations} confs"
                if d.confirmations is not None and d.required_confirmations is not None
                else "-"
            )
            print(f"{d.id} {d.asset} {d.amount} {d.status} ({confs})")


if __name__ == "__main__":
    main()
