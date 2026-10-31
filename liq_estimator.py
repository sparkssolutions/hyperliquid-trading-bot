"""
Estimated liquidation zone calculator.
Uses OI, long/short ratio, and recent price action to estimate
where liquidation clusters sit at various price levels.
"""
from data_feed import get_candles, get_all_prices
from coinglass import get_oi_snapshot, get_long_short_ratio

LEVERAGE_TIERS = [3, 5, 10, 20, 25, 50, 100]
LEVERAGE_WEIGHTS = [0.05, 0.20, 0.30, 0.20, 0.12, 0.08, 0.05]
NUM_BUCKETS = 60
PRICE_RANGE_PCT = 0.08  # ±8% from current price


def estimate_liq_zones(coin: str) -> dict | None:
    candles = get_candles(coin, "15m", 96)  # last 24h
    if not candles or len(candles) < 20:
        return None

    prices = get_all_prices()
    current_price = prices.get(coin)
    if not current_price:
        return None

    oi = get_oi_snapshot(coin)
    ls = get_long_short_ratio(coin, limit=1)

    total_oi = oi["oi_usd"] if oi else 50_000_000
    long_pct = ls[-1]["long_pct"] / 100 if ls else 0.5
    short_pct = 1 - long_pct

    long_oi = total_oi * long_pct
    short_oi = total_oi * short_pct

    price_low = current_price * (1 - PRICE_RANGE_PCT)
    price_high = current_price * (1 + PRICE_RANGE_PCT)
    bucket_size = (price_high - price_low) / NUM_BUCKETS

    long_buckets = [0.0] * NUM_BUCKETS
    short_buckets = [0.0] * NUM_BUCKETS
    bucket_prices = [price_low + (i + 0.5) * bucket_size for i in range(NUM_BUCKETS)]

    total_vol = sum(c.get("volume", 1) for c in candles) or 1

    for candle in candles:
        entry = candle["close"]
        vol_weight = candle.get("volume", 1) / total_vol

        for lev, lev_weight in zip(LEVERAGE_TIERS, LEVERAGE_WEIGHTS):
            # Where would longs entered here get liquidated?
            long_liq_price = entry * (1 - 1 / lev)
            # Where would shorts entered here get liquidated?
            short_liq_price = entry * (1 + 1 / lev)

            weight = vol_weight * lev_weight

            # Add long liquidation to bucket
            if price_low <= long_liq_price <= price_high:
                idx = int((long_liq_price - price_low) / bucket_size)
                idx = min(idx, NUM_BUCKETS - 1)
                long_buckets[idx] += long_oi * weight

            # Add short liquidation to bucket
            if price_low <= short_liq_price <= price_high:
                idx = int((short_liq_price - price_low) / bucket_size)
                idx = min(idx, NUM_BUCKETS - 1)
                short_buckets[idx] += short_oi * weight

    # Normalize to relative values (0-100 scale)
    max_val = max(max(long_buckets), max(short_buckets), 1)

    # Build cumulative curves
    cum_long = 0.0
    cum_short = 0.0
    cum_long_list = []
    cum_short_list = []
    for i in range(NUM_BUCKETS):
        cum_long += long_buckets[i]
        cum_long_list.append(cum_long)
    for i in range(NUM_BUCKETS - 1, -1, -1):
        cum_short += short_buckets[i]
        cum_short_list.insert(0, cum_short)

    cum_max = max(max(cum_long_list), max(cum_short_list), 1)

    zones = []
    for i in range(NUM_BUCKETS):
        zones.append({
            "price": round(bucket_prices[i], 2),
            "long_liq": round(long_buckets[i] / max_val * 100, 1),
            "short_liq": round(short_buckets[i] / max_val * 100, 1),
            "long_liq_usd": round(long_buckets[i]),
            "short_liq_usd": round(short_buckets[i]),
            "cum_long": round(cum_long_list[i] / cum_max * 100, 1),
            "cum_short": round(cum_short_list[i] / cum_max * 100, 1),
        })

    # Find top clusters
    all_levels = []
    for i, z in enumerate(zones):
        if z["long_liq"] > 30:
            all_levels.append({"price": z["price"], "type": "long", "strength": z["long_liq"], "usd": z["long_liq_usd"]})
        if z["short_liq"] > 30:
            all_levels.append({"price": z["price"], "type": "short", "strength": z["short_liq"], "usd": z["short_liq_usd"]})

    all_levels.sort(key=lambda x: x["strength"], reverse=True)

    return {
        "coin": coin,
        "current_price": current_price,
        "price_low": round(price_low, 2),
        "price_high": round(price_high, 2),
        "total_oi": total_oi,
        "long_pct": round(long_pct * 100, 1),
        "short_pct": round(short_pct * 100, 1),
        "zones": zones,
        "top_clusters": all_levels[:6],
    }


if __name__ == "__main__":
    from config import COINS
    for coin in COINS[:2]:
        result = estimate_liq_zones(coin)
        if result:
            print(f"\n{'='*50}")
            print(f"  {coin} ESTIMATED LIQ ZONES")
            print(f"  Price: ${result['current_price']:,.2f}")
            print(f"  OI: ${result['total_oi']/1e9:.2f}B")
            print(f"  Long/Short: {result['long_pct']:.0f}%/{result['short_pct']:.0f}%")
            print(f"\n  Top clusters:")
            for c in result["top_clusters"]:
                arrow = "🔴 LONG" if c["type"] == "long" else "🟢 SHORT"
                print(f"    {arrow} liq @ ${c['price']:,.2f} — ${c['usd']/1e6:.1f}M ({c['strength']:.0f}%)")
