import requests
import time
from config import HYPERLIQUID_API, COINS

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}


def get_candles(coin: str, interval: str = "15m", limit: int = 200) -> list[dict]:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (INTERVAL_MS[interval] * limit)
    payload = {
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
    }
    resp = requests.post(f"{HYPERLIQUID_API}/info", json=payload, timeout=10)
    resp.raise_for_status()
    raw = resp.json()
    candles = []
    for c in raw:
        candles.append({
            "time": c["t"],
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"]),
        })
    return candles


def get_mid_price(coin: str) -> float:
    payload = {"type": "allMids"}
    resp = requests.post(f"{HYPERLIQUID_API}/info", json=payload, timeout=10)
    resp.raise_for_status()
    mids = resp.json()
    return float(mids.get(coin, 0))


def get_all_prices() -> dict[str, float]:
    payload = {"type": "allMids"}
    resp = requests.post(f"{HYPERLIQUID_API}/info", json=payload, timeout=10)
    resp.raise_for_status()
    mids = resp.json()
    return {coin: float(mids.get(coin, 0)) for coin in COINS}


def get_funding_rate(coin: str) -> float | None:
    payload = {"type": "metaAndAssetCtxs"}
    resp = requests.post(f"{HYPERLIQUID_API}/info", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    meta = data[0]["universe"]
    ctxs = data[1]
    for i, asset in enumerate(meta):
        if asset["name"] == coin:
            return float(ctxs[i]["funding"])
    return None


if __name__ == "__main__":
    for coin in COINS:
        price = get_mid_price(coin)
        funding = get_funding_rate(coin)
        candles = get_candles(coin, "15m", 10)
        print(f"{coin}: ${price:,.2f} | funding: {funding} | last 10 candles loaded")
