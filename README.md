# nexus-exchange (Python)

Official Python SDK for the [Nexus Exchange](https://exchange.nexus.xyz) API — a
thin, typed wrapper over the public REST API.

> **⚠️ Experimental / in development.** This is an early skeleton. The surface is
> small and may change without notice; only the endpoints in the table below are
> implemented. For the complete, ahead-of-this surface use the
> [Rust SDK](https://github.com/nexus-xyz/nexus-exchange-rs). This SDK exists so
> agents and bots can be written in **Python or Rust** depending on the
> libraries they need.

## Install

```bash
pip install nexus-exchange   # once published; for now, install from source:
pip install git+https://github.com/nexus-xyz/nexus-exchange-py
```

Requires Python **3.10+**. Depends on [`httpx`](https://www.python-httpx.org/)
(REST) and [`websockets`](https://websockets.readthedocs.io/) (streaming).

## Quick start

```python
from nexus_exchange import Client

with Client() as client:                 # defaults to the public gateway
    for market in client.fetch_markets():
        print(market.market_id)

    ticker = client.fetch_ticker("BTC-USDX-PERP")
    print(ticker.last, ticker.mark_price)
```

No credentials are needed for market data. See `examples/public_market_data.py`.

## What's supported

| Area | Status |
|---|---|
| Markets — `GET /markets`, `/markets/summary`, `/tickers` | ✅ implemented |
| Ticker — `GET /markets/{id}/ticker` | ✅ implemented |
| Order book — `GET /markets/{id}/orderbook` | ✅ implemented |
| Trades — `GET /markets/{id}/trades` | ✅ implemented |
| OHLCV candles — `GET /markets/{id}/candles` | ✅ implemented |
| Funding / mark price / status — `GET /markets/{id}/{funding,mark-price,status}` | ✅ implemented |
| ADL events — `GET /markets/{id}/adl-events`, `/account/{addr}/adl-history` | ✅ implemented |
| Health — `GET /health` | ✅ implemented |
| HMAC request signing (the plumbing for authed calls) | ✅ implemented |
| Error taxonomy (terminal vs transient) | ✅ implemented |
| Typed money — `Decimal` prices/sizes (full payload still on `.raw` / `.info`) | ✅ implemented |
| Account reads — `GET /account`, `/positions`, `/fills`, `/withdrawals`, `/account/rate-limit` | ✅ implemented |
| Trading — `POST /orders`, `/orders/batch`; `GET /orders`, `/orders/{id}`; `DELETE /orders`, `/orders/{id}` | ✅ implemented |
| Funds — `POST /account/deposit`, `/account/credit` | ✅ implemented |
| Keys / agents / WS token — `/keys`, `/agents`, `POST /ws-tokens` | ✅ implemented |
| Admin tiers — `GET`/`PUT`/`DELETE /admin/tiers` | ✅ implemented |
| Wallet-signed auth flows — `POST /auth/login` (EIP-191), `/agents/register` (EIP-712) | ❌ not yet (needs an Ethereum signer dep) |
| WebSocket streaming — `GET /ws` (asyncio, reconnect + resume) | ✅ implemented |
| Pagination helpers | ❌ not yet |
| Rate-limit-aware retry (`429` / `Retry-After`, token bucket) | ❌ not yet |

The hand-maintained coverage source of truth is [`endpoints.txt`](./endpoints.txt).
Anything not listed there is not wrapped yet — contributions welcome.

## Authentication

Signed requests use the canonical HMAC-SHA256 scheme the exchange verifies:

```text
<timestamp>\n<METHOD>\n<path>\n<query>\n<sha256hex(body)>
```

signed with the hex-decoded secret, sent as `x-signature` with `x-api-key` and
`x-timestamp`. Pass `api_key` / `api_secret` to `Client`. Note the default public
gateway proxies signed calls to the *site* account; to act as a specific account,
point `base_url` (or `Network.LOCAL`) at a direct gateway that verifies client
HMAC. Typed authed methods are not built yet — `Client._request(..., signed=True)`
is the low-level escape hatch in the meantime.

## Streaming (WebSocket)

`Client.stream(...)` opens an **asyncio** WebSocket stream — the async counterpart
to the synchronous REST client. The background task connects, reconnects with
jittered exponential backoff, ponges heartbeats, re-subscribes each channel after
every reconnect, and resumes each stream from a `since` cursor. Async-iterate it
for decoded, typed frames; a `Lagged` item reports any frames dropped when a slow
consumer can't keep up (the queue is bounded, so the socket is never starved).

```python
import asyncio
from nexus_exchange import Client, Channel, Network

async def main() -> None:
    client = Client(Network.LOCAL)
    async with client.stream([Channel.trades("BTC-USDX-PERP")]) as stream:
        async for msg in stream:
            print(msg)        # Subscribed / Event / OutOfSync / ServerError / Lagged

asyncio.run(main())
```

Public channels (`Channel.trades`, `Channel.book`, `Channel.candles`) need no
credentials. Account channels (`Channel.orders`, `.fills`, `.positions`,
`.balances`) are private: pass `api_key` / `api_secret` and the client mints a
single-use `/ws-tokens` before each connection. The WS host is a separate origin
from the REST base; it is only known for `Network.LOCAL` today (production host
pending — ENG-3398), so pass `ws_url=...` to `stream()` for other networks.

## API version

This SDK targets a released version of the Exchange API spec, pinned in
[`.api-version`](./.api-version). The spec lives in
[`nexus-xyz/nexus-exchange-api`](https://github.com/nexus-xyz/nexus-exchange-api).

| SDK version | API spec |
|---|---|
| `0.1.x` | `v0.4.0` |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest          # tests — unit (mocked httpx) + an integration smoke over a
                # real loopback socket; both run offline, no network
ruff check .    # lint
mypy src        # types
```

`tests/test_integration_smoke.py` stands up a real local HTTP server and drives
a real `Client` against it (`fetch_markets` / `fetch_ticker` / `health_check`),
mirroring the Rust SDK's wiremock tests — so the transport, URL building, and
JSON decoding are exercised end to end, not just the mock layer.

For an opt-in round-trip against a **live** gateway (read-only, unauthenticated;
not run in CI), use the smoke script:

```bash
python scripts/smoke.py                  # stable public gateway
python scripts/smoke.py --network beta
python scripts/smoke.py --base-url http://localhost:9090
```

## License

Dual-licensed under [MIT](./LICENSE-MIT) or [Apache-2.0](./LICENSE-APACHE), at
your option — same as the other Nexus Exchange SDKs.
