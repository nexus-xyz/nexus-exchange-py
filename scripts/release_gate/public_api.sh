#!/usr/bin/env bash
# Public-surface snapshot (ENG-18798). Lists the public API of the package exactly as a release
# builds it (the wheel `python -m build` makes, installed into a clean venv) and compares it with
# the committed `public-api.txt`.
#
#   scripts/release_gate/public_api.sh           check: fail on any difference (CI, `prepublish-surface`)
#   scripts/release_gate/public_api.sh --write   regenerate public-api.txt after a deliberate API change
#
# Any difference fails, additions included, so the snapshot moves in the same PR as the code. A
# removed or changed item then shows up as a `-` line in the diff a reviewer reads, instead of
# first surfacing in a user's `pip install --upgrade`.
#
# The listing is scripts/release_gate/public_surface.py, run inside that venv. It renders
# signatures with `inspect`, whose output moves between Python minor versions, so the snapshot is
# generated with one pinned version: 3.12, the one pre-publish.yml installs. public_surface.py
# refuses any other. PYTHON (default python3) must be that version, with `build` installed:
#
#   uv venv --seed -p 3.12 /tmp/gate && uv pip install --python /tmp/gate/bin/python build
#   PYTHON=/tmp/gate/bin/python scripts/release_gate/public_api.sh --write
set -euo pipefail

PYTHON="${PYTHON:-python3}"
SNAPSHOT="public-api.txt"

mode="check"
case "${1:-}" in
  "") ;;
  --write) mode="write" ;;
  *) echo "usage: $0 [--write]" >&2; exit 2 ;;
esac

root="$(git rev-parse --show-toplevel)"
cd "$root"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# What a publish would upload: release.yml runs `python -m build`, which builds the sdist and then
# the wheel from the unpacked sdist, so a file the sdist leaves out is missing here too. Into a
# temp dir, so dist/ is left alone.
if ! "$PYTHON" -m build --outdir "$work/dist" . > "$work/build.log" 2>&1; then
  cat "$work/build.log"
  echo "::error title=prepublish-surface::python -m build failed; nothing to list."
  exit 1
fi
wheels=("$work"/dist/*.whl)
if [ "${#wheels[@]}" -ne 1 ] || [ ! -f "${wheels[0]}" ]; then
  echo "::error title=prepublish-surface::expected exactly one wheel from python -m build, got: ${wheels[*]}"
  exit 1
fi
wheel="$(basename "${wheels[0]}")"

# A clean venv with the wheel and its declared dependencies, nothing from the source tree. -I
# keeps the working directory, PYTHONPATH and user site-packages off sys.path, and
# public_surface.py checks that the package it imported is the one installed here.
"$PYTHON" -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --quiet --disable-pip-version-check "${wheels[0]}"
(cd "$work" && "$work/venv/bin/python" -I "$root/scripts/release_gate/public_surface.py") > "$work/public-api.txt"

if [ "$mode" = "write" ]; then
  cp "$work/public-api.txt" "$SNAPSHOT"
  echo "wrote $SNAPSHOT ($(wc -l < "$SNAPSHOT") items) from $wheel"
  exit 0
fi

if diff -u --label "$SNAPSHOT (committed)" --label "$SNAPSHOT (built $wheel)" \
  "$SNAPSHOT" "$work/public-api.txt" > "$work/diff.txt"; then
  echo "public surface matches $SNAPSHOT ($(wc -l < "$SNAPSHOT") items)"
  exit 0
fi

removed="$(grep -c '^-[^-]' "$work/diff.txt" || true)"
added="$(grep -c '^+[^+]' "$work/diff.txt" || true)"
cat "$work/diff.txt"
echo "::error title=prepublish-surface::The built wheel's public API differs from $SNAPSHOT: ${removed} item(s) gone or changed, ${added} new. If that is deliberate, run scripts/release_gate/public_api.sh --write and commit $SNAPSHOT in this PR, so the change is in the diff a reviewer reads. A removal or change is breaking."
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Public surface: ❌ differs from \`$SNAPSHOT\`"
    echo
    echo "${removed} item(s) gone or changed (\`-\`), ${added} new (\`+\`). Regenerate with \`scripts/release_gate/public_api.sh --write\` if deliberate."
    echo
    echo '```diff'
    cat "$work/diff.txt"
    echo '```'
  } >> "$GITHUB_STEP_SUMMARY"
fi
exit 1
