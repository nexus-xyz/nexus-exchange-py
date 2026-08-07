"""Walk a full fill history with cursor pagination (spec v0.7.2).

``iter_my_trades`` follows the ``X-Next-Cursor`` response header page by page,
lazily — one signed request per page — so nothing beyond the current page is
held in memory. Credentials come from the environment so they stay out of
source.

    export NEXUS_API_KEY=...      # hex api key
    export NEXUS_API_SECRET=...   # hex api secret
    python examples/paginate_fills.py
"""

from __future__ import annotations

import os

from nexus_exchange import Client, PaginationError


def main() -> None:
    api_key = os.environ.get("NEXUS_API_KEY")
    api_secret = os.environ.get("NEXUS_API_SECRET")
    if not (api_key and api_secret):
        raise SystemExit("set NEXUS_API_KEY and NEXUS_API_SECRET to run this example")

    with Client(api_key=api_key, api_secret=api_secret) as client:
        # `limit` is the page size, not a total. `max_pages` bounds the walk —
        # worth setting on an account with a long history, since nothing else
        # limits how far back this goes.
        count = 0
        try:
            for fill in client.iter_my_trades(limit=500, max_pages=20):
                count += 1
                print(f"  {fill.side:<4} {fill.size} @ {fill.price}  (fee {fill.fee})")
        except PaginationError as exc:
            # The server handed back a cursor that does not advance, so the walk
            # stopped rather than re-requesting one page forever. What was read
            # so far is incomplete — say so instead of treating it as the end.
            print(f"pagination stalled after {count} fill(s): {exc}")
            raise SystemExit(1) from exc

        print(f"{count} fill(s) total")

        # The manual form, for a cursor that has to outlive the process:
        page = client.fetch_my_trades_page(limit=500)
        print(f"first page: {len(page.items)} fill(s), is_last={page.is_last}")
        if not page.is_last:
            print(f"resume with cursor={page.next_cursor!r}")


if __name__ == "__main__":
    main()
