# Contributing

Thanks for contributing to `nexus-exchange`! This SDK is a thin, typed
wrapper over the Nexus Exchange API. Keep PRs focused — open separate PRs
for unrelated changes.

## Development setup

Use a virtual environment and install the package in editable mode with the
`dev` extras:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Before opening a PR, run the same checks CI runs:

```bash
ruff format --check .   # formatting (CI: format)
ruff check .            # lint (CI: lint)
mypy src                # type check (CI: types)
pytest -q               # tests (CI: test)
```

`ruff format .` (without `--check`) rewrites files in place, and
`ruff check . --fix` applies safe autofixes. The test job runs across Python
3.10–3.13; the SDK targets `requires-python >= 3.10`.

## Compatibility & deprecations

This SDK follows [semver](https://semver.org/) (version in `pyproject.toml`).
It's **experimental** — expect churn before `1.0` — but we still work to
minimize and **batch** breaking changes so integrators aren't forced through
one break at a time. Pre-1.0 (`0.x`), a breaking change is a **minor** bump.

### Prefer designs that don't need a break

- **Model uncertainty as `Optional`/absence, not a guessed concrete value.**
  If an endpoint, URL, or field might not exist or isn't confirmed, return
  `None` / `Optional[...]` (or don't expose it) rather than shipping a
  placeholder you'll later have to retype. A change of return type can't be
  softened with deprecation (see below), so get this right up front.
- **Keep dataclass / model fields additive.** Adding a new optional field to
  a response model is non-breaking; renaming or removing one is not. The typed
  models keep the full payload on `.raw`, so prefer reading new data off `.raw`
  until a field is stable enough to promote to a typed attribute.
- **Prefer keyword-only, optional parameters for new arguments** (`*,
  foo: X | None = None`) so adding them doesn't break existing call sites.

### When a rename is needed: deprecate, don't remove

Add the new name and keep the old one as a delegating alias for at least one
minor release before removing it. Emit a `DeprecationWarning` from the old
path so integrators get a runtime nudge:

```python
import warnings


def fetch_ticker(self, market_id: str) -> Ticker:
    """Latest ticker for one market."""
    ...


def ticker(self, market_id: str) -> Ticker:
    """Deprecated alias for :meth:`fetch_ticker`."""
    warnings.warn(
        "`ticker` is deprecated; use `fetch_ticker` instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return self.fetch_ticker(market_id)
```

This only works for a **pure rename** (same signature and semantics). A change
of return type or behavior is a genuine break — keeping the old method would
preserve the old (often wrong) behavior, so removal is correct there.

### When a break is unavoidable

- **Batch** breaking changes into a single planned minor bump rather than
  shipping them one-per-PR.
- Call the break out explicitly in the PR description so it can be summarized
  in the release notes.

### API spec version

The SDK tracks a pinned Exchange API spec version in `.api-version`. The
`drift` CI job fails if that pin isn't the latest release of the spec repo, so
bump `.api-version` (and the README spec table) together when you target a new
spec release.

### Toward 1.0

`0.x` is for iteration. We'll commit to a stable public surface at `1.0`; after
that, breaking changes require a deprecation window and a major bump.
