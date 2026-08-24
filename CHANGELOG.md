# Changelog

All notable changes to this project are documented in this file.

This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html);
pre-1.0, a breaking change is a **minor** bump.

**This file is maintained by [release-please](https://github.com/googleapis/release-please)**
— it writes a new section from the Conventional Commit subjects merged since the
last release, into the open release PR. Don't hand-edit released sections or add
an `Unreleased` heading: release-please inserts each new section directly above
the topmost version heading, so an extra heading at the top would push every
future release below it. To improve the wording of a release, edit
`CHANGELOG.md` on the release PR's branch before merging it. Sections at and
below `0.4.0` predate this and follow
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.0] - 2026-08-20

### Changed

- **`NetworkConfig.real_funds` (bool) is now `funds` (tri-state) (#60).**
  **Breaking.** `Funds.REAL` / `Funds.PLAY` / `Funds.UNKNOWN`, on both
  `NetworkConfig` and `Network`. A boolean cannot express a target whose funds
  were never declared, and that state has to exist — a caller-supplied URL says
  nothing about what is behind it. It is deliberately **not** kept as a derived
  boolean: `real_funds is False` would read as "play money" for an undeclared
  target, which is the one wrong answer that costs money. Guard on `PLAY`
  positively (`funds is not Funds.PLAY`), never by negating `REAL`.
- **`Client.network` returns the `NetworkConfig`, not the `Network` member.**
  **Breaking.** A named network and a custom target now expose the same fields,
  so every guard reads one shape. Compare against `Network.TESTNET.config`.
- **A bare `base_url` with no network named now reports undeclared funds.**
  **Breaking (behavioural).** It builds a custom config with `Funds.UNKNOWN` and
  no faucet, so `claim_credit` refuses where it previously inherited testnet's
  faucet. Previously the override changed transport only and left whichever
  network was default supplying the funds semantics — which is exactly how a
  client pointed at a real-funds deployment kept reporting play-funds
  guardrails. Naming the network alongside the URL keeps its semantics
  (`Client(Network.LOCAL, base_url=…)`), and mainnet's required override is
  unaffected.
- **A raw `base_url` / `direct_base_url` override is now validated (#60).**
  **Breaking (behavioural).** Naming a network keeps its config, so an override
  passed alongside one never reached `NetworkConfig.custom`'s checks: it was
  stripped of trailing slashes and then used to build *and sign* requests. Both
  doors now share one validator, so an override gets the scheme, host, userinfo
  and query/fragment checks too. This was the only path to mainnet, which makes
  it the one place a malformed or hostile base could not be caught. A blank
  override still means "unset" and defers to the network's default — validation
  runs on what survives that fallback, not on the raw argument.

### Added

- **`NetworkConfig.custom(...)` — a caller-supplied network target (#60).**
  Reaches a deployment this SDK ships no hostname for, and carries the whole
  bundle rather than a bare URL: both bases, funds semantics, faucet, signing
  domain and a label. Accepted anywhere a `Network` is — `Client(config)` —
  while the shipped network map stays immutable and a custom target stays
  un-addressable by name. `label` and `funds` are required and have no defaults.
  `label` is charset-validated (`[A-Za-z0-9._-]`, max 64, no `.` or `..`)
  because it is the key credentials are namespaced under across these SDKs; an
  unvalidated one could let one target address another's. `has_faucet` defaults
  to absent, and a real-funds target with a faucet is refused outright as
  incoherent. `chain_id` defaults to unknown, so signing refuses rather than
  guessing — the same rule the named networks follow. Both base URLs are
  validated for scheme and host, and rejected if they carry userinfo or a query
  or fragment: the request path is appended to the base, so `https://host?a=1`
  would be signed as `https://host?a=1/api/v1/orders`, and
  `https://exchange.nexus.xyz@evil.com` reads as the published host while
  sending API keys somewhere else. A path is deliberately still accepted — that
  is the gateway topology this variant exists to reach (#60).

- **Cursor pagination on the five paginated list endpoints (#46).** Spec
  v0.7.2 added a `cursor` query parameter and an `X-Next-Cursor` response header
  to trades, fills, order history, closed positions and equity history. The SDK
  now threads them: `iter_trades` / `iter_my_trades` and the other `iter_*`
  helpers walk every page rather than returning the first one and reporting
  completion. New public surface: `Page`, `iter_pages`, `iter_items` and
  `NEXT_CURSOR_HEADER` from `nexus_exchange.pagination`, plus `*_page` methods
  that return one page and its next cursor.
- **`PaginationError`.** Raised when an endpoint hands back the same
  `X-Next-Cursor` it was given, so the walk cannot advance. The SDK stops and
  says so rather than hanging, and rather than stopping quietly — a silent stop
  is indistinguishable from "that was the last page", which would hand the
  caller a truncated history it believes is complete.
- **`/orders/history`, `/positions/closed` and `/account/equity-history`
  (#47).** Three endpoints the SDK did not reach. Each has a
  `fetch_*` (one page), a `*_page` (page plus next cursor) and an `iter_*`
  (walks every page), with `OrderHistoryEntry`, `ClosedPosition` and
  `EquityPoint` models and the per-endpoint page-size maxima
  `ORDER_HISTORY_LIMIT_MAX`, `CLOSED_POSITIONS_LIMIT_MAX` and
  `EQUITY_HISTORY_LIMIT_MAX` taken from the spec rather than copied.
- **Absent money and timestamps decode to `None`, not `0`.** `ClosedPosition`
  and `EquityPoint` declare no `required` fields in spec v0.7.2, so their
  money and timestamp fields are `Decimal | None` / `int | None`. A defaulted
  zero was a real, wrong number: an absent `realized_pnl` read as "closed
  flat", and an absent `closed_at_ms` plotted at 1970. An explicitly `null`
  field and an omitted one now agree, where previously one raised and the
  other returned zero.
- **`RestrictedJurisdictionError` for the jurisdiction `403` (#54).** Spec
  v0.7.3 declares this refusal on every state-changing operation — placing,
  amending and batching orders, deposits, margin adjustments, credits, the
  faucet — and, for the sanctions code, on reads as well. It is **permanent for
  the caller's origin**, so it is not a failure to surface and retry later, and
  that is what earns it a type instead of an `ApiError` whose `code` has to be
  string-matched. Branch on `err.block_reason`: `US_RESTRICTED` (US write
  restriction), `GEO_UNRESOLVED` (origin unresolved, the write failed closed) or
  `RESTRICTED_JURISDICTION` (sanctions list). The value comes from the
  `x-nexus-block-reason` header, falling back to the body `code` — the header is
  preferred because it still classifies when the body is absent, truncated or
  not JSON. The spec keeps that list **open**, so an unrecognized reason raises
  the same class rather than degrading to a bare `ApiError`. Additive: it
  subclasses `ApiError`, so existing `except ApiError` handlers are unaffected,
  and the other `403`s in the contract (`credits_frozen`, the admin-secret
  refusal) deliberately stay plain `ApiError`s — those can lift, this one does
  not.
- **The network axis `{Mainnet, Testnet, Local}` (#51).** `Network` now
  names a *network* — which chain, and whose money — instead of a release
  channel, and each member bundles its whole config in one frozen
  `NetworkConfig`: both REST bases, the market-data and authenticated
  **WebSocket bases** (previously unavailable for hosted environments at all),
  whether the network has a faucet, whether it moves **real funds**, and its
  EIP-712 `SigningDomain`. The host map is spelled out with mainnet as a named
  case — `api.nexus.xyz`, never `api.mainnet.nexus.xyz` — because interpolating
  the network name resolves for every environment that can be tested and breaks
  only on real funds. Mirrors the spec's `x-nexus-networks` (#51).
- **`direct_base_url` on `Client`.** Targets a deploy that keeps the
  gateway/direct split, which a single `base_url` collapses. This is what the
  retired `beta` channel becomes.
- **A `/api/exchange` base reaching the direct surface is now rejected at
  construction.** The direct `/api/v1` service is served at the host root, so a
  gateway base would send *and HMAC-sign* `/api/exchange/api/v1/orders` — a 404
  that reads as an auth failure. Since `base_url` alone covers both surfaces,
  the likeliest way in was copying the first line of the `beta` migration, or a
  `baseUrl` from the TypeScript SDK, where that name means the *direct* base.
  Pass `direct_base_url` alongside it; `base_url` itself may still be a gateway
  URL, as the network defaults are.

- **Portfolio-parity endpoints and fields (#43).** Surfaces the
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
- **Account cancel-on-disconnect methods (#38).** `fetch_cancel_on_disconnect`
  (`GET /api/v1/account/cancel-on-disconnect`) and `set_cancel_on_disconnect`
  (`PUT /api/v1/account/cancel-on-disconnect`, body `{"enabled": <bool>}`) wrap
  the account COD endpoints added in Exchange API v0.7.1. Both are signed calls
  on the direct `/api/v1` surface and return a new `CancelOnDisconnectStatus`,
  which distinguishes the account's own opt-in (`enabled`) from whether COD will
  actually fire (`active` — opt-in *and* the exchange-side feature switch) and
  exposes the disconnect `grace_secs` window.
- **`TrailingLimit` order placement (#39).** `OrderRequest.trailing_limit(...)`
  models the request side of the `TrailingLimit` order type (a variant of
  `POST /api/v1/orders`). It requires both `trailing_offset_bps` (the trailing
  trigger) and `limit_offset_bps` (the fire-time limit offset) as positive
  integers (basis points; 1 bp = 0.01%) and carries no `price` — the limit
  price is computed server-side at fire time. The `Order` model now also echoes
  the nullable `limit_offset_bps` integer.
- **Release automation (#40).** A `release` workflow cuts a release from a
  `vX.Y.Z` tag push (or manual `workflow_dispatch` on an existing tag): it guards
  that the tag equals `pyproject.toml`'s version, runs the full check suite
  (lint/types/tests) so a red tree is never shipped, builds the sdist + wheel
  (`hatchling`), and attaches them to a GitHub release whose notes are the
  matching `CHANGELOG.md` section (extracted by `scripts/changelog_notes.py`).
  PyPI Trusted Publishing (OIDC) is wired but dormant — it activates only once a
  maintainer sets the `PYPI_ENABLED` repo variable and configures the `pypi`
  environment, so releases never fail on unconfigured PyPI. Distribution stays
  git-source until PyPI is live; the README install line is unchanged.

- **Spec-drift verification (#50).** `scripts/check_spec_drift.py`, wired as
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
- **`spec-autobump` workflow (#50).** Replaces `api-version-sync` with the
  design nexus-exchange-rs established: dispatch from the api repo on
  release, a daily poll as the self-healing fallback, and manual dispatch. It
  classifies old-pin → new with a **pinned** oasdiff (`breaking --fail-on ERR`) and
  labels the PR `spec-autobump` or `breaking · needs-SDK-update`, requesting review
  on the latter. The PR still touches only the pin, the managed README line and the
  baked `DEFAULT_API_VERSION`; `spec-drift` on that PR is the merge signal. Because
  this repo has `allow_auto_merge` disabled, the workflow probes the setting and
  says plainly in the PR that a human must merge, rather than arming auto-merge
  into a silent no-op.

### Deprecated

- **A bare `base_url` with no network named — use `NetworkConfig.custom(...)`
  (#61).** Now that every client has landed a custom target,
  the raw override is sugar over it, and the sugar cannot say what it is
  pointed at. Both forms reach the same host; only the config declares the
  funds, the faucet and the signing domain, which is why the bare form has to
  fall back to `Funds.UNKNOWN` and no faucet. Nothing changes at runtime: the
  bare form still works, still builds the same undeclared-funds config, and the
  guarded paths still refuse.
  - **No `DeprecationWarning` is raised** — this is a documentation-only
    deprecation, decided across the five SDKs rather than per-repo.
    The mechanisms deliberately differ by ecosystem idiom, and so does how loud
    they are: Rust carries `#[deprecated]` and warns on every build, the MCP
    server prints a notice on stderr every run, while Python and TypeScript
    mark this in prose only. Python and TypeScript callers therefore get **no
    runtime signal at all** from this release, which is the tradeoff being
    made, not an oversight.
  - **Removal requires a release that warns first.** Because this one is
    silent, the bare selector cannot go straight from here to removed: a real
    `DeprecationWarning` — which Python shows by default when the caller is
    `__main__`, i.e. in exactly the local scripts and notebooks this form
    exists for — has to ship, and ship in a release, before removal. Removal is
    itself a breaking change and needs its own release after that. Recorded
    here rather than only in the PR that decided it.
  - The **selector** is what is deprecated — a URL that picks the target on its
    own. `direct_base_url` is a modifier and is untouched, and so is a URL
    passed *alongside* a named network (`Client(Network.LOCAL, base_url=…)`,
    and mainnet's required override): those refine a target whose funds the
    caller has already declared.
  - The retired-`beta` migration message and the README now suggest
    `NetworkConfig.custom(...)` rather than the bare override. The suggestion
    declares `Funds.UNKNOWN`, not the play funds that deploy once had: this SDK
    cannot check what that host serves today, and guessing low is the direction
    that costs money.

### Fixed

- **`Network.TESTNET.direct_base_url` changed from
  `https://exchange.nexus.xyz` to `https://exchange.nexus.xyz/api/exchange`,
  which fixes every `direct=True` route on a default testnet client**
  (#62, [rs#131](https://github.com/nexus-xyz/nexus-exchange-rs/pull/131)). The
  client composes `direct_base_url + "/api/v1" + path`, so the old value built
  `https://exchange.nexus.xyz/api/v1/...`, which answers `404 text/html` — the
  frontend, not the API. The working URL is
  `https://exchange.nexus.xyz/api/exchange/api/v1/...`: on this deploy the
  `/api/v1` service is mounted *under* the `/api/exchange` gateway prefix rather
  than at the host root, so the direct base has to carry that prefix too. All
  ~36 direct routes (market data, account, trading) were unreachable; only the
  legacy gateway routes worked. `_resolve_base` had already stopped rejecting a
  gateway base for this field on the strength of that measurement, but the
  default was never moved to match. No caller change is required — code that was
  failing starts working — though anyone asserting the old literal will see it
  change. The two fields stay separate for a deploy that does serve the surfaces
  apart; on this one they are equal. The README's SDK-comparison table
  documented the old, broken URL as the expected result and is corrected here.

- **A gateway-prefixed `direct_base_url` is no longer rejected (#60).**
  The validation asserted that the direct `/api/v1` surface is served only at
  the host root; production measurement found the opposite
  ([rs#131](https://github.com/nexus-xyz/nexus-exchange-rs/pull/131)) — the
  gateway mounts `/api/v1` under its own prefix and answers `200`, while the
  host root answers `404 text/html`. Both topologies are real, so the
  unconditional rejection made the working configuration unreachable on
  the deploy this SDK targets by default. Which one applies is a property of the
  URL, and is now left to the URL.

### Changed

- **BREAKING: `fetch_trades` and `fetch_my_trades` now reject an out-of-range
  `limit` locally instead of forwarding it (#46).** The paginated endpoints
  declare a `maximum` (1000 for trades and fills), and the SDK now validates
  against it before the request — and, on a signed route, before signing —
  raising `ValueError`. Previously the value went to the server, which clamped
  it: `fetch_trades(market_id, limit=5000)` used to succeed and return 1000 rows,
  and now raises.

  This can break a working caller, which is why it is here rather than only in
  `Added`. A caller passing a limit above the maximum was already not getting
  what they asked for; the change makes that visible at the call site instead of
  silently downgrading it. Pass `limit=1000` (or omit it) for the previous
  behaviour, or use `iter_trades` / `iter_my_trades` to walk past one page's
  worth — which is what a caller reaching for `limit=5000` usually wanted.

  The `iter_*` forms validate the same bound **at call time** rather than at the
  first `next()`, so both forms of the same call now report a caller's own
  mistake at the same moment.

- **BREAKING: `Network.STABLE` and `Network.BETA` are removed (#51).** They
  were release channels, not networks, and the old enum had no way to name
  mainnet at all.
  - `Network.STABLE` → **`Network.TESTNET`**, which is also the new default for
    `Client` and `NexusExchange`. The targets are byte-identical — the legacy
    `https://exchange.nexus.xyz/api/exchange` gateway serves testnet — so code
    that relied on the default keeps hitting exactly the same URLs. Defaulting
    to play funds is deliberate: real funds should never be one keystroke away.
  - `Network.BETA` → an explicit override,
    `Client(base_url="https://beta.exchange.nexus.xyz/api/exchange",
    direct_base_url="https://beta.exchange.nexus.xyz")`.
  - Both retired names raise a `ValueError` carrying their migration, rather
    than resolving to a network that merely looks right. Any other unrecognized
    identifier is refused too: an unknown network is treated as real funds.
- **`Client(Network.MAINNET)` raises without an explicit `base_url`.** The
  mainnet host is published but not yet resolvable (DNS is separate infra), and
  the SDK will neither invent a real-funds target nor fall back to another
  network's. Failing at construction beats failing mid-order. Filling the
  default in once DNS lands is additive.
- **`register_agent` refuses to sign without a chain id.** The EIP-712 domain is
  per-network and server-authoritative; `None`, `0` and `True` (an `int`
  subclass that would otherwise sign under Ethereum Mainnet's chain id 1) now
  raise `AuthError`. The static map publishes `chain_id=None`, meaning *not
  published* — read the live value from the edge's `/metadata`. Signing under a
  guessed domain either fails verification or, worse, yields a signature valid
  on a different network.
- **`claim_credit` refuses on a faucet-less network.** The spec marks
  `POST /account/credit` testnet/local-only, so a mainnet call now fails locally
  instead of spending a signed request against a real-funds host.
- **Corrected `endpoints.txt`: five bridge operations were listed without their
  `/api/v1` prefix (#50).** `GET|POST /bridge/deposit-addresses`,
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
- **Bounded `ruff` to one minor in the `dev` extra (#49).** `ruff>=0.6`
  became `ruff>=0.16,<0.17`. CI installs the formatter only through this extra,
  so an unbounded spec made a formatting change in any ruff release fail
  whichever unrelated PR happened to open first after it — as 0.16.0 did by
  formatting Python inside Markdown. Development-only: no runtime dependency and
  no API change. Dependabot now opens ruff bumps individually rather than inside
  the grouped minor PR, so the reformat one requires is the diff under review.
- **Pinned the Exchange API spec to `v0.7.2` (was `v0.7.1`) (#43).** The
  spec release that adds the portfolio-parity surface above. Bumps
  `.api-version`, the baked `DEFAULT_API_VERSION` constant sent as
  `X-Nexus-Api-Version`, and the bot-managed README line. v0.7.2 is purely
  additive — no existing method or model changed shape. The README SDK↔spec
  compatibility table records *shipped* SDK versions, so its row for this pin
  lands with the release that ships it.

## [0.3.0] - 2026-07-16

### Added

- **Request identity headers (#34).** Every REST request now sends
  `X-Nexus-Api-Version: <spec tag>` (defaulting to the pinned `.api-version`,
  overridable via `Client(api_version=…)`) and a normalized
  `User-Agent: nexus-exchange-py/<package version>`, so the edge can pin the
  request to a contract version and segment per-key usage metrics by client +
  version. Both headers are also sent on a
  caller-supplied `http_client`. Adds `DEFAULT_API_VERSION` to the public API.

- **Tier-3 trading methods (#28).** Brings the Python surface to parity
  with the Rust SDK: `amend_order` (`PATCH /orders/{order_id}` on the `/api/v1`
  surface — `market_id` rides as a signed query param and an empty amend is
  rejected client-side), `adjust_margin` (`POST /account/margin`, add/remove
  isolated margin), and `set_leverage` (`POST /account/leverage`).
  `set_leverage` is a code-only op ahead of the pinned spec, so it is not
  listed in `endpoints.txt`.

### Changed

- **Pinned the Exchange API spec to `v0.7.1` (was `v0.6.2`) (#35).** Bumps
  `.api-version`, the bot-managed README line, and the baked `DEFAULT_API_VERSION`
  constant (the `X-Nexus-Api-Version` header value) in lockstep, clearing spec
  drift. `v0.7.1` adds surface — the `TrailingLimit` order type (#39),
  account cancel-on-disconnect (#38), and `/v1/bridge` Phase A (#32) —
  tracked as separate parity follow-ups (py drift treats uncovered routes as
  informational).

- **Typed `create_orders` return value (#25).** `Client.create_orders`
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

- **`/api/v1` direct-service routing (#26).** As the REST gateway is
  retired, the migrated market-data and account/trading routes now
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
