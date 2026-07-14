# Examples

Runnable, copy-pasteable programs for the Nexus Exchange Python SDK. Each reads
its configuration from the environment — no secrets in source.

## Configuration

| Variable | Purpose |
| --- | --- |
| `NEXUS_BASE_URL` | Explicit base URL; overrides `NEXUS_NETWORK` (e.g. `http://localhost:9090`). |
| `NEXUS_NETWORK` | Named environment: `stable` (default) \| `beta` \| `local`. |
| `NEXUS_API_KEY` | HMAC key id (signed examples only). |
| `NEXUS_API_SECRET` | HMAC secret, hex (signed examples only). |

Run from the repo root so each program can import its sibling `_shared.py`:

```sh
python examples/public_market_data.py
```

## Programs

| Example | Auth | Endpoints exercised |
| --- | --- | --- |
| `public_market_data.py` | none | `markets`, `ticker`, `orderbook`, `trades`, `candles` |
| `account_and_positions.py` | HMAC | `account`, `positions`, `account/rate-limit` |
| `place_and_cancel_order.py` | HMAC | `POST /orders`, `GET /orders/{id}`, `GET /orders`, `DELETE /orders/{id}` |
| `fills_and_withdrawals.py` | HMAC | `fills`, `withdrawals` |

Most of these routes are served by the direct `/api/v1` service (the gateway is
being retired, ENG-4740); a few (`markets`, `withdrawals`, `GET /orders/{id}`)
remain on the legacy gateway. The client routes each method transparently — see
`endpoints.txt` for the authoritative split.

The public gateway proxies signed calls to the *site* account; for per-account
auth point `NEXUS_BASE_URL` at a direct gateway (e.g. `http://localhost:9090`).
See the top-level README.

Auth flows (`POST /auth/login`, `POST /agents/register`) and the WebSocket
streaming client are not yet on `main`, so no examples cover them here.
