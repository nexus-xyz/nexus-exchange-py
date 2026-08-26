# Examples

Runnable, copy-pasteable programs for the Nexus Exchange Python SDK. Each reads
its configuration from the environment — no secrets in source.

## Configuration

| Variable | Purpose |
| --- | --- |
| `NEXUS_BASE_URL` | Explicit base URL; overrides `NEXUS_NETWORK` (e.g. `http://localhost:9090`). |
| `NEXUS_NETWORK` | Named network: `mainnet` \| `testnet` (default) \| `local`. Signed examples default to `local` and **refuse** `mainnet` outright — it moves real funds. |
| `NEXUS_API_KEY` | HMAC key id (signed examples only). |
| `NEXUS_API_SECRET` | HMAC secret, hex (signed examples only). |

> `beta` is no longer a value here. It named a release *channel*, and ENG-6454
> replaced that axis with a network axis — which chain, and whose money. What
> `beta` meant is now an explicit base URL:
> `NEXUS_BASE_URL=https://beta.exchange.nexus.xyz/api/exchange`.

Run from the repo root so each program can import its sibling `_shared.py`:

```sh
python examples/public_market_data.py
```

## Programs

Every program in this directory, not only the ones added most recently — a
partial catalog reads as "these are the examples that exist" and sends people
looking for a flow that is already here.

| Example | Auth | Endpoints exercised |
| --- | --- | --- |
| `single_ticker.py` | none | `ticker` (one market) |
| `public_market_data.py` | none | `markets`, `ticker`, `orderbook`, `trades`, `candles` |
| `ccxt_market_data.py` | none | CCXT adapter: `load_markets`, `fetch_ticker`, `fetch_order_book`, `fetch_trades`, `fetch_ohlcv` |
| `account_and_positions.py` | HMAC | `account`, `positions`, `account/rate-limit` |
| `place_and_cancel_order.py` | HMAC | `POST /orders`, `GET /orders/{id}`, `GET /orders`, `DELETE /orders/{id}` |
| `fills_and_withdrawals.py` | HMAC | `fills`, `withdrawals` |
| `paginate_fills.py` | HMAC | `fills` (cursor pagination via `iter_my_trades` / `fetch_my_trades_page`) |
| `bridge_deposit.py` | HMAC | `bridge/assets`, `bridge/deposit-addresses`, `bridge/deposits` |
| `signed_request.py` | HMAC | low-level signed-request escape hatch (no typed method) |
| `wallet_auth.py` | wallet signature | `POST /auth/login` (EIP-191), `POST /agents/register` (EIP-712) |

`_shared.py` is a helper module, not a runnable program.

Most of these routes are served by the direct `/api/v1` service (the gateway is
being retired, ENG-4740); a few (`markets`, `withdrawals`, `GET /orders/{id}`)
remain on the legacy gateway. The client routes each method transparently — see
`endpoints.txt` for the authoritative split.

The public gateway proxies signed calls to the *site* account; for per-account
auth point `NEXUS_BASE_URL` at a direct gateway (e.g. `http://localhost:9090`).
See the top-level README.

Wallet-signed auth (`wallet_auth.py`, above) uses `Client.sign_in` /
`Client.register_agent` — a different credential model from the HMAC examples,
which authenticate with a static api key/secret pair. The WebSocket streaming
client is not built yet, so no streaming example is included here.
