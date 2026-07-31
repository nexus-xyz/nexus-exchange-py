# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Portfolio-parity endpoints and fields (ENG-6459).** Surfaces the
  portfolio-parity additions from Exchange API v0.7.2:
  - `fetch_account_state` (`GET /api/v1/account/state`) — the consolidated
    single-call snapshot (`AccountState`: summary aggregates + every open
    position, from one coherent read), replacing an `/account/summary` +
    `/positions` pair. The new `AccountPortfolioSummary` carries
    **`withdrawable`** — engine-authoritative free margin floored at zero.
  - `fetch_account_summary` (`GET /api/v1/account/summary`) — the same
    `AccountPortfolioSummary` aggregates without the position list, for callers
    that only need `withdrawable` / equity and not a full snapshot.
  - `fetch_account_fees` (`GET /api/v1/account/fees`) — the effective fee
    schedule (`AccountFees`: maker/taker bps, tier, rolling 30-day volume and
    its `volume_30d_estimated` flag, discounts). `maker_fee_bps` may be
    negative — a rebate paid to the maker.
  - `fetch_portfolio_history` (`GET /api/v1/account/portfolio-history`) — the
    equity / cumulative-PnL / cumulative-volume time series
    (`PortfolioHistory` + `PortfolioPoint`) over a `PortfolioWindow`
    (`day` | `week` | `month` | `all`, default `day`), oldest first. The
    `window` and `limit` arguments are validated client-side, so a bad value
    raises `ValueError` instead of spending a signed request on a `400`.
  - `Position` gains the enriched risk detail — `leverage`, `notional_value`,
    `roe`, `margin_used`, `max_leverage`, `funding_paid` (paid-positive) — each
    with its machine-readable `*_error` companion. A field the server cannot
    derive, or that an older deployment omits, is `None` rather than a
    defaulted `0`, so a missing figure never reads as a real one. Added after
    `raw` with defaults, so the existing field order is unchanged.

  `withdrawable` and the consolidated state are derived from the
  engine-authoritative margin view, which **fails closed**: HTTP 502
  (`authoritative_margin_unavailable`) surfaces as `ApiError` rather than a
  locally-estimated balance.
- **`DecodeError` in the error taxonomy.** Raised when a 2xx body does not match
  the contract — a spec-`required` field absent, `null`, non-finite or the wrong
  shape. Subclasses both `NexusExchangeError` (so `except NexusExchangeError`
  catches a malformed payload, which strict decoding previously escaped as a
  bare `ValueError`) and `ValueError` (so existing handlers keep working). A
  plain `ValueError` now means *caller* error — a bad `window` or `limit`,
  raised before the request is signed — so the two are distinguishable by type.
- **Account cancel-on-disconnect methods (ENG-6132).** `fetch_cancel_on_disconnect`
  (`GET /api/v1/account/cancel-on-disconnect`) and `set_cancel_on_disconnect`
  (`PUT /api/v1/account/cancel-on-disconnect`, body `{"enabled": <bool>}`) wrap
  the account COD endpoints added in Exchange API v0.7.1. Both are signed calls
  on the direct `/api/v1` surface and return a new `CancelOnDisconnectStatus`,
  which distinguishes the account's own opt-in (`enabled`) from whether COD will
  actually fire (`active` — opt-in *and* the exchange-side feature switch) and
  exposes the disconnect `grace_secs` window.
- **`TrailingLimit` order placement (ENG-6131).** `OrderRequest.trailing_limit(...)`
  models the request side of the `TrailingLimit` order type (a variant of
  `POST /api/v1/orders`). It requires both `trailing_offset_bps` (the trailing
  trigger) and `limit_offset_bps` (the fire-time limit offset) as positive
  integers (basis points; 1 bp = 0.01%) and carries no `price` — the limit
  price is computed server-side at fire time. The `Order` model now also echoes
  the nullable `limit_offset_bps` integer.
- **Release automation (ENG-6135).** A `release` workflow cuts a release from a
  `vX.Y.Z` tag push (or manual `workflow_dispatch` on an existing tag): it guards
  that the tag equals `pyproject.toml`'s version, runs the full check suite
  (lint/types/tests) so a red tree is never shipped, builds the sdist + wheel
  (`hatchling`), and attaches them to a GitHub release whose notes are the
  matching `CHANGELOG.md` section (extracted by `scripts/changelog_notes.py`).
  PyPI Trusted Publishing (OIDC) is wired but dormant — it activates only once a
  maintainer sets the `PYPI_ENABLED` repo variable and configures the `pypi`
  environment, so releases never fail on unconfigured PyPI. Distribution stays
  git-source until PyPI is live; the README install line is unchanged.

- **Spec-drift verification (ENG-7960).** `scripts/check_spec_drift.py`, wired as
  the `spec-drift` CI job on **every** PR — including the pin-bump PR, where the
  pin *is* the change and a trigger gated on spec-file diffs would verify nothing.
  It enforces both directions against the pinned spec: every `endpoints.txt` entry
  must exist in it (matched exactly, placeholder names included), and the
  operations the client code requests must *equal* that list. The code side is read
  with `ast` — every request goes through `Client._request`, and `direct=True`
  calls are resolved through client.py's own `API_V1_PREFIX`, so an operation is
  attributed to the path actually sent. Two named allowlists carry the by-design
  exceptions (`CODE_ONLY_OPS`, `NON_REST_TARGETS`), and both fail when stale, so an
  exemption can't outlive its reason. `scripts/test_check_spec_drift.py` (40 cases,
  hermetic, run in the same job) proves the checker goes red when defeated — a
  green run is only evidence if red is reachable.
