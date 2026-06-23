# Contributing

## Compatibility & deprecations

This SDK follows [semver](https://semver.org/) (version in `pyproject.toml`).
It's **experimental** — expect churn before `1.0` — but we still minimize and
**batch** breaking changes so integrators aren't forced through one break at a
time. Pre-1.0 (`0.x`), a breaking change is a minor bump.

### Prefer designs that don't need a break

- **Model uncertainty as `None`/optional**, not a guessed concrete value you'll
  later have to retype.
- Extend via **new optional keyword args and new methods**, not by changing
  existing signatures or return types.
- Keep typed models lenient (full payload on `.raw`) so response-shape changes
  don't break callers.

### When a rename is needed: deprecate, don't remove

Keep the old name as a thin wrapper that warns, for at least one minor release:

```python
import warnings

def fetch_account(self) -> Account: ...

def get_account(self) -> Account:  # old name
    warnings.warn(
        "get_account is deprecated; use fetch_account",
        DeprecationWarning,
        stacklevel=2,
    )
    return self.fetch_account()
```

A change of return type or semantics is a genuine break — keeping the old method
would preserve the old behavior, so removal is correct there.

### When a break is unavoidable

Batch breaking changes into a single planned minor bump rather than one-per-PR,
and call it out in the PR.

### Toward 1.0

`0.x` is for iteration; we commit to a stable public surface at `1.0`, after
which breaking changes require a deprecation window and a major bump.
