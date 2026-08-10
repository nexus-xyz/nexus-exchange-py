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

with Client() as client:  # defaults to the public gateway
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
| Portfolio — `GET /account/state` (summary + positions, incl. `withdrawable`), `/account/summary`, `/account/fees`, `/account/portfolio-history` | ✅ implemented |
| Trading — `POST /orders`, `/orders/batch`; `GET /orders`, `/orders/{id}`; `DELETE /orders`, `/orders/{id}` | ✅ implemented |
| Funds — `POST /account/deposit`, `/account/credit` | ✅ implemented |
| Bridge — `GET /bridge/assets`, `/bridge/deposits`(`/{id}`); `POST`/`GET /bridge/deposit-addresses` | ✅ implemented (⚠️ see note) |
| Keys / agents / WS token — `/keys`, `/agents`, `POST /ws-tokens` | ✅ implemented |
| Admin tiers — `GET`/`PUT`/`DELETE /admin/tiers` | ✅ implemented |
| Cursor pagination — `cursor` + `X-Next-Cursor` on `/markets/{id}/trades`, `/fills` | ✅ implemented |
| WebSocket streaming | ❌ not yet |
| Rate-limit-aware retry (`429` / `Retry-After`, token bucket) | ❌ not yet |
| OAuth auth | ❌ not yet |

The hand-maintained coverage source of truth is [`endpoints.txt`](./endpoints.txt).
Anything not listed there is not wrapped yet — contributions welcome.

> ⚠️ **Implemented is not the same as deployed.** The bridge *reads* — `GET
> /bridge/assets`, `/bridge/deposits`, `/bridge/deposits/{id}` and `GET
> /bridge/deposit-addresses` — answer `404` (a JSON one, from the API) on testnet
> today: the routes are in the pinned spec but not yet deployed. `POST
> /bridge/deposit-addresses` is live. Measured 2026-08-10; every other operation
> in the table above is reachable. Nothing to fix in this SDK, but budget for the
> `ApiError` if you build on the bridge reads now.

### Networks

`Network` is the **network** axis — which chain, and whose money:

| `Network` | Funds | Faucet | Notes |
| --- | --- | --- | --- |
| `Network.TESTNET` | Play (synthetic USDX) | Yes | **Default.** The safe target for integration work and CI. |
| `Network.MAINNET` | **Real** | No | Collateral is USDX bridged from Ethereum Mainnet. |
| `Network.LOCAL` | Play | Yes | A locally run indexer. Not a public network. |

Each member bundles its config — REST bases, both WebSocket bases, funds
semantics and the EIP-712 signing domain:

```python
from nexus_exchange import Client, Network

Network.TESTNET.ws_market_data_url  # 'wss://api.testnet.nexus.xyz/stream'
Network.TESTNET.ws_authenticated_url  # 'wss://api.testnet.nexus.xyz/ws'
Network.MAINNET.real_funds  # True — branch on this, never on the host string

with Client(Network.TESTNET) as client:
    ...
```

The two WebSocket bases and `published_rest_base` are the spec's **durable**
per-network values, recorded here so they live in one place. The hosted ones do
not resolve yet (DNS is ENG-8155), and this SDK ships no WebSocket client, so
treat them as published targets rather than something to connect to today. What
the client actually sends to is `base_url` / `direct_base_url`.

Three things worth knowing before you pick one:

- **Mainnet has no default base URL yet.** Its host (`api.nexus.xyz`) is
  published but DNS is not live, so `Client(Network.MAINNET)` raises rather than
  guessing a real-funds target or quietly falling back to testnet. Pass
  `base_url=…` explicitly to target it.
- **Credentials never cross networks.** Session tokens, HMAC keys and agent keys
  are minted per network and are invalid on any other. Switching network means a
  new client *and* new credentials — never carry a signature, nonce or agent
  registration across.
- **The signing domain's `chain_id` is not published statically.** Read it from
  the edge's `/metadata` for the network you are on. `register_agent` refuses to
  sign without one rather than defaulting: a wrong domain either fails
  verification or produces a signature valid on a *different* network.

The retired `stable` / `beta` release channels were never networks. `stable`
became `Network.TESTNET` (same target); `beta` is now an explicit override:

```python
Client(base_url="https://beta.exchange.nexus.xyz/api/exchange")
```

### Routing: direct `/api/v1` service vs. legacy gateway

