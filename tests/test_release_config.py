"""Tests for the release-please version-bump configuration (ENG-7536).

The version is no longer hand-edited: release-please computes it from commit
history and rewrites `pyproject.toml`, the `_resolve_version` fallback literal in
`src/nexus_exchange/client.py`, and `.release-please-manifest.json` together.
That arrangement has two failure modes that are silent until release time, so
they are asserted here instead:

* **The pre-1.0 policy stops being enforced.** `bump-minor-pre-major` and
  `bump-patch-for-minor-pre-major` are the only reason a breaking change bumps
  the minor instead of cutting `1.0.0`. Delete either and the next `feat!` reads
  as a stable-API promise this SDK has not made. Going 1.0 deliberately means
  changing these tests and the `ALLOW_MAJOR_RELEASE` guard in `release.yml` — the
  point is that it can't happen by omission.
* **The three version copies drift.** release-please reads the manifest as the
  current version, while `release.yml` guards the tag against `pyproject.toml`.
  If those disagree, every release fails on that guard with nothing explaining
  why.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "release-please-config.json"
MANIFEST_PATH = REPO_ROOT / ".release-please-manifest.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
CLIENT_PATH = REPO_ROOT / "src" / "nexus_exchange" / "client.py"

SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


@pytest.fixture(scope="module")
def package(config: dict) -> dict:
    """The single root package's settings."""
    packages = config["packages"]
    assert list(packages) == ["."], "this is a single-package repo; expected only a '.' entry"
    return packages["."]


def pyproject_version() -> str:
    """`project.version` from pyproject.toml, without tomllib (3.11+ only)."""
    matches = re.findall(r"(?m)^version = \"([^\"]+)\"$", PYPROJECT_PATH.read_text())
    assert len(matches) == 1, f"expected exactly one top-level version line, found {matches}"
    return matches[0]


def client_fallback_version() -> str:
    """The `x-release-please-version`-annotated literal in client.py."""
    annotated = [
        line for line in CLIENT_PATH.read_text().splitlines() if "x-release-please-version" in line
    ]
    # Exactly one, or release-please would rewrite a line nobody is tracking.
    assert len(annotated) == 1, f"expected exactly one annotated line, found {annotated}"
    found = re.search(r"\"([0-9]+\.[0-9]+\.[0-9]+)\"", annotated[0])
    assert found, f"no version literal on the annotated line: {annotated[0]!r}"
    return found.group(1)


def test_pre_major_bump_flags_are_set(config: dict) -> None:
    # Both must be literally true: release-please treats a missing key as false,
    # which is what would let a breaking change cut 1.0.0.
    assert config.get("bump-minor-pre-major") is True
    assert config.get("bump-patch-for-minor-pre-major") is True


def test_version_is_still_pre_1_0() -> None:
    # Deliberately brittle. Committing to a stable surface means editing this
    # test, dropping the two flags above, and setting ALLOW_MAJOR_RELEASE=true —
    # one decision, in one reviewed diff.
    major = pyproject_version().split(".")[0]
    assert major == "0", "leaving 0.x is a deliberate decision; see CONTRIBUTING.md 'Toward 1.0'"


def test_manifest_matches_pyproject() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    assert list(manifest) == ["."]
    assert SEMVER_RE.match(manifest["."]), f"manifest version is not X.Y.Z: {manifest['.']!r}"
    assert manifest["."] == pyproject_version()


def test_client_fallback_matches_pyproject() -> None:
    assert client_fallback_version() == pyproject_version()


def test_client_is_listed_as_an_extra_file(package: dict) -> None:
    # The annotation in client.py only does something if the file is listed here.
    paths = {
        entry["path"] if isinstance(entry, dict) else entry
        for entry in package.get("extra-files", [])
    }
    assert "src/nexus_exchange/client.py" in paths


def test_tags_are_bare_v_prefixed_semver(config: dict, package: dict) -> None:
    # release.yml only accepts `v[0-9]+.[0-9]+.[0-9]+`, and the python strategy
    # tags `v<version>`; a component in the tag would produce something that
    # workflow's trigger and its `--verify-tag` step would never match.
    assert package["release-type"] == "python"
    assert package.get("include-component-in-tag") is False
    assert "tag-separator" not in config and "tag-separator" not in package


def test_the_release_pr_ci_run_is_approved_not_dispatched() -> None:
    # `main` requires CI as status checks, and the release PR's own
    # `pull_request` run is the only thing that can satisfy them — a run
    # dispatched at the release branch is not associated with the pull request,
    # so its check runs land on the head commit and count for nothing. That was
    # the original workaround and it never worked: measured on #75, nine green
    # check runs on the head commit, `statusCheckRollup` empty, PR BLOCKED
    # (ENG-13320). Re-adding the dispatch would restore a duplicate CI run that
    # looks like it fixes the problem and does not.
    rp = (REPO_ROOT / ".github" / "workflows" / "release-please.yml").read_text()
    assert "gh workflow run ci.yml" not in rp, (
        "dispatching ci.yml does not satisfy the release PR's required checks"
    )
    assert "/actions/runs/${run_id}/approve" in rp, (
        "release-please.yml must approve the release PR's gated CI run"
    )


def test_the_ci_approval_cannot_be_aimed_at_a_fork() -> None:
    # Approving a workflow run hands a runner to whatever code that run checks
    # out, and the approval is found by branch name — which a fork controls, and
    # can set to the release branch's name. Only the head-repository check keeps
    # a fork's run out of the set this step will approve; without it the step is
    # a one-click-free path for an outside PR to run in this repo.
    rp = (REPO_ROOT / ".github" / "workflows" / "release-please.yml").read_text()
    assert ".head_repository.full_name == env.GITHUB_REPOSITORY" in rp, (
        "the approval must be restricted to runs whose head repository is this repo"
    )
    assert "head=${owner}:${BRANCH}" in rp, (
        "the release PR must be looked up by owner-qualified head ref, not branch name alone"
    )


def test_release_is_drafted_so_artifacts_land_before_it_is_public(config: dict) -> None:
    # release-please cuts the release before anything is built; release.yml
    # attaches the sdist/wheel and only then undrafts it. Without this, a
    # published release with no artifacts exists for the length of the build —
    # and permanently, if the build fails.
    assert config.get("draft") is True
