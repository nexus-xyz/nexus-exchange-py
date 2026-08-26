"""Make a signed (HMAC-authenticated) request via the low-level escape hatch.

Typed account/trading methods aren't built yet, but the request plumbing
already signs calls. Until typed methods land, drop down to ``Client._request``
with ``signed=True`` to reach authenticated routes. Credentials come from the
environment so they stay out of source.

    export NEXUS_API_KEY=...      # hex api key
    export NEXUS_API_SECRET=...   # hex api secret
    python examples/signed_request.py
"""

from __future__ import annotations

import os

from nexus_exchange import Client


def main() -> None:
    api_key = os.environ.get("NEXUS_API_KEY")
    api_secret = os.environ.get("NEXUS_API_SECRET")
    if not (api_key and api_secret):
        raise SystemExit("set NEXUS_API_KEY and NEXUS_API_SECRET to run this example")

    with Client(api_key=api_key, api_secret=api_secret) as client:
        # `_request` is the low-level escape hatch: pick a method, path, and any
        # query string, and set `signed=True` to attach HMAC auth headers.
        # `/account` is served by the direct /api/v1 service (ENG-4946), so pass
        # `direct=True` — the client adds the /api/v1 prefix and signs over the
        # full prefixed path, exactly as the server verifies it.
        result = client._request("GET", "/account", signed=True, direct=True)
        print(result)


if __name__ == "__main__":
    main()
