#!/usr/bin/env python3
"""Print the CHANGELOG.md section for a single released version.

Used by `.github/workflows/release.yml` to turn the hand-written changelog into
the body of the GitHub release, so release notes have exactly one source of
truth (CHANGELOG.md) rather than being re-typed at tag time.

The changelog follows Keep a Changelog: each release is a level-2 heading like

    ## [0.3.0] - 2026-07-16

    ### Added
    ...

This extracts the body between that heading and the next `## ` heading (or end
of file). It is intentionally strict — a release must have a matching,
non-empty section — so the workflow fails loudly rather than publishing a
release with empty or wrong notes.

Usage:
  changelog_notes.py <version>            # 0.3.0 or v0.3.0
  changelog_notes.py <version> --file CHANGELOG.md
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_CHANGELOG = os.path.join(REPO, "CHANGELOG.md")

# A release version is strictly `X.Y.Z` (numeric). An optional leading `v` is
# accepted for convenience (tags are `vX.Y.Z`) and stripped. Validated before
# it is ever interpolated into the section regex so untrusted input (a tag name
# off a workflow input) cannot inject regex metacharacters.
VERSION_RE = re.compile(r"^v?([0-9]+\.[0-9]+\.[0-9]+)$")


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def normalize_version(raw):
    """Return the bare `X.Y.Z` for a `X.Y.Z` or `vX.Y.Z` input, or fail.

    The `X.Y.Z` shape gate also rejects staging labels like `Unreleased`, which
    are never releasable.
    """
    m = VERSION_RE.match(raw.strip())
    if not m:
        fail(f"version must look like X.Y.Z or vX.Y.Z (got: {raw!r})")
    return m.group(1)


def extract_notes(text, version):
    """Return the trimmed body of the `## [version]` changelog section.

    Fails if the section is absent (the release was never written up) or present
    but empty (a placeholder with no notes) — both are release-time mistakes we
    want surfaced, not silently shipped.
    """
    # Anchor on the section heading. `[version]` is the Keep a Changelog form;
    # allow anything after the bracket (e.g. ` - 2026-07-16`) on the same line.
    # `version` is already validated to bare digits+dots, so re.escape guards the
    # dots without any injection risk.
    #
    # The body runs to the next `## ` heading, a link-reference-definition line
    # (`[id]: url`, which Keep a Changelog collects at the file's end), or EOF —
    # whichever comes first. Stopping at link definitions keeps them out of the
    # notes for the *last* section, which would otherwise absorb everything to EOF.
    pattern = re.compile(
        r"^##\s*\[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^##\s|^\[[^\]]+\]:|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        fail(
            f"no '## [{version}]' section in the changelog; add the release notes "
            f"before tagging {version}."
        )
    notes = m.group(1).strip()
    if not notes:
        fail(f"the '## [{version}]' changelog section is empty; write the notes first.")
    return notes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", help="release version (X.Y.Z or vX.Y.Z)")
    ap.add_argument(
        "--file",
        default=DEFAULT_CHANGELOG,
        metavar="PATH",
        help="changelog path (default: CHANGELOG.md at the repo root)",
    )
    args = ap.parse_args()

    version = normalize_version(args.version)
    try:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        fail(f"cannot read {args.file}: {e}")

    sys.stdout.write(extract_notes(text, version) + "\n")


if __name__ == "__main__":
    main()
