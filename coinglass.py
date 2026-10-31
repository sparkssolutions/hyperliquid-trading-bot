import requests
import time
from config import COINGLASS_API_KEY, COINGLASS_API

HEADERS = {
    "accept": "application/json",
    "CG-API-KEY": COINGLASS_API_KEY,
}

PAIR_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "HYPE": "HYPEUSDT", "ZEC": "ZECUSDT"}
EXCHANGES = "Binance,OKX,Bybit"

_cache = {}
CACHE_TTL = 240


def _get(path: str, params: dict) -> dict | None:
    key = f"{path}:{sorted(params.items())}"
    if key in _cache and time.time() - _cache[key]["t"] < CACHE_TTL:
        return _cache[key]["data"]
    try:
        resp = requests.get(f"{COINGLASS_API}{path}", params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            return None
        _cache[key] = {"data": data["data"], "t": time.time()}
        return data["data"]
    except Exception:
        return None


def get_oi_snapshot(coin: str) -> dict | None:
    data = _get("/api/futures/open-interest/exchange-list", {"symbol": coin})
    if not data:
        return None
    agg = next((d for d in data if d["exchange"] == "All"), None)
    if not agg:
        return None
    return {
        "oi_usd": agg["open_interest_usd"],
        "oi_change_5m": agg.get("open_interest_change_percent_5m", 0),
        "oi_change_15m": agg.get("open_interest_change_percent_15m", 0),
        "oi_change_1h": agg.get("open_interest_change_percent_1h", 0),
        "oi_change_4h": agg.get("open_interest_change_percent_4h", 0),
        "oi_change_24h": agg.get("open_interest_change_percent_24h", 0),
    }


def get_oi_history(coin: str, interval: str = "4h", limit: int = 20) -> list[dict] | None:
    data = _get("/api/futures/open-interest/aggregated-history", {
        "symbol": coin, "interval": interval, "limit": limit,
    })
    if not data:
        return None
    return [{"time": d["time"], "open": float(d["open"]), "close": float(d["close"]),
             "high": float(d["high"]), "low": float(d["low"])} for d in data]


def get_long_short_ratio(coin: str, interval: str = "4h", limit: int = 10) -> list[dict] | None:
    pair = PAIR_MAP.get(coin, f"{coin}USDT")
    data = _get("/api/futures/global-long-short-account-ratio/history", {
        "symbol": pair, "interval": interval, "limit": limit, "exchange": "Binance",
    })
    if not data:
        return None
    return [{
        "time": d["time"],
        "long_pct": d["global_account_long_percent"],
        "short_pct": d["global_account_short_percent"],
        "ratio": d["global_account_long_short_ratio"],
    } for d in data]


def get_funding_rates(coin: str) -> dict | None:
    data = _get("/api/futures/funding-rate/exchange-list", {"symbol": coin})
    if not data or not data[0].get("stablecoin_margin_list"):
        return None
    rates = [r for r in data[0]["stablecoin_margin_list"] if "funding_rate" in r]
    avg_rate = sum(r["funding_rate"] for r in rates) / len(rates) if rates else 0
    return {
        "avg_rate": avg_rate,
        "rates": {r["exchange"]: r["funding_rate"] for r in rates[:5]},
        "sentiment": "bearish" if avg_rate > 0.01 else "bullish" if avg_rate < -0.01 else "neutral",
    }


def get_liquidations(coin: str, interval: str = "4h", limit: int = 10) -> list[dict] | None:
    data = _get("/api/futures/liquidation/aggregated-history", {
        "symbol": coin, "interval": interval, "limit": limit,
        "exchange_list": EXCHANGES,
    })
    if not data:
        return None
    return [{
        "time": d["time"],
        "long_liq": d["aggregated_long_liquidation_usd"],
        "short_liq": d["aggregated_short_liquidation_usd"],
        "bias": "long_pain" if d["aggregated_long_liquidation_usd"] > d["aggregated_short_liquidation_usd"] * 2
                else "short_pain" if d["aggregated_short_liquidation_usd"] > d["aggregated_long_liquidation_usd"] * 2
                else "balanced",
    } for d in data]


def get_taker_volume(coin: str, interval: str = "4h", limit: int = 10) -> list[dict] | None:
    data = _get("/api/futures/aggregated-taker-buy-sell-volume/history", {
        "symbol": coin, "interval": interval, "limit": limit,
        "exchange_list": EXCHANGES,
    })
    if not data:
        return None
    return [{
        "time": d["time"],
        "buy_vol": d["aggregated_buy_volume_usd"],
        "sell_vol": d["aggregated_sell_volume_usd"],
        "ratio": d["aggregated_buy_volume_usd"] / d["aggregated_sell_volume_usd"]
                 if d["aggregated_sell_volume_usd"] > 0 else 1.0,
    } for d in data]


def get_market_context(coin: str) -> dict:
    """Single call that aggregates all Coinglass data for a coin."""
    oi = get_oi_snapshot(coin)
    ls = get_long_short_ratio(coin, limit=3)
    funding = get_funding_rates(coin)
    liqs = get_liquidations(coin, limit=3)
    taker = get_taker_volume(coin, limit=3)

    ctx = {
        "oi": oi,
        "long_short": ls[-1] if ls else None,
        "funding": funding,
        "liquidations": liqs[-1] if liqs else None,
        "taker": taker[-1] if taker else None,
    }

    score = 0
    reasons = []

    if oi:
        if oi["oi_change_1h"] > 2:
            score += 1
            reasons.append(f"OI↑{oi['oi_change_1h']:.1f}% 1h")
        elif oi["oi_change_1h"] < -2:
            score -= 1
            reasons.append(f"OI↓{oi['oi_change_1h']:.1f}% 1h")

    if ls:
        latest = ls[-1]
        if latest["ratio"] > 1.5:
            score -= 1
            reasons.append(f"crowd long {latest['ratio']:.2f} (contrarian short)")
        elif latest["ratio"] < 0.7:
            score += 1
            reasons.append(f"crowd short {latest['ratio']:.2f} (contrarian long)")

    if funding:
        if funding["avg_rate"] > 0.02:
            score -= 1
            reasons.append(f"high funding {funding['avg_rate']:.4f} (longs pay)")
        elif funding["avg_rate"] < -0.02:
            score += 1
            reasons.append(f"neg funding {funding['avg_rate']:.4f} (shorts pay)")

    if liqs:
        latest = liqs[-1]
        if latest["bias"] == "long_pain":
            score -= 1
            reasons.append("long liquidations dominant")
        elif latest["bias"] == "short_pain":
            score += 1
            reasons.append("short liquidations dominant")

    if taker:
        latest = taker[-1]
        if latest["ratio"] > 1.15:
            score += 1
            reasons.append(f"buyers aggressive {latest['ratio']:.2f}")
        elif latest["ratio"] < 0.85:
            score -= 1
            reasons.append(f"sellers aggressive {latest['ratio']:.2f}")

    ctx["score"] = score
    ctx["bias"] = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
    ctx["reasons"] = reasons
    return ctx


if __name__ == "__main__":
    from config import COINS
    for coin in COINS:
        print(f"\n{'='*50}")
        print(f"  {coin} MARKET CONTEXT")
        print(f"{'='*50}")
        ctx = get_market_context(coin)
        if ctx["oi"]:
            oi = ctx["oi"]
            print(f"  OI: ${oi['oi_usd']/1e9:.1f}B | 1h: {oi['oi_change_1h']:+.2f}% | 24h: {oi['oi_change_24h']:+.2f}%")
        if ctx["long_short"]:
            ls = ctx["long_short"]
            print(f"  L/S: {ls['long_pct']:.1f}% long / {ls['short_pct']:.1f}% short (ratio {ls['ratio']:.2f})")
        if ctx["funding"]:
            f = ctx["funding"]
            print(f"  Funding: avg {f['avg_rate']:.4f}% ({f['sentiment']})")
        if ctx["liquidations"]:
            lq = ctx["liquidations"]
            print(f"  Liqs: ${lq['long_liq']/1e6:.1f}M long / ${lq['short_liq']/1e6:.1f}M short ({lq['bias']})")
        if ctx["taker"]:
            tk = ctx["taker"]
            print(f"  Taker: ${tk['buy_vol']/1e9:.2f}B buy / ${tk['sell_vol']/1e9:.2f}B sell (ratio {tk['ratio']:.2f})")
        print(f"\n  SCORE: {ctx['score']} → {ctx['bias'].upper()}")
        for r in ctx["reasons"]:
            print(f"    • {r}")
