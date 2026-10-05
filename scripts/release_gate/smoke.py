"""Pre-publish smoke test (ENG-18798): one unauthenticated read against the public testnet,
through the wheel exactly as ``python -m build`` builds it.

``scripts/release_gate/smoke.sh`` installs the built wheel into a clean venv and runs this file
there with ``python -I``, so it sees what a ``pip install nexus-exchange`` user would get, not the
source tree. The read lists markets, the same read every SDK's smoke test makes:
``fetch_markets_summary``, the keyless way to enumerate them. ``scripts/smoke.py`` and the
examples list them with ``fetch_markets`` instead, which on testnet today returns three markets
whose ``market_id`` is empty: testnet serves the CCXT shape while the spec pin sits at v0.8.1
(ENG-18801), and ``Market.from_dict`` reads a missing ``market_id`` as ``""``. Main and the
published 0.6.0 alike. That is also why a market only counts here when it carries its id.

Three outcomes, kept apart by exit code, because "could not reach testnet" must never read as a
pass and is not the SDK's fault either:

  0  passed       the read decoded at least one market, each with a market_id
  1  failed       the SDK got an answer and could not use it (decode, 4xx, empty list, no ids)
  2  unreachable  no usable answer from testnet: network, timeout, 5xx, 408, 429

No keys, no writes, no orders. ``NEXUS_SMOKE_BASE_URL`` points it elsewhere, for testing the
outcomes themselves. Stdlib and the installed package only.
"""

from __future__ import annotations

import os
import sys
from typing import NoReturn


def finish(code: int, message: str) -> NoReturn:
    outcome = {0: "passed", 2: "unreachable"}.get(code, "failed")
    # One line, bounded: smoke.sh puts it in a `::error` annotation, which ends at a newline,
    # and an error body can be a whole HTML page.
    text = " ".join(message.split())
    print(f"smoke: {outcome}: {text if len(text) <= 400 else text[:400] + ' ...'}")
    sys.exit(code)


def main() -> NoReturn:
    try:
        import nexus_exchange as sdk
    except Exception as exc:  # the package itself is broken: nothing was read
        finish(1, f"cannot import nexus_exchange from the installed wheel: {exc!r}")

    url = os.environ.get("NEXUS_SMOKE_BASE_URL", "").strip()
    try:
        network = (
            sdk.NetworkConfig.custom(label="smoke", funds=sdk.Funds.UNKNOWN, base_url=url)
            if url
            else sdk.Network.TESTNET
        )
        client = sdk.Client(network)
    except ValueError as exc:
        finish(1, f"NEXUS_SMOKE_BASE_URL is not usable: {exc}")
    target = client.base_url
    what = f"nexus-exchange {sdk.__version__}: fetch_markets_summary against {target}"

    try:
        with client:
            markets = client.fetch_markets_summary()
    except sdk.TransportError as exc:
        finish(2, f"{target} gave no usable answer: transport error: {exc}")
    except sdk.ApiError as exc:
        # The statuses the SDK itself marks transient: the server, not the request.
        if exc.status >= 500 or exc.status in (408, 429):
            finish(2, f"{target} gave no usable answer: {exc}")
        finish(1, f"{what} was refused: {exc}")
    except Exception as exc:  # decode or anything else: an answer the SDK could not use
        finish(1, f"{what} failed: {type(exc).__name__}: {exc}")

    if not markets:
        finish(1, f"{what} decoded an EMPTY list")
    missing = sum(1 for market in markets if not market.market_id)
    if missing:
        finish(1, f"{what} decoded {len(markets)} markets, {missing} with no market_id")
    finish(0, f"{what} decoded {len(markets)} markets (first: {markets[0].market_id})")


if __name__ == "__main__":
    main()
