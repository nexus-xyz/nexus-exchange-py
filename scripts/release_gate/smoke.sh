#!/usr/bin/env bash
# Pre-publish smoke test (ENG-18798). Builds the sdist and wheel as a release does
# (`python -m build`), installs the wheel into a fresh venv, and runs one unauthenticated read
# against the public testnet with it (scripts/release_gate/smoke.py). No keys, no writes.
#
#   scripts/release_gate/smoke.sh               build, install, read
#   scripts/release_gate/smoke.sh --build-only  build, install and import, no testnet (PRs that are not a release)
#
# Exit codes, kept apart on purpose: 0 passed, 1 failed, 2 testnet unreachable. The workflow fails
# on 1 and on 2, under different names. Unreachable is not a pass, and it is not the SDK's fault:
# re-run the job once testnet answers.
#
# PYTHON (default python3) builds and hosts the venv, and needs `build` installed.
set -euo pipefail

PYTHON="${PYTHON:-python3}"

mode="read"
case "${1:-}" in
  "") ;;
  --build-only) mode="build" ;;
  *) echo "usage: $0 [--build-only]" >&2; exit 64 ;;
esac

root="$(git rev-parse --show-toplevel)"
cd "$root"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if ! "$PYTHON" -m build --outdir "$work/dist" . > "$work/build.log" 2>&1; then
  cat "$work/build.log"
  echo "::error title=prepublish-smoke (failed)::python -m build failed; there is no wheel to install."
  exit 1
fi
wheels=("$work"/dist/*.whl)
if [ "${#wheels[@]}" -ne 1 ] || [ ! -f "${wheels[0]}" ]; then
  echo "::error title=prepublish-smoke (failed)::expected exactly one wheel from python -m build, got: ${wheels[*]}"
  exit 1
fi
wheel="$(basename "${wheels[0]}")"

# The consumer: a venv holding the wheel and the dependencies it declares, and nothing else.
"$PYTHON" -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --quiet --disable-pip-version-check "${wheels[0]}"
# -I, from the temp dir: neither the checkout nor PYTHONPATH can stand in for the wheel.
cd "$work"
"$work/venv/bin/python" -I -c 'import nexus_exchange'
echo "installed $wheel into a clean venv and imported it"

summary() {
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '### Testnet smoke: %s\n\n%s\n' "$1" "$2" >> "$GITHUB_STEP_SUMMARY"
  fi
}

if [ "$mode" = "build" ]; then
  echo "::notice title=prepublish-smoke (read not attempted)::Not a release PR: the wheel built, installed into a clean venv and imported, and no testnet read was made. The read runs on the release PR."
  summary "build only" "Not a release PR: \`${wheel}\` built, installed into a clean venv and imported. No testnet read was attempted, so this is not a smoke pass."
  exit 0
fi

set +e
line="$("$work/venv/bin/python" -I "$root/scripts/release_gate/smoke.py")"
code=$?
set -e
echo "$line"
case "$code" in
  0)
    summary "✅ passed" "$line"
    ;;
  2)
    echo "::error title=prepublish-smoke (testnet unreachable)::NOT a pass: the testnet read got no usable answer, so nothing about this release was verified. Re-run this job once testnet answers. ${line}"
    summary "⚠️ TESTNET UNREACHABLE: not a pass" "$line"
    ;;
  *)
    echo "::error title=prepublish-smoke (failed)::The built wheel could not make an unauthenticated testnet read. ${line}"
    summary "❌ FAILED" "$line"
    code=1
    ;;
esac
exit "$code"
