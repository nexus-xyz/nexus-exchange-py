"""Place a limit order, list it, then cancel it — signed (HMAC).

Demonstrates the order lifecycle: build an ``OrderRequest``, place it, read it
back via ``fetch_order`` / ``fetch_open_orders``, then cancel it. Uses a limit
price far from the market so it rests on the book rather than filling.

    NEXUS_API_KEY=... NEXUS_API_SECRET=... NEXUS_BASE_URL=http://localhost:9090 \\
        python examples/place_and_cancel_order.py [MARKET_ID]

MARKET_ID defaults to BTC-USDX-PERP.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from _shared import make_signed_client

from nexus_exchange import OrderRequest


def main() -> None:
    market_id = sys.argv[1] if len(sys.argv) > 1 else "BTC-USDX-PERP"
    with make_signed_client() as client:
        # A resting bid well below market so it does not fill immediately.
        order = OrderRequest.limit(market_id, "Buy", Decimal("1000"), Decimal("0.001"))
        resp = client.create_order(order)

        # `oid` is read INSIDE the try, not before it (@Luc-Campos in review).
        # The order exists on the book the moment `create_order` returns, so any
        # raise between that return and the `finally` leaves it resting — which is
        # exactly what the `finally` was added to prevent. A malformed response
        # (`resp.order` absent, so `.id` raises AttributeError) is the realistic
        # case, and it was the one path still able to leak an order.
        #
        # The consequence: `oid` may be unbound when `finally` runs, so the
        # cleanup has to tolerate that rather than assume it.
        oid: str | None = None
        try:
            oid = resp.order.id
            print(
                f"placed {oid}: {resp.order.side} {resp.order.quantity} @ {resp.order.price} "
                f"status={resp.order.status}"
            )

            fetched = client.fetch_order(oid)
            print(f"fetched {fetched.id}: status={fetched.status} filled={fetched.filled_qty}")

            open_orders = client.fetch_open_orders()
            print(f"open orders: {len(open_orders)}")
        finally:
            # Always cancel, even if a read above raises, so the example never
            # leaves a resting order on the book.
            #
            # `oid is None` means the failure happened before the id could be read
            # — there is nothing to cancel by id, and calling with None would
            # replace the original traceback with a confusing one. Say so instead:
            # if the order did reach the book, the operator needs to know a manual
            # cancel may be required, and silence here would hide that.
            if oid is None:
                print(
                    "could not read the order id from the create-order response; "
                    "if the order reached the book it must be cancelled manually",
                    file=sys.stderr,
                )
            else:
                client.cancel_order(oid)
                print(f"cancelled {oid}")


if __name__ == "__main__":
    main()
