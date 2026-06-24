"""Stream public trades over the WebSocket API — asyncio, no credentials.

Connects to a local indexer (``Network.LOCAL``) and prints decoded frames as they
arrive. The stream reconnects transparently and resumes from a cursor; press
Ctrl-C to stop.

    python examples/ws_streaming.py
"""

from __future__ import annotations

import asyncio

from nexus_exchange import Channel, Client, Event, Lagged, Network


async def main() -> None:
    client = Client(Network.LOCAL)
    # For account channels (Channel.fills(), .orders(), ...) pass api_key /
    # api_secret and the client mints a single-use /ws-tokens per connection.
    async with client.stream([Channel.trades("BTC-USDX-PERP")]) as stream:
        async for msg in stream:
            if isinstance(msg, Event):
                print(f"[{msg.channel}] seq={msg.seq} {msg.payload}")
            elif isinstance(msg, Lagged):
                print(f"(lagged: dropped {msg.dropped} frames)")
            else:
                print(msg)  # Subscribed / Unsubscribed / OutOfSync / ServerError


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
