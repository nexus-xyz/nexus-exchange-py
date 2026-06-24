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
| Error taxonomy (terminal vs transient) | ✅ implemented |
| Typed money — `Decimal` prices/sizes (full payload still on `.raw` / `.info`) | ✅ implemented |
| Typed account / positions / balances reads | ❌ not yet |
| Trading — place / cancel orders | ❌ not yet |
| Deposits / withdrawals | ❌ not yet |
| WebSocket streaming | ❌ not yet |
| Pagination helpers | ❌ not yet |
| Rate-limit-aware retry (`429` / `Retry-After`, token bucket) | ❌ not yet |
| Agent-key registration (wallet → agent) | ✅ implemented |
| OAuth auth | ❌ not yet |

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
