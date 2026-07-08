# nexus-exchange (Python)

[![License](https://img.shields.io/badge/license-MIT%2FApache--2.0-blue.svg)](#license)

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
| Wallet-signed auth — `sign_in` (EIP-191) + `register_agent` (EIP-712) | ✅ implemented |
| CCXT-compatible adapter — public market data | ✅ implemented |
| Error taxonomy (terminal vs transient) | ✅ implemented |
| Typed money — `Decimal` prices/sizes (full payload still on `.raw` / `.info`) | ✅ implemented |
| Account reads — `GET /account`, `/positions`, `/fills`, `/withdrawals`, `/account/rate-limit` | ✅ implemented |
| Trading — `POST /orders`, `/orders/batch`; `GET /orders`, `/orders/{id}`; `DELETE /orders`, `/orders/{id}` | ✅ implemented |
| Funds — `POST /account/deposit`, `/account/credit` | ✅ implemented |
| Keys / agents / WS token — `/keys`, `/agents`, `POST /ws-tokens` | ✅ implemented |
| Admin tiers — `GET`/`PUT`/`DELETE /admin/tiers` | ✅ implemented |
| WebSocket streaming — `GET /ws` (async `WsClient`, multiplexed, auto-resume) | ✅ implemented (`pip install nexus-exchange[ws]`) |
| Pagination helpers | ❌ not yet |
| Rate-limit-aware retry (`429` / `Retry-After`, token bucket) | ❌ not yet |
| OAuth auth | ❌ not yet |

The hand-maintained coverage source of truth is [`endpoints.txt`](./endpoints.txt).
Anything not listed there is not wrapped yet — contributions welcome.

### Routing: direct `/api/v1` service vs. legacy gateway

As the REST gateway is retired (ENG-4740), backend services expose their own
REST API under an **`/api/v1`** prefix served at the host root
(`https://exchange.nexus.xyz`), rather than the `…/api/exchange` gateway. The
migrated market-data and account/trading routes now target this direct service;
the HMAC signature covers the full path (e.g. `/api/v1/orders`). Routes with no
`/api/v1` equivalent yet — `GET /markets`, `/health`, ADL history, `GET
/orders/{id}`, deposits, keys/agents, WS tokens and admin tiers — stay on the
legacy gateway. This split is internal; method names and signatures are
unchanged. A custom `base_url` overrides both bases, so point it at the service
root (e.g. `http://localhost:9090`), not a `/api/exchange` URL.

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

### Wallet-signed auth

The HMAC scheme above signs *requests* with an API key. The two wallet-authorized
flows are different: an EVM wallet key authorizes a **session** or an **agent
key**, with the signature carried in the request *body* (these POSTs are
themselves unauthenticated). This mirrors the
[Rust SDK](https://github.com/nexus-xyz/nexus-exchange-rs)'s `EthSigner` and the
digests are cross-checked, byte-for-byte, against the server's known-answer
vectors.

`EthSigner` is a pure signer — the caller supplies the private key (a library
pattern; there is no key prompt or file handling). It needs the
[`eth-account`](https://pypi.org/project/eth-account/) dependency, which ships
with the SDK.

```python
from nexus_exchange import Client, EthSigner

signer = EthSigner.from_hex("0x<wallet-private-key>")   # you own the key

with Client() as client:
    # EIP-191 personal_sign → POST /auth/login → session token.
    session = client.sign_in(signer)
    print(session.address, session.token)   # token is a secret

    # EIP-712 → POST /agents/register. expires_at_ms / nonce / chain_id are
    # caller-supplied; expiry must fall in [now + 1d, now + 90d].
    registration = signer.register_agent(
        agent="0x<agent-address>",
        expires_at_ms=1_782_000_000_000,
        nonce=1,
        chain_id=393,
        label="my-bot",
    )
    registered = client.register_agent(registration)
    print(registered.agent_address, registered.expires_at)
```

## CCXT compatibility

[CCXT](https://github.com/ccxt/ccxt) is the unified API the Python quant/retail
stack (freqtrade, hummingbot, bots) speaks. `nexus_exchange.ccxt_adapter`
exposes the exchange under CCXT's unified method names and return shapes, so
CCXT-shaped code can talk to Nexus with minimal changes.

This first increment covers `describe()` and public market data —
`fetch_markets`, `fetch_ticker`, `fetch_tickers`, `fetch_order_book`,
`fetch_ohlcv`, `fetch_trades`, plus `load_markets`. Private / trading methods
are a follow-up.

```python
from nexus_exchange.ccxt_adapter import NexusExchange

with NexusExchange() as ex:
    ex.load_markets()
    ticker = ex.fetch_ticker("BTC-USDX-PERP")     # unified ticker dict
    book = ex.fetch_order_book("BTC-USDX-PERP", limit=10)   # [price, amount] levels
    candles = ex.fetch_ohlcv("BTC-USDX-PERP", "1m", limit=100)  # [ts,o,h,l,c,v]
    trades = ex.fetch_trades("BTC-USDX-PERP", limit=50)
```

The adapter returns plain CCXT-shaped `dict`/`list` structures and does **not**
import or subclass `ccxt` — it follows CCXT's conventions without taking the
dependency. See `examples/ccxt_market_data.py`.

## API version

<!-- api-version-sync:start -->

Currently targets Exchange API spec **`v0.6.2`**.

<!-- api-version-sync:end -->

The pinned version lives in [`.api-version`](./.api-version); the spec itself is
published by
[`nexus-xyz/nexus-exchange-api`](https://github.com/nexus-xyz/nexus-exchange-api).
This repo does not vendor a copy — the `drift` CI check fetches the pinned
release to detect drift, and the scheduled `api-version-sync` workflow opens a PR
when a newer spec releases. The line above is bot-managed; the table below is
maintained by hand when an SDK release ships a new pin.

| SDK version | API spec |
|---|---|
| `0.1.x` | `v0.4.0` |
| `0.2.x` | `v0.6.2` |

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
