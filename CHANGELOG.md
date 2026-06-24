# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Pinned the Exchange API spec to `v0.4.0` (was `v0.3.5`).

  **Breaking (upstream spec):** `v0.4.0` renames the market summary
  `mark_price` field to `last_trade_price`. The field key in market-summary
  payloads (e.g. anything read through `.raw["mark_price"]`) is now
  `"last_trade_price"`. Consumers reading the raw payload must move to
  `.raw["last_trade_price"]`, otherwise they will hit a `KeyError` at runtime.
  There is no compatibility shim for the old key.
