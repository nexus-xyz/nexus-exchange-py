# Contributing guide — nexus-exchange-py

The Python SDK for the Nexus Exchange API.

## Merging

- Don't merge a PR without an approving review — CI passing isn't a substitute.
- Don't merge a PR you didn't author without an approving review **and** the
  author's sign-off. Check the author first
  (`gh pr view <n> --json author,reviewDecision`).
- Re-approval isn't needed for follow-up commits to an already-approved PR.

## Pull requests

- One concern per PR; link its tracking issue (`ENG-XXXX`) in the title.
- Respond to review comments before merging.

## Checks (before pushing)

- `ruff check`, `ruff format --check`, `mypy src`, and `pytest` all pass — CI
  enforces these.

## API contract

- `.api-version` pins a released `nexus-exchange-api` tag; `endpoints.txt` lists
  the operations this SDK implements against it. Update it when you add a typed
  method — `scripts/check_spec_drift.py` (CI job `spec-drift`, every PR) enforces
  both directions: every line exists in the pinned spec, and the operations the
  client requests are exactly that list. An operation the pinned spec doesn't
  define belongs in that script's `CODE_ONLY_OPS`, not in `endpoints.txt`.
- Include the `/api/v1` prefix in an `endpoints.txt` line whenever the call passes
  `direct=True` — the prefix is part of the operation, and the spec lists the two
  surfaces separately.
- Pre-1.0: bump minor on breaking changes, patch on features and fixes.