As the REST gateway is retired (ENG-4740), backend services expose their own
REST API under an **`/api/v1`** prefix. The migrated market-data and
account/trading routes target it; the HMAC signature covers the full prefixed
path (e.g. `/api/v1/orders`). Routes with no `/api/v1` equivalent yet — `GET
/markets`, `/health`, ADL history, `GET /orders/{id}`, deposits, keys/agents, WS
tokens and admin tiers — stay unprefixed. This split is internal; method names
and signatures are unchanged.

**Where that prefix is mounted is a property of the deploy, not of the prefix.**
On the hosted networks it sits *under* the gateway — `…/api/exchange/api/v1/…` is
the path that answers — so `base_url` and `direct_base_url` are the same value
there. A local service has no gateway in front of it, so both are the host root.
Requesting `/api/v1/…` at a hosted host root returns a **404 with an HTML body**,
which is the tell that the base is wrong: the web frontend answered and the API
never saw the request. (This SDK assumed the host root until ENG-9200, which
404'd 32 of its 53 operations, order placement included.)

A custom `base_url` overrides both bases, which is usually what you want. Pass
`direct_base_url` separately only for a deploy that answers `/api/v1` elsewhere,
and do not include `/api/v1` in it — the client appends the prefix, and a base
that already carries it is rejected at construction rather than left to sign a
doubled path.

#### If you are coming from another Nexus SDK

The field names differ, so line them up before copying a base URL across — the
two-URL split here is one field in the TypeScript client:

| Surface | Python | TypeScript | Resolves to (testnet) |
| --- | --- | --- | --- |
| Direct `/api/v1` surface | `direct_base_url` (the `/api/v1` prefix is added per request) | `baseUrl` (prefix included in the base) | `https://exchange.nexus.xyz/api/exchange` |
| Legacy unprefixed routes | `base_url` | *not modelled* | `https://exchange.nexus.xyz/api/exchange` |

So Python's two fields hold the same value on testnet, while TypeScript's
`baseUrl` is that value **plus** `/api/v1`. Copying a `baseUrl` into
`direct_base_url` is rejected at construction, since it would double the
prefix.

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

signer = EthSigner.from_hex("0x<wallet-private-key>")  # you own the key

with Client() as client:
    # EIP-191 personal_sign → POST /auth/login → session token.
    session = client.sign_in(signer)
    print(session.address, session.token)  # token is a secret

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

## Bridge

Deposit funds across chains via the `/bridge` surface (USDC/USDX in Phase A).
Get a deposit address (idempotent per account + chain), send funds, then poll a
deposit until `status` is `credited`:

```python
assets = client.fetch_bridge_assets()
addr = client.create_bridge_deposit_address(assets.chains[0].chain)
print(f"send USDC/USDX to {addr.address} on {addr.chain}")

deposits = client.fetch_bridge_deposits(limit=1, chain=addr.chain)
# deposits[0].status: "detected" | "confirming" | "credited" | "failed"
```

See [`examples/bridge_deposit.py`](./examples/bridge_deposit.py).

## Portfolio

One signed call returns the whole account state — summary aggregates plus every
open position, from a single coherent read:

```python
from nexus_exchange import PortfolioWindow

state = client.fetch_account_state()
print(state.summary.total_equity, state.summary.withdrawable)  # None if unreported
for pos in state.positions:
    # Enriched risk detail; None + a `*_error` reason when not derivable.
    print(pos.market_id, pos.notional_value, pos.roe, pos.funding_paid)
    print(pos.leverage, pos.leverage_error)  # None, "margin_state_not_mirrored"

summary = client.fetch_account_summary()  # the aggregates alone, no positions
print(summary.withdrawable)

fees = client.fetch_account_fees()
print(fees.maker_fee_bps, fees.taker_fee_bps)  # maker may be negative (a rebate)

history = client.fetch_portfolio_history(PortfolioWindow.WEEK, limit=100)
for point in history.points:  # oldest first
    print(point.timestamp_ms, point.equity, point.pnl, point.volume)
```

`withdrawable` is engine-authoritative free margin floored at zero. The
endpoints serving it fail closed with `502 authoritative_margin_unavailable`
(an `ApiError`) rather than returning a local estimate — that is transient, so
retry rather than substituting a self-computed figure.

Every money field is a `Decimal`, and decoding never invents one. A field the
spec marks optional decodes to `None` when unreported, never a defaulted `0`
that would read as a real balance — while a reported `"0"` stays `Decimal(0)`. A
field the spec marks **required** decodes strictly: if it is absent, `null` or
malformed, the call raises `DecodeError` (a `NexusExchangeError`, and a
`ValueError`) rather than handing back a plausible figure the server never sent.
That extends to lists — a malformed point or position raises instead of silently
dropping out of the series.

## Pagination

The list endpoints return a page of results plus an opaque cursor for the next
page, carried in the **`X-Next-Cursor`** response header (spec v0.7.2). Of the
five paginated endpoints this SDK wraps two: `GET /markets/{id}/trades` and
`GET /fills`.

`iter_*` walks every page for you, lazily — one request per page, driven by the
cursor:

```python
for fill in client.iter_my_trades(limit=500):  # limit = page size, not a total
    print(fill.id, fill.price, fill.size)

# Stop early and the requests stop with you.
for trade in client.iter_trades("BTC-USDX-PERP", limit=100, max_pages=5):
    ...
```

`fetch_*_page` is the manual form, for when the cursor has to outlive the
process (a resumable backfill):

```python
page = client.fetch_my_trades_page(limit=500)
save_checkpoint(page.next_cursor)  # None once page.is_last
page = client.fetch_my_trades_page(limit=500, cursor=load_checkpoint())
```

Cursors are opaque — never parse one. Termination rules:

- **No `X-Next-Cursor` header ⇒ the last page.** Not an error, and not a reason
  to retry.
- An **empty page that still carries a cursor is not the end** — a sparse window
  keeps paging.
- A server that hands back the **same** cursor it was given cannot advance, so
  the walk raises `PaginationError` instead of re-requesting one page forever. A
  silent stop would report a truncated history as complete.
- Nothing else bounds how far back a walk goes; pass `max_pages` when that
  matters.

`limit` sets the page size and is validated against **that endpoint's** spec
maximum before the request — 1000 for both trades and fills
(`TRADES_LIMIT_MAX` / `FILLS_LIMIT_MAX`). These are not interchangeable across
endpoints, and in particular the `366` bound belongs only to
`/account/portfolio-history` (`PORTFOLIO_LIMIT_MAX`), which is not paginated.

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
    ticker = ex.fetch_ticker("BTC-USDX-PERP")  # unified ticker dict
    book = ex.fetch_order_book("BTC-USDX-PERP", limit=10)  # [price, amount] levels
    candles = ex.fetch_ohlcv("BTC-USDX-PERP", "1m", limit=100)  # [ts,o,h,l,c,v]
    trades = ex.fetch_trades("BTC-USDX-PERP", limit=50)
```

The adapter returns plain CCXT-shaped `dict`/`list` structures and does **not**
import or subclass `ccxt` — it follows CCXT's conventions without taking the
dependency. See `examples/ccxt_market_data.py`.

## API version

<!-- api-version-sync:start -->

Currently targets Exchange API spec **`v0.7.3`**.

<!-- api-version-sync:end -->

The pinned version lives in [`.api-version`](./.api-version); the spec itself is
published by
[`nexus-xyz/nexus-exchange-api`](https://github.com/nexus-xyz/nexus-exchange-api).
This repo does not vendor a copy. Two CI checks keep the pin honest, answering
different questions:

- **`spec-drift`** — does the SDK still match the spec it *pins*? It fetches the
  pinned release and enforces, both ways, that every operation in
  [`endpoints.txt`](./endpoints.txt) exists in that spec and that the operations
  the client code requests are exactly that list.
- **`drift`** — is the pin still the *latest* release? It compares `.api-version`
  against the spec repo's newest tag.

The `spec-autobump` workflow opens the bump PR when a newer spec releases,
labelling it breaking or non-breaking from an
[oasdiff](https://github.com/oasdiff/oasdiff) classification; `spec-drift` runs on
that PR too, so a bump that would require SDK changes cannot land quietly. The
line above is bot-managed; the table below is maintained by hand when an SDK
release ships a new pin.

Every request advertises the pinned tag in an `X-Nexus-Api-Version` header (and
identifies itself with a `User-Agent: nexus-exchange-py/<version>`). Override the
advertised tag per client with `Client(api_version="vX.Y.Z")` if you need to
target a specific contract version.

| SDK version | API spec |
|---|---|
| `0.1.x` | `v0.4.0` |
| `0.2.x` | `v0.6.2` |
| `0.3.x` | `v0.7.1` |

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
python scripts/smoke.py                     # testnet (default; play funds)
python scripts/smoke.py --network local
python scripts/smoke.py --base-url http://localhost:9090
```

## License

Dual-licensed under [MIT](./LICENSE-MIT) or [Apache-2.0](./LICENSE-APACHE), at
your option — same as the other Nexus Exchange SDKs.
