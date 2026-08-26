"""Recent fills and withdrawal history — signed (HMAC).

Reads the authenticated account's own executions (``GET /api/v1/fills``, on the
direct service) and its withdrawal records (``GET /withdrawals``, still on the
legacy gateway). Read-only.

    NEXUS_API_KEY=... NEXUS_API_SECRET=... NEXUS_BASE_URL=http://localhost:9090 \\
        python examples/fills_and_withdrawals.py
"""

from __future__ import annotations

from _shared import make_signed_client


def main() -> None:
    with make_signed_client() as client:
        fills = client.fetch_my_trades()
        print(f"recent fills ({len(fills)}):")
        for f in fills[:10]:
            print(
                f"  {f.market_id} {f.side:4} {f.size} @ {f.price} "
                f"fee={f.fee} {f.taker_or_maker or '?'}"
            )

        withdrawals = client.fetch_withdrawals()
        print(f"\nwithdrawals ({len(withdrawals)}):")
        for w in withdrawals[:10]:
            print(f"  {w.id} {w.amount} status={w.status} t={w.timestamp}")


if __name__ == "__main__":
    main()
