"""Tests for scripts/changelog_notes.py (ENG-6135).

The release workflow turns the hand-written CHANGELOG into the GitHub release
body via this helper, so its extraction and its strictness (fail on missing or
empty sections) are release-safety guarantees, not niceties.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "changelog_notes.py"

# scripts/ is not an importable package; load the module straight from its path.
_spec = importlib.util.spec_from_file_location("changelog_notes", SCRIPT)
assert _spec and _spec.loader
changelog_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changelog_notes)

SAMPLE = """\
# Changelog

## [Unreleased]

## [0.3.0] - 2026-07-16

### Added

- A shiny thing.

### Changed

- A different thing.

## [0.2.0] - 2026-07-07

### Changed

- Older stuff.
"""


def test_extracts_the_requested_section_only():
    notes = changelog_notes.extract_notes(SAMPLE, "0.3.0")
    assert "A shiny thing." in notes
    assert "A different thing." in notes
    # Must not bleed into the next release's section.
    assert "Older stuff." not in notes
    # Trimmed: no leading/trailing blank lines, and the heading itself is excluded.
    assert notes == notes.strip()
    assert "## [0.3.0]" not in notes


def test_extracts_last_section_at_end_of_file():
    notes = changelog_notes.extract_notes(SAMPLE, "0.2.0")
    assert "Older stuff." in notes
    assert "A shiny thing." not in notes


@pytest.mark.parametrize("raw", ["0.3.0", "v0.3.0"])
def test_normalize_accepts_bare_and_v_prefixed(raw):
    assert changelog_notes.normalize_version(raw) == "0.3.0"


@pytest.mark.parametrize("bad", ["0.3", "1.2.3.4", "abc", "v", "", "0.3.0-rc1"])
def test_normalize_rejects_malformed_versions(bad):
    with pytest.raises(SystemExit):
        changelog_notes.normalize_version(bad)


def test_normalize_rejects_unreleased():
    # "Unreleased" is not X.Y.Z-shaped, so it is rejected at the shape gate.
    with pytest.raises(SystemExit):
        changelog_notes.normalize_version("unreleased")


def test_missing_section_fails_loudly():
    with pytest.raises(SystemExit):
        changelog_notes.extract_notes(SAMPLE, "9.9.9")


def test_empty_section_fails_loudly():
    text = "## [1.0.0] - 2026-01-01\n\n## [0.9.0] - 2025-12-01\n\n- notes\n"
    with pytest.raises(SystemExit):
        changelog_notes.extract_notes(text, "1.0.0")


def test_version_is_regex_escaped_not_interpreted():
    # A version string is validated to bare digits+dots upstream, but confirm the
    # extractor treats the dots literally: "1.0.0" must not match "1x0x0".
    text = "## [1x0x0] - 2026-01-01\n\n- trap\n"
    with pytest.raises(SystemExit):
        changelog_notes.extract_notes(text, "1.0.0")


def test_real_changelog_has_notes_for_current_version():
    # Guard the actual repo file so a release of the current pin would succeed.
    # Use the package's resolved version (from installed distribution metadata,
    # i.e. pyproject.toml) rather than tomllib, which is stdlib only on 3.11+.
    from nexus_exchange import __version__

    text = (REPO_ROOT / "CHANGELOG.md").read_text()
    notes = changelog_notes.extract_notes(text, __version__)
    assert notes.strip()
