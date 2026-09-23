"""Stream public trades over the WebSocket API — no credentials required.

    python examples/ws_trades.py [MARKET_ID]

Requires the ws extra:  pip install nexus-exchange[ws]

Targets the public testnet gateway by default. Override with NEXUS_BASE_URL
(e.g. http://localhost:9090) or NEXUS_NETWORK (mainnet|testnet|local); the
socket URL is derived from whichever one resolves, so the two stay in step.
Override the socket base directly with NEXUS_WS_URL when it is not co-located
with the REST base (e.g. wss://api.testnet.nexus.xyz/v1; the client appends /ws).

For account-scoped channels (orders / fills / positions / balances), pass a
`token_provider` that mints a fresh single-use token per (re)connect — the
tokens are single-use, so a lambda that re-mints is required rather than one
token captured once:

    from _shared import make_signed_client

    rest = make_signed_client()
    ws = WsClient(url, token_provider=lambda: rest.create_ws_token().token)
"""

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlsplit, urlunsplit

from _shared import make_client

from nexus_exchange import WsClient


def _ws_url(base_url: str) -> str:
    """Derive the socket base from the resolved REST base: https -> wss.

    Only the scheme changes. `WsClient` appends `/ws` itself, so appending it
    here too dialled `…/ws/ws`, which the host answers with a generic 401.
    """
    override = os.environ.get("NEXUS_WS_URL") or None
    if override:
        return override
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    # Netloc carries any port the base set; the path keeps any route prefix.
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    market = args[0] if args else "BTC-USDX-PERP"

    # make_client() resolves NEXUS_BASE_URL / NEXUS_NETWORK and prints the target.
    # The REST client is not used to stream; it is how this example stays on the
    # same target as every other one rather than hardcoding a second host map.
    with make_client() as client:
        url = _ws_url(client.base_url)

    async with WsClient(url) as ws:
        sub = ws.subscribe("trades", market=market)
        print(f"streaming trades for {market} from {url}/ws (ctrl-c to stop)…")
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
