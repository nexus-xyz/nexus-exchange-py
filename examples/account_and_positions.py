"""Account balances, open positions, and rate-limit status — signed (HMAC).

Reads the authenticated account's collateral summary, its open positions, and
the caller's current rate-limit budget.

    NEXUS_API_KEY=... NEXUS_API_SECRET=... NEXUS_BASE_URL=http://localhost:9090 \\
        python examples/account_and_positions.py

The public gateway proxies signed calls to the *site* account; point
NEXUS_BASE_URL at a direct gateway for per-account auth (see the README).
"""

from __future__ import annotations

from _shared import make_signed_client


def main() -> None:
    with make_signed_client() as client:
        acct = client.fetch_balance()
        print("account:")
        print(f"  balance={acct.balance} collateral={acct.collateral}")
        print(f"  equity={acct.equity} available_margin={acct.available_margin}")

        positions = client.fetch_positions()
        print(f"\nopen positions ({len(positions)}):")
        for p in positions:
            liq = "-" if p.liquidation_price is None else p.liquidation_price
            print(
                f"  {p.market_id} {p.side} size={p.size} entry={p.entry_price} "
                f"uPnL={p.unrealized_pnl} liq={liq}"
            )

        rl = client.fetch_rate_limit_status()
        print(f"\nrate limit: tier={rl.tier} limit={rl.limit} remaining={rl.remaining}")


if __name__ == "__main__":
    main()
