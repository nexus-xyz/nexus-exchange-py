"""Public market data — no credentials required.

Walks the public market-data surface: list markets, then for the first market
fetch its ticker, order-book top of book, recent trades, and recent candles.

    python examples/public_market_data.py

Targets the stable public gateway by default. Override with NEXUS_BASE_URL
(e.g. http://localhost:9090) or NEXUS_NETWORK (stable|beta|local).
"""

from __future__ import annotations

from _shared import make_client


def main() -> None:
    with make_client() as client:
        markets = client.fetch_markets()
        print(f"{len(markets)} markets")
        for market in markets[:5]:
            print(" -", market.market_id)
        if not markets:
            return

        mid = markets[0].market_id

        ticker = client.fetch_ticker(mid)
        print(f"\nticker {mid}:")
        print(f"  last={ticker.last} mark={ticker.mark_price} bid={ticker.bid} ask={ticker.ask}")

        book = client.fetch_order_book(mid)
        top_bid = book.bids[0] if book.bids else None
        top_ask = book.asks[0] if book.asks else None
        print(f"\norder book {mid}: {len(book.bids)} bids / {len(book.asks)} asks")
        if top_bid:
            print(f"  best bid: {top_bid.amount} @ {top_bid.price}")
        if top_ask:
            print(f"  best ask: {top_ask.amount} @ {top_ask.price}")

        trades = client.fetch_trades(mid, limit=5)
        print(f"\nrecent trades {mid} ({len(trades)}):")
        for t in trades:
            print(f"  {t.side:4} {t.amount} @ {t.price}")

        candles = client.fetch_ohlcv(mid, timeframe="1m", limit=5)
        print(f"\nrecent 1m candles {mid} ({len(candles)}):")
        for c in candles:
            print(f"  t={c.timestamp} o={c.open} h={c.high} l={c.low} c={c.close} v={c.volume}")


if __name__ == "__main__":
    main()
