"""Client-side request-signing benchmark (ENG-15689).

Measures only the signer (no HTTP, no order serialization) for the two
request-auth schemes the API ships, on one fixed order-placement request:

- ``hmac``: ``Client._sign`` with API-key credentials (``hmacAuth``), i.e.
  HMAC-SHA256 over the canonical string with the hex secret decoded per call,
  exactly the code path every signed request takes.
- ``agent``: ``AgentSigner.headers`` (``agentAuth``), i.e. secp256k1 ECDSA over
  keccak256 of the canonical string, low-S, 65-byte ``r||s||v``, with a fresh
  nonce issued on every iteration as on a real write.

The ECDSA cost depends on which ``eth_keys`` backend is active: the pure-Python
``NativeECCBackend`` that a plain ``pip install nexus-exchange`` gets, or
``CoinCurveECCBackend`` (libsecp256k1) once ``coincurve`` is installed. The
backend in use is printed with every result.

A ``time.perf_counter_ns`` loop: warm up, then time each signature individually
and print p50 / p95 and signatures/sec, in the same format as the Rust and
TypeScript SDK benches so the numbers line up.

Run: ``python bench/signing_bench.py``
"""

from __future__ import annotations

import os
import platform
import time
from collections.abc import Callable

from eth_keys.backends import get_backend

from nexus_exchange import AgentSigner, Client, Network

# Shared fixture: identical bytes in all three SDK benches.
METHOD = "POST"
PATH = "/api/v1/orders"
QUERY = ""
BODY = (
    b'{"market_id":"BTC-USDX-PERP","side":"Buy","order_type":"Limit","price":"50000",'
    b'"quantity":"0.1","time_in_force":"GTC","client_order_id":"bench-0000000001"}'
)
TIMESTAMP_MS = 1_776_033_900_000
HMAC_KEY_ID = "nx_bench"
HMAC_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
# Well-known public test key; never fund it.
AGENT_KEY = "0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"

WARMUP_S = 2.0
SAMPLES = int(os.environ.get("BENCH_SAMPLES", "2000"))


def measure(scheme: str, sign_once: Callable[[], object]) -> None:
    warm_end = time.perf_counter() + WARMUP_S
    while time.perf_counter() < warm_end:
        sign_once()

    clock = time.perf_counter_ns
    ns = [0] * SAMPLES
    wall = clock()
    for i in range(SAMPLES):
        t = clock()
        sign_once()
        ns[i] = clock() - t
    wall_s = (clock() - wall) / 1e9
    ns.sort()

    def pct(p: float) -> str:
        return f"{ns[min(int(len(ns) * p), len(ns) - 1)] / 1e3:.2f}"

    print(
        f"RESULT sdk=py scheme={scheme} n={SAMPLES} p50_us={pct(0.50)} p95_us={pct(0.95)} "
        f"sig_per_s={SAMPLES / wall_s:.0f} python={platform.python_version()} "
        f"ecc_backend={type(get_backend()).__name__}"
    )


def main() -> None:
    client = Client(Network.TESTNET, api_key=HMAC_KEY_ID, api_secret=HMAC_SECRET)
    client._now_ms = lambda: TIMESTAMP_MS  # pin the timestamp; no request is sent
    agent = AgentSigner.from_hex(AGENT_KEY)

    measure("hmac", lambda: client._sign(METHOD, PATH, QUERY, BODY))
    measure("agent", lambda: agent.headers(METHOD, PATH, QUERY, BODY, TIMESTAMP_MS))


if __name__ == "__main__":
    main()