- **`spec-autobump` workflow (ENG-7960).** Replaces `api-version-sync` with the
  design nexus-exchange-rs established (ENG-3563): dispatch from the api repo on
  release, a daily poll as the self-healing fallback, and manual dispatch. It
  classifies old-pin → new with a **pinned** oasdiff (`breaking --fail-on ERR`) and
  labels the PR `spec-autobump` or `breaking · needs-SDK-update`, requesting review
  on the latter. The PR still touches only the pin, the managed README line and the
  baked `DEFAULT_API_VERSION`; `spec-drift` on that PR is the merge signal. Because
  this repo has `allow_auto_merge` disabled, the workflow probes the setting and
  says plainly in the PR that a human must merge, rather than arming auto-merge
  into a silent no-op.

### Changed

- **Corrected `endpoints.txt`: five bridge operations were listed without their
  `/api/v1` prefix (ENG-7960).** `GET|POST /bridge/deposit-addresses`,
  `GET /bridge/assets`, `GET /bridge/deposits` and `GET /bridge/deposits/{id}` are
  requested with `direct=True`, so the client has always called them at
  `/api/v1/bridge/...` — the paths the spec defines. The manifest claimed the bare
  paths, which exist in no released spec. **The code was correct; the manifest was
  wrong**, so this is a bookkeeping correction with no behavior change, and the
  reported coverage rises from 43 to 48 of the 98 operations in `v0.7.2` — a
  correction, not new delivery. `GET /health` also left the manifest: the client
  probes it (`health_check`) but the spec dropped it in v0.7.1 in favour of
  `GET /status`, so it now sits in `CODE_ONLY_OPS` and no longer inflates coverage
  with an operation no released spec defines.
- **Decoding rejects values it previously coerced.** A non-finite money value
  (`"NaN"`, `"Infinity"`) now raises `DecodeError` instead of producing a
  `Decimal("NaN")` that silently poisons every comparison and sum it reaches; a
  required integer that is fractional or a `bool` raises instead of truncating to
  a fabricated figure; and a non-object element in a decoded list raises instead
  of an `AttributeError` from library internals. Applies to all models, not just
  the new ones. Optional and nullable fields still decode to `None` exactly as
  before.
- **Bounded `ruff` to one minor in the `dev` extra (ENG-7728).** `ruff>=0.6`
  became `ruff>=0.16,<0.17`. CI installs the formatter only through this extra,
  so an unbounded spec made a formatting change in any ruff release fail
  whichever unrelated PR happened to open first after it — as 0.16.0 did by
  formatting Python inside Markdown. Development-only: no runtime dependency and
  no API change. Dependabot now opens ruff bumps individually rather than inside
  the grouped minor PR, so the reformat one requires is the diff under review.
- **Pinned the Exchange API spec to `v0.7.2` (was `v0.7.1`) (ENG-6459).** The
  spec release that adds the portfolio-parity surface above. Bumps
  `.api-version`, the baked `DEFAULT_API_VERSION` constant sent as
  `X-Nexus-Api-Version`, and the bot-managed README line. v0.7.2 is purely
  additive — no existing method or model changed shape. The README SDK↔spec
  compatibility table records *shipped* SDK versions, so its row for this pin
  lands with the release that ships it.

## [0.3.0] - 2026-07-16

### Added

- **Request identity headers (ENG-5955).** Every REST request now sends
  `X-Nexus-Api-Version: <spec tag>` (defaulting to the pinned `.api-version`,
  overridable via `Client(api_version=…)`) and a normalized
  `User-Agent: nexus-exchange-py/<package version>`, so the edge can pin the
  request to a contract version and segment per-key usage metrics by client +
  version (ENG-5350 / ENG-4804). Both headers are also sent on a
  caller-supplied `http_client`. Adds `DEFAULT_API_VERSION` to the public API.

- **Tier-3 trading methods (ENG-5296).** Brings the Python surface to parity
  with the Rust SDK: `amend_order` (`PATCH /orders/{order_id}` on the `/api/v1`
  surface — `market_id` rides as a signed query param and an empty amend is
  rejected client-side), `adjust_margin` (`POST /account/margin`, add/remove
  isolated margin), and `set_leverage` (`POST /account/leverage`).
  `set_leverage` is a code-only op ahead of the pinned spec, so it is not
  listed in `endpoints.txt`.

### Changed

- **Pinned the Exchange API spec to `v0.7.1` (was `v0.6.2`) (ENG-6037).** Bumps
  `.api-version`, the bot-managed README line, and the baked `DEFAULT_API_VERSION`
  constant (the `X-Nexus-Api-Version` header value) in lockstep, clearing spec
  drift. `v0.7.1` adds surface — the `TrailingLimit` order type (ENG-6131),
  account cancel-on-disconnect (ENG-6132), and `/v1/bridge` Phase A (#32) —
  tracked as separate parity follow-ups (py drift treats uncovered routes as
  informational).

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

## [0.2.0] - 2026-07-07

### Changed

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

## [0.1.0] - 2026-06-24

### Changed

- Pinned the Exchange API spec to `v0.4.0` (was `v0.3.5`).

  **Breaking (upstream spec):** `v0.4.0` renames the market summary
  `mark_price` field to `last_trade_price`. The field key in market-summary
  payloads (e.g. anything read through `.raw["mark_price"]`) is now
  `"last_trade_price"`. Consumers reading the raw payload must move to
  `.raw["last_trade_price"]`, otherwise they will hit a `KeyError` at runtime.
  There is no compatibility shim for the old key.
