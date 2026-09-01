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
- The same script checks the credential each call sends (`signed=True` / `bearer=`
  / neither) against the `security` the pinned spec declares for that operation.
  Match the spec: sign what it declares `hmacAuth`, and send nothing on a public
  route — a signature it does not ask for makes the route unreachable to a keyless
  caller. Known disagreements awaiting a contract decision live in that script's
  `SECURITY_EXCEPTIONS`, pinned on both sides, and an entry fails once the
  disagreement it records is gone.
- Pre-1.0: bump minor on breaking changes, patch on features and fixes.

## Versioning

- **Never hand-edit `version` in `pyproject.toml`, and never push a version tag.**
  release-please computes the version from Conventional Commit subjects on `main`
  and opens a release PR; merging it is the release. See CONTRIBUTING.md
  "Releasing".
- The PR title is the squash commit subject, and therefore the bump. Any type
  with `!` → minor; `feat:`/`fix:`/`perf:`/`revert:` → patch; everything else,
  including an unrecognised or unparseable type, → no release. An unparseable
  subject contributes nothing and fails silently.
- Three files carry the version and are rewritten together: `pyproject.toml`,
  `.release-please-manifest.json`, and the marked `_resolve_version` fallback
  literal in `src/nexus_exchange/client.py`. Adding a fourth means adding it to
  `extra-files` in `release-please-config.json` with a `x-release-please-version`
  marker comment — and never writing that marker text in prose, since every line
  carrying it gets rewritten.
- `DEFAULT_API_VERSION` is the spec tag from `.api-version`, not a package
  version. It is not release-please's business.

## Networks

- `src/nexus_exchange/networks.py` is the **only** place hosts live. Add or
  re-decide a target there, never inline in a client or a script.
- **Never build a host by interpolating the network name.** Mainnet is
  `api.nexus.xyz`, not `api.mainnet.nexus.xyz`; a template resolves everywhere
  testable and breaks only on real funds.
- Branch on `network.real_funds` / `network.has_faucet`, never on the host string.
- Absence beats a guess: an unpublished target is `None` and the client refuses,
  rather than defaulting to something that would silently be another network.
- Credentials are per-network and never portable. One client, one network.
- Never default a signing `chain_id`. It is server-authoritative (`/metadata`);
  no value means refuse to sign.
