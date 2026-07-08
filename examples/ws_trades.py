"""Stream public trades over the WebSocket API — no credentials required.

Run with:  npx-style →  python examples/ws_trades.py [MARKET_ID] [--network beta]
(requires the ws extra:  pip install nexus-exchange[ws])

For account-scoped channels (orders / fills / positions / balances), pass a
`token_provider` that mints a fresh single-use token per (re)connect, e.g.:

    from nexus_exchange import Client
    rest = Client(network=Network.STABLE, api_key=..., api_secret=...)
    ws = WsClient(url, token_provider=lambda: rest.mint_web_socket_token().token)
"""

from __future__ import annotations

import asyncio
import sys

from nexus_exchange import Network, WsClient

# Derive the ws:// URL from the direct-service base (https -> wss), + /ws.
_WS_URL = {
    Network.STABLE: "wss://exchange.nexus.xyz",
    Network.BETA: "wss://beta.exchange.nexus.xyz",
    Network.LOCAL: "ws://localhost:9090",
}


async def main() -> None:
    argv = sys.argv[1:]
    net_idx = argv.index("--network") if "--network" in argv else -1
    net_arg = argv[net_idx + 1] if net_idx >= 0 else None
    network = {"beta": Network.BETA, "local": Network.LOCAL}.get(net_arg or "", Network.STABLE)
    market = next(
        (a for i, a in enumerate(argv) if not a.startswith("--") and i != net_idx + 1),
        "BTC-USDX-PERP",
    )

    async with WsClient(_WS_URL[network]) as ws:
        sub = ws.subscribe("trades", market=market)
        print(f"streaming trades for {market} (ctrl-c to stop)…")
        async for event in sub:
            if event.out_of_sync:
                print("  [out of sync — refetch via REST]")
                continue
            print(f"  seq={event.seq} {event.data}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
