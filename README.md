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

Requires Python **3.10+**. Depends only on [`httpx`](https://www.python-httpx.org/).

## Quick start

```python
from nexus_exchange import Client

with Client() as client:                 # defaults to the public gateway
    for market in client.fetch_markets():
        print(market.market_id)

    ticker = client.fetch_ticker("BTC-USDX-PERP")
    print(ticker.raw)
```

No credentials are needed for market data. See `examples/public_market_data.py`.

## What's supported

| Area | Status |
|---|---|
| List markets — `GET /markets/summary` | ✅ implemented |
| Ticker — `GET /markets/{id}/ticker` | ✅ implemented |
| Health — `GET /health` | ✅ implemented |
| HMAC request signing (the plumbing for authed calls) | ✅ implemented |
| Error taxonomy (terminal vs transient) | ✅ implemented |
| Account / positions / fills reads (signed) — `GET /account`, `/positions`, `/fills` | ✅ implemented |
| Trading — place / cancel orders (signed) — `POST /orders`, `DELETE /orders/{id}`, `DELETE /orders` | ✅ implemented |
| Open orders (signed) — `GET /orders` | ✅ implemented |
| Deposits / withdrawals | ❌ not yet |
| WebSocket streaming | ❌ not yet |
| Pagination helpers | ❌ not yet |
| Rate-limit-aware retry (`429` / `Retry-After`, token bucket) | ❌ not yet |
| Agent-key / OAuth auth | ❌ not yet |
| Richer typed models (Decimal prices/sizes) | 🟡 partial — models keep the full payload on `.raw` |

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
HMAC.

```python
from nexus_exchange import Client, Network

with Client(Network.LOCAL, api_key="nx_...", api_secret="<hex secret>") as client:
    account = client.fetch_account()
    positions = client.fetch_positions()

    resp = client.place_order(
        "BTC-USDX-PERP", side="Buy", quantity="0.1", price="65000"
    )
    order_id = resp["order"]["id"]
    client.cancel_order(order_id)
```

Anything not yet wrapped is still reachable through the low-level escape hatch
`Client._request(method, path, ..., signed=True)`.

## API version

This SDK targets a released version of the Exchange API spec, pinned in
[`.api-version`](./.api-version). The spec lives in
[`nexus-xyz/nexus-exchange-api`](https://github.com/nexus-xyz/nexus-exchange-api).

| SDK version | API spec |
|---|---|
| `0.1.x` | `v0.3.5` |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest          # tests (HMAC scheme, parsing, error mapping) — mocked, no network
ruff check .    # lint
mypy src        # types
```

## License

Dual-licensed under [MIT](./LICENSE-MIT) or [Apache-2.0](./LICENSE-APACHE), at
your option — same as the other Nexus Exchange SDKs.
