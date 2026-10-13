#!/usr/bin/env bash
# Pre-publish smoke test (ENG-18798). Builds the sdist and wheel as a release does
# (`python -m build`), installs the wheel into a fresh venv, and runs one unauthenticated read
# against the public testnet with it (scripts/release_gate/smoke.py). No keys, no writes.
#
#   scripts/release_gate/smoke.sh               build, install, probe exits 2 and 1, read
#   scripts/release_gate/smoke.sh --build-only  build, install, import, probe exits 2 and 1, no testnet
#                                               (PRs that are not a release)
#
# The probes, in both modes, before any testnet read: the just-installed smoke is run three times
# with NEXUS_SMOKE_BASE_URL pointed away from testnet, each under a 180s timeout. At
# http://127.0.0.1:1, where nothing listens, it must exit 2 (unreachable); at
# scripts/release_gate/stub_server.py answering every request with an empty list, 1 (failed); at
# the same stub answering 503 to everything, 2 again, so an outage never reads as failed or
# passed. Any other code, or a timeout, fails this script with a `prepublish-smoke (outcomes)`
# annotation. So exits 2 and 1 are exercised on every PR. Exit 0 is exercised only by the real
# read, which runs on a release PR or a change to this gate.
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
# Its stdout and stderr both go to a file, so nothing it prints becomes an annotation; each probe
# logs one plain line instead. A probe that runs past 180s is a failure of its own: long enough
# for the SDK's own retries with backoff to finish first.
outcomes_failed() {
  echo "::error title=prepublish-smoke (outcomes)::$1"
  summary "❌ OUTCOMES WRONG" "$1"
  exit 1
}
# `timeout` is GNU coreutils: a Mac has none, or `gtimeout` from Homebrew. Without either the
# probes still run, with no time limit, and say so once.
limit=()
if command -v timeout > /dev/null; then
  limit=(timeout 180)
elif command -v gtimeout > /dev/null; then
  limit=(gtimeout 180)
else
  echo "note: neither timeout nor gtimeout is installed, so the outcome probes run without a time limit"
fi
probe() {
  local name="$1" url="$2" want="$3" first got
  set +e
  # ${limit[@]+...}: an empty array under `set -u` is an error in bash 3.2, the Mac's.
  NEXUS_SMOKE_BASE_URL="$url" ${limit[@]+"${limit[@]}"} \
    "$work/venv/bin/python" -I "$root/scripts/release_gate/smoke.py" > "$work/probe.log" 2>&1
  got=$?
  set -e
  first="$(head -n 1 "$work/probe.log")"
  echo "outcome probe, ${name}: exit ${got} (want ${want}): ${first}"
  if [ "${#limit[@]}" -gt 0 ] && [ "$got" -eq 124 ]; then
    outcomes_failed "Pointed at ${name} (${url}), the smoke did not finish within 180s."
  fi
  if [ "$got" -ne "$want" ]; then
    outcomes_failed "Pointed at ${name} (${url}), the smoke exited ${got}, not ${want}, so its exit codes cannot be trusted to keep a pass, a failure and an unreachable testnet apart. ${first}"
  fi
}
# stub_server.py in the background, with an optional status for every answer; sets `port`.
start_stub() {
  : > "$work/stub.port"
  "$PYTHON" -I "$root/scripts/release_gate/stub_server.py" "$@" > "$work/stub.port" 2> "$work/stub.log" &
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
    outcomes_failed "stub_server.py $* printed no port within 5s, so its outcome was not probed."
  fi
}
stop_stub() {
  kill "$stub_pid" 2> /dev/null || true
  wait "$stub_pid" 2> /dev/null || true
  stub_pid=""
}
probe "a port nothing listens on" "http://127.0.0.1:1" 2
start_stub
probe "a stub that answers with nothing" "http://127.0.0.1:${port}" 1
stop_stub
start_stub 503
probe "a stub that answers 503 to everything" "http://127.0.0.1:${port}" 2
stop_stub
echo "outcome probes passed: a closed port and a 503 exit 2 (unreachable), an empty answer exits 1 (failed)"

if [ "$mode" = "build" ]; then
  echo "::notice title=prepublish-smoke (read not attempted)::Not a release PR: the wheel built, installed into a clean venv and imported, its exits 2 and 1 were exercised, and no testnet read was made. The read runs on the release PR."
  summary "build only" "Not a release PR: \`${wheel}\` built, installed into a clean venv and imported, and its exit codes were proved against a closed port and a 503 stub (2) and an empty stub (1). No testnet read was attempted, so this is not a smoke pass."
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
