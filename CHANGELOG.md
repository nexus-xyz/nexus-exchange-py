# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Typed `create_orders` return value (ENG-3976).** `Client.create_orders`
  (`POST /orders/batch`) now returns `list[BatchOrderResult]` — the spec's
  per-order tagged union (`outcome == "ok"` with `order`/`fills`, or
  `outcome == "err"` with `error`/`message`) — instead of the raw decoded
  JSON (`Any`). One result per submitted order, in request order; malformed
  response elements decode to `err`-shaped placeholders
  (`error == "malformed_result"`) rather than being dropped, so positional
  alignment with the request always holds.

  **Breaking:** `OrderResponse.fills` is now `list[Fill]` (was
  `list[dict[str, Any]]` — the spec has typed fills since `v0.5.0`).
  Consumers indexing fills as dicts (`fill["price"]`) must switch to
  attribute access (`fill.price`); the raw payload remains available via
  `OrderResponse.raw["fills"]`.

- **`/api/v1` direct-service routing (ENG-4946).** As the REST gateway is
  retired (ENG-4740), the migrated market-data and account/trading routes now
  target each backend service directly under an `/api/v1` prefix at the host
  root (`https://exchange.nexus.xyz`) instead of the `…/api/exchange` gateway.
  The HMAC signature now covers the full path including the prefix (e.g.
  `/api/v1/orders`). Public method names and signatures are unchanged. Routes
  with no `/api/v1` equivalent yet (`GET /markets`, `/health`, ADL history,
  `GET /orders/{id}`, deposits, keys/agents, WS tokens, admin tiers) stay on
  the legacy gateway. See `endpoints.txt` for the per-route split.

  Pins the Exchange API spec to `v0.6.2` (was `v0.4.0`).

- Pinned the Exchange API spec to `v0.4.0` (was `v0.3.5`).

  **Breaking (upstream spec):** `v0.4.0` renames the market summary
  `mark_price` field to `last_trade_price`. The field key in market-summary
  payloads (e.g. anything read through `.raw["mark_price"]`) is now
  `"last_trade_price"`. Consumers reading the raw payload must move to
  `.raw["last_trade_price"]`, otherwise they will hit a `KeyError` at runtime.
  There is no compatibility shim for the old key.
