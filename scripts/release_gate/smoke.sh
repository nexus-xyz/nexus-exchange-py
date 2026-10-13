#!/usr/bin/env bash
# Pre-publish smoke test (ENG-18798). Builds the sdist and wheel as a release does
# (`python -m build`), installs the wheel into a fresh venv, and runs one unauthenticated read
# against the public testnet with it (scripts/release_gate/smoke.py). No keys, no writes.
#
#   scripts/release_gate/smoke.sh               build, install, prove the outcomes, read
#   scripts/release_gate/smoke.sh --build-only  build, install, import, prove the outcomes, no testnet
#                                               (PRs that are not a release)
#
# Proving the outcomes, in both modes, before any testnet read: the just-installed smoke is run
# twice with NEXUS_SMOKE_BASE_URL pointed away from testnet. At http://127.0.0.1:1, where nothing
# listens, it must exit 2 (unreachable); at scripts/release_gate/stub_server.py, which answers
# every request with an empty list, it must exit 1 (failed). Any other code fails this script
# with a `prepublish-smoke (outcomes)` annotation, so a smoke that would read a dead testnet or an
# empty answer as a pass never gets to grade a release.
#
# Exit codes, kept apart on purpose: 0 passed, 1 failed, 2 testnet unreachable (or refused this
# runner's location). The workflow fails on 1 and on 2, under different names. Unreachable is not a
# pass, and it is not the SDK's fault: re-run the job once testnet answers.
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
stub_pid=""
cleanup() {
  if [ -n "$stub_pid" ]; then
    kill "$stub_pid" 2> /dev/null || true
  fi
  rm -rf "$work"
}
trap cleanup EXIT

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

# The outcome probes. The smoke runs directly, not through the `case` below, because exit 1 and 2
# are what these expect, and the annotations that case prints for them would read as a failure.
outcomes_failed() {
  echo "::error title=prepublish-smoke (outcomes)::$1"
  summary "❌ OUTCOMES WRONG" "$1"
  exit 1
}
probe() {
  local name="$1" url="$2" want="$3" out got
  set +e
  out="$(NEXUS_SMOKE_BASE_URL="$url" "$work/venv/bin/python" -I "$root/scripts/release_gate/smoke.py")"
  got=$?
  set -e
  echo "outcome probe, ${name}: exit ${got} (want ${want}): ${out}"
  if [ "$got" -ne "$want" ]; then
    outcomes_failed "Pointed at ${name} (${url}), the smoke exited ${got}, not ${want}, so its exit codes cannot be trusted to keep a pass, a failure and an unreachable testnet apart. ${out}"
  fi
}
probe "a port nothing listens on" "http://127.0.0.1:1" 2
"$PYTHON" -I "$root/scripts/release_gate/stub_server.py" > "$work/stub.port" 2> "$work/stub.log" &
stub_pid=$!
for _ in $(seq 50); do
  if [ -s "$work/stub.port" ]; then
    break
  fi
  sleep 0.1
done
port="$(head -n 1 "$work/stub.port")"
if [ -z "$port" ]; then
  cat "$work/stub.log"
  outcomes_failed "stub_server.py printed no port within 5s, so the failed outcome was not probed."
fi
probe "a stub that answers with nothing" "http://127.0.0.1:${port}" 1
kill "$stub_pid" 2> /dev/null || true
wait "$stub_pid" 2> /dev/null || true
stub_pid=""
echo "outcome probes passed: unreachable exits 2, an empty answer exits 1"

if [ "$mode" = "build" ]; then
  echo "::notice title=prepublish-smoke (read not attempted)::Not a release PR: the wheel built, installed into a clean venv and imported, its outcomes were probed, and no testnet read was made. The read runs on the release PR."
  summary "build only" "Not a release PR: \`${wheel}\` built, installed into a clean venv and imported, and its exit codes were proved against a closed port (2) and an empty stub (1). No testnet read was attempted, so this is not a smoke pass."
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
    echo "::error title=prepublish-smoke (testnet unreachable)::NOT a pass: the testnet read got no usable answer, so nothing about this release was verified. Re-run this job once testnet answers; if it refused this runner's location, re-running from the same place will not help. ${line}"
    summary "⚠️ TESTNET UNREACHABLE: not a pass" "$line"
    ;;
  *)
    echo "::error title=prepublish-smoke (failed)::The built wheel could not make an unauthenticated testnet read. ${line}"
    summary "❌ FAILED" "$line"
    code=1
    ;;
esac
exit "$code"
