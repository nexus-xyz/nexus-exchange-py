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

Run these from the virtualenv you installed the `dev` extra into. CI installs
`ruff` only from that extra, where it's bounded to one minor (`>=0.16,<0.17`)
because a ruff minor can change formatting — a globally installed ruff of a
different minor can leave the tree formatted in a way CI rejects. Widening the
bound is its own PR, since the reformat it demands is the point of that diff.

## How a PR lands

Squash-and-merge is the only method enabled, and the source branch is deleted on
merge, so one PR is always exactly one commit on `main`. **That commit's subject
is your PR title**, so write it as a
[conventional commit](https://www.conventionalcommits.org/) — `feat:`, `fix:`,
`docs:`, `chore:`, `ci:` — and declare a breaking change with `!` before the
colon (`feat!:`, `feat(client)!: …`).

**That subject is load-bearing.** release-please reads the merged commit history
and computes the next version from it (see [Releasing](#releasing)), so the type
you write is the bump you get:

| Subject | Bump from `0.4.0` |
| -- | -- |
| any type with `!` (or a `BREAKING CHANGE:` commit footer) | `0.5.0` (minor) |
| `feat:` | `0.4.1` (patch) |
| `fix:`, `perf:`, `revert:` | `0.4.1` (patch) |
| `docs:`, `style:`, `chore:`, `refactor:`, `test:`, `build:`, `ci:` | none |

That last row is the whole list of non-releasing types, and anything outside both
lists — an unrecognised type, or a subject that doesn't parse at all — is
discarded the same way. So a subject that doesn't parse doesn't count: it lands
silently and contributes nothing to the version or the changelog, which is the
failure mode to watch for. (Dependabot's `deps:` prefix is in that discarded
group, so dependency bumps don't cut releases on their own.)

A period containing only non-releasing commits produces no release PR at all —
the notes would be empty, and release-please skips rather than cutting an empty
release.

Two asymmetries are worth knowing:

- A `BREAKING CHANGE:` footer counts only in a **commit** body. The squash
  commit's body is assembled from the commit messages on the branch and never
  from the PR description, so a footer typed into the PR description is lost.
  The `!` in the title is the reliable declaration.
- `!` is a **minor** bump here, not a major one — see
  [Compatibility & deprecations](#compatibility--deprecations).

## Compatibility & deprecations

This SDK follows [semver](https://semver.org/) (version in `pyproject.toml`).
It's **experimental** — expect churn before `1.0` — but we still work to
minimize and **batch** breaking changes so integrators aren't forced through
one break at a time. Pre-1.0 (`0.x`), a breaking change is a **minor** bump.

That rule is mechanical, not remembered (ENG-7536): `bump-minor-pre-major` and
`bump-patch-for-minor-pre-major` in `release-please-config.json` are what make
`feat!` a minor rather than a `1.0.0`, and `tests/test_release_config.py` fails if
either flag goes missing. See [Toward 1.0](#toward-10) for what leaving `0.x`
takes.

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

### Deprecating a form, not a name

A form with a *better* replacement rather than a new spelling — the bare
`base_url` selector, deprecated in favour of `NetworkConfig.custom(...)`
(ENG-10955) — is not a rename, so the runtime nudge above is a judgement call
rather than the rule. That one is deliberately **documentation-only**: the
mechanism was picked per ecosystem across the SDKs (ENG-10950) and Python's is
prose, so it does not `warnings.warn`, and `tests/test_custom_network.py` pins
that silence so the decision is asserted rather than inferred.

The cost is that a caller who never reads the docs gets no signal, which sets
the condition for undoing it: **a release that warns has to ship before the one
that removes the form.** Adding that warning means updating the test that
currently pins silence — deliberately, in the PR that adds it.

### When a break is unavoidable

- **Batch** breaking changes into a single planned minor bump rather than
  shipping them one-per-PR.
- Put a `!` in the PR title (`feat!:`). This is the declaration that computes
  the bump — see [How a PR lands](#how-a-pr-lands). Nothing else does: not the
  PR description, not a label, not the changelog.
- Also call the break out in the PR description. That's for the reviewer, and
  for the prose you'll want when enriching the generated release notes on the
  release PR (see [Releasing](#releasing)).

### API spec version

The SDK tracks a pinned Exchange API spec version in `.api-version`. The `drift`
CI job fails if that pin isn't the latest release of the spec repo, so bump
`.api-version` (and the README spec table) together when you target a new spec
release. In practice `spec-autobump` opens that PR for you when the spec repo
publishes a release.

`endpoints.txt` lists the operations this SDK implements, and the `spec-drift` CI
job enforces it in both directions on **every** PR: each line must exist in the
pinned spec, and the operations the client code requests must be exactly that list.
So adding a typed method means adding its line — CI fails otherwise, and equally
fails on a line no method requests. To run it locally:

```bash
curl -fsSL "https://raw.githubusercontent.com/nexus-xyz/nexus-exchange-api/$(cat .api-version)/openapi.json" \
  -o openapi.pinned.json
python3 scripts/check_spec_drift.py openapi.pinned.json
python3 scripts/test_check_spec_drift.py   # the checker's own tests
```

Two escape hatches exist for operations that legitimately can't be listed, both
named and documented in `scripts/check_spec_drift.py` — `CODE_ONLY_OPS` (the
client requests it but the pinned spec doesn't define it) and `NON_REST_TARGETS`
(listed, but reached without a `_request` call). Both are checked for staleness, so
an entry can't outlive its reason.

### Toward 1.0

`0.x` is for iteration. We'll commit to a stable public surface at `1.0`; after
that, breaking changes require a deprecation window and a major bump.

Nothing gets there by accident. Three locks have to be opened in one deliberate
diff, and each one fails loudly on its own:

1. Drop `bump-minor-pre-major` and `bump-patch-for-minor-pre-major` from
   `release-please-config.json` — until then the computed version literally
   cannot reach `1.0.0`.
2. Update `tests/test_release_config.py`, which asserts both flags and that the
   version is still `0.x`.
3. Set the repo variable `ALLOW_MAJOR_RELEASE=true`; `release.yml` refuses to
   release a `>= 1.0.0` version without it, which also covers a hand-pushed tag.

## Releasing

The version is **computed, not hand-set** (ENG-7536). Two workflows, two stages:

1. `release-please.yml` watches `main` and keeps a standing **release PR** open,
   accumulating the Conventional Commit history into a version bump
   (`pyproject.toml`, `.release-please-manifest.json`, and the
   `_resolve_version` fallback literal in `src/nexus_exchange/client.py`) plus a
   new `CHANGELOG.md` section. Nothing ships while it sits open. It needs a
   review and green checks like any other PR — with one wrinkle: this repo
   requires approval before a first-time contributor's workflows run, and
   `github-actions[bot]` counts as one on every release PR, so its CI run parks
   in *action_required* and the PR reports no checks at all until that run is
   approved. `release-please.yml` approves it itself (ENG-13320); if it warns
   that it could not — the built-in token may not be allowed to — click
   **Approve and run workflows** on the PR, or approve the run from
   **Actions**. That is the only manual step, and the checks go green on their
   own afterwards.
2. **Merging that PR is the release.** release-please tags the merge commit and
   cuts a *draft* GitHub release, then dispatches `release.yml`, which guards
   the tag against `pyproject.toml`, guards the version against the pre-1.0
   policy, runs the full check suite, builds the sdist + wheel, attaches them,
   sets the release notes to the `CHANGELOG.md` section for that version, and
   only then undrafts the release — so a published release with no artifacts
   never exists.

So: don't edit `version` in `pyproject.toml`, and don't push tags by hand.

**Enriching the notes.** release-please generates one bullet per commit, which is
thinner than the prose this changelog has carried. To do better, edit
`CHANGELOG.md` on the release PR's branch before merging it — that's the one
place hand-written notes belong now. Keep the `## [X.Y.Z](…)` heading intact: the
release notes are extracted from it by `scripts/changelog_notes.py`, and an
empty or missing section fails the release.

If the handoff in step 2 fails (draft release and tag exist, no artifacts),
re-run it from **Actions → Release → Run workflow** with that tag. Both stages
are idempotent. A hand-pushed `vX.Y.Z` tag also still triggers `release.yml`
directly, so releases remain possible if release-please is ever removed.

PyPI publishing is wired (Trusted Publishing / OIDC) but off by default. To turn
it on: register the project and a trusted publisher on pypi.org, create a `pypi`
GitHub environment, set the repo variable `PYPI_ENABLED=true`, and then flip the
README install line to `pip install nexus-exchange`.
