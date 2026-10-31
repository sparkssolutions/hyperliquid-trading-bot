from config import (
    EMA_FAST, EMA_SLOW, EMA_TREND,
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    VOLUME_SPIKE_MULT,
)


def ema(closes: list[float], period: int) -> list[float]:
    if len(closes) < period:
        return [closes[-1]] * len(closes)
    mult = 2 / (period + 1)
    values = [sum(closes[:period]) / period]
    for price in closes[period:]:
        values.append((price - values[-1]) * mult + values[-1])
    pad = [values[0]] * (len(closes) - len(values))
    return pad + values


def rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_values = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))
    pad = [50.0] * (len(closes) - len(rsi_values))
    return pad + rsi_values


def macd(closes: list[float]) -> tuple[list[float], list[float], list[float]]:
    ema_fast = ema(closes, MACD_FAST)
    ema_slow = ema(closes, MACD_SLOW)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, MACD_SIGNAL)
    histogram = [m - s for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, histogram


def volume_spike(volumes: list[float], lookback: int = 20) -> bool:
    if len(volumes) < lookback + 1:
        return False
    avg_vol = sum(volumes[-lookback - 1:-1]) / lookback
    if avg_vol == 0:
        return False
    return volumes[-1] > avg_vol * VOLUME_SPIKE_MULT


def detect_trend(closes: list[float]) -> str:
    ema_f = ema(closes, EMA_FAST)
    ema_s = ema(closes, EMA_SLOW)
    ema_t = ema(closes, EMA_TREND)
    price = closes[-1]
    fast = ema_f[-1]
    slow = ema_s[-1]
    trend = ema_t[-1]

    if fast > slow > trend and price > fast:
        return "STRONG_UP"
    elif fast > slow and price > slow:
        return "UP"
    elif fast < slow < trend and price < fast:
        return "STRONG_DOWN"
    elif fast < slow and price < slow:
        return "DOWN"
    return "SIDEWAYS"


def support_resistance(candles: list[dict], lookback: int = 50) -> tuple[float, float]:
    recent = candles[-lookback:]
    highs = [c["high"] for c in recent]
    lows = [c["low"] for c in recent]
    resistance = max(highs)
    support = min(lows)
    return support, resistance


def is_breakout(candles: list[dict], lookback: int = 20) -> str | None:
    if len(candles) < lookback + 1:
        return None
    prev = candles[-lookback - 1:-1]
    current = candles[-1]
    prev_high = max(c["high"] for c in prev)
    prev_low = min(c["low"] for c in prev)
    if current["close"] > prev_high:
        return "BREAKOUT_UP"
    elif current["close"] < prev_low:
        return "BREAKOUT_DOWN"
    return None


def detect_double_top(candles: list[dict], lookback: int = 40, tolerance: float = 0.003) -> dict | None:
    if len(candles) < lookback:
        return None
    recent = candles[-lookback:]
    highs = [c["high"] for c in recent]

    peak1_idx = 0
    peak1_val = highs[0]
    for i in range(1, len(highs) // 2):
        if highs[i] > peak1_val:
            peak1_val = highs[i]
            peak1_idx = i

    valley_idx = peak1_idx
    valley_val = highs[peak1_idx]
    for i in range(peak1_idx + 1, min(peak1_idx + lookback // 2, len(highs))):
        if recent[i]["low"] < valley_val:
            valley_val = recent[i]["low"]
            valley_idx = i

    if valley_idx <= peak1_idx + 2:
        return None
    pullback = (peak1_val - valley_val) / peak1_val
    if pullback < 0.005:
        return None

    peak2_idx = valley_idx
    peak2_val = highs[valley_idx]
    for i in range(valley_idx + 1, len(highs)):
        if highs[i] > peak2_val:
            peak2_val = highs[i]
            peak2_idx = i

    if peak2_idx <= valley_idx + 1:
        return None
    if peak2_idx < len(highs) - 5:
        return None

    diff = abs(peak1_val - peak2_val) / peak1_val
    if diff > tolerance:
        return None

    neckline = valley_val
    price = recent[-1]["close"]
    if price < neckline:
        return {"type": "DOUBLE_TOP", "peak": round(peak1_val, 2),
                "neckline": round(neckline, 2), "confirmed": True}

    if price < peak2_val and price > neckline:
        return {"type": "DOUBLE_TOP", "peak": round(peak1_val, 2),
                "neckline": round(neckline, 2), "confirmed": False}

    return None


def detect_double_bottom(candles: list[dict], lookback: int = 40, tolerance: float = 0.003) -> dict | None:
    if len(candles) < lookback:
        return None
    recent = candles[-lookback:]
    lows = [c["low"] for c in recent]

    trough1_idx = 0
    trough1_val = lows[0]
    for i in range(1, len(lows) // 2):
        if lows[i] < trough1_val:
            trough1_val = lows[i]
            trough1_idx = i

    peak_idx = trough1_idx
    peak_val = lows[trough1_idx]
    for i in range(trough1_idx + 1, min(trough1_idx + lookback // 2, len(lows))):
        if recent[i]["high"] > peak_val:
            peak_val = recent[i]["high"]
            peak_idx = i

    if peak_idx <= trough1_idx + 2:
        return None
    bounce = (peak_val - trough1_val) / trough1_val
    if bounce < 0.005:
        return None

    trough2_idx = peak_idx
    trough2_val = lows[peak_idx]
    for i in range(peak_idx + 1, len(lows)):
        if lows[i] < trough2_val:
            trough2_val = lows[i]
            trough2_idx = i

    if trough2_idx <= peak_idx + 1:
        return None
    if trough2_idx < len(lows) - 5:
        return None

    diff = abs(trough1_val - trough2_val) / trough1_val
    if diff > tolerance:
        return None

    neckline = peak_val
    price = recent[-1]["close"]
    if price > neckline:
        return {"type": "DOUBLE_BOTTOM", "trough": round(trough1_val, 2),
                "neckline": round(neckline, 2), "confirmed": True}

    if price > trough2_val and price < neckline:
        return {"type": "DOUBLE_BOTTOM", "trough": round(trough1_val, 2),
                "neckline": round(neckline, 2), "confirmed": False}

    return None


def detect_liquidity_grab(candles: list[dict], lookback: int = 30) -> dict | None:
    if len(candles) < lookback + 3:
        return None

    prev = candles[-(lookback + 3):-3]
    prev_high = max(c["high"] for c in prev)
    prev_low = min(c["low"] for c in prev)

    last3 = candles[-3:]
    grab_candle = last3[0]
    confirm1 = last3[1]
    confirm2 = last3[2]

    body = abs(grab_candle["close"] - grab_candle["open"])
    full_range = grab_candle["high"] - grab_candle["low"]
    if full_range == 0:
        return None

    upper_wick = grab_candle["high"] - max(grab_candle["close"], grab_candle["open"])
    lower_wick = min(grab_candle["close"], grab_candle["open"]) - grab_candle["low"]

    if grab_candle["high"] > prev_high:
        sweep = grab_candle["high"] - prev_high
        if grab_candle["close"] < prev_high and upper_wick > body * 1.5:
            bearish_confirm = confirm1["close"] < confirm1["open"] or confirm2["close"] < confirm2["open"]
            if bearish_confirm:
                return {"type": "GRAB_HIGH", "level": round(prev_high, 2),
                        "swept_to": round(grab_candle["high"], 2),
                        "sweep_pct": round(sweep / prev_high * 100, 3)}

    if grab_candle["low"] < prev_low:
        sweep = prev_low - grab_candle["low"]
        if grab_candle["close"] > prev_low and lower_wick > body * 1.5:
            bullish_confirm = confirm1["close"] > confirm1["open"] or confirm2["close"] > confirm2["open"]
            if bullish_confirm:
                return {"type": "GRAB_LOW", "level": round(prev_low, 2),
                        "swept_to": round(grab_candle["low"], 2),
                        "sweep_pct": round(sweep / prev_low * 100, 3)}

    return None


def find_swing_levels(candles: list[dict], lookback: int = 100, swing_window: int = 5, cluster_pct: float = 0.008) -> dict:
    """Find swing highs/lows where price has bounced. Returns {support: [...], resistance: [...]}."""
    if len(candles) < lookback:
        recent = candles
    else:
        recent = candles[-lookback:]

    swing_lows = []
    swing_highs = []

    for i in range(swing_window, len(recent) - swing_window):
        low_i = recent[i]["low"]
        high_i = recent[i]["high"]

        is_swing_low = all(low_i <= recent[j]["low"] for j in range(i - swing_window, i + swing_window + 1) if j != i)
        if is_swing_low:
            swing_lows.append({"price": low_i, "index": i})

        is_swing_high = all(high_i >= recent[j]["high"] for j in range(i - swing_window, i + swing_window + 1) if j != i)
        if is_swing_high:
            swing_highs.append({"price": high_i, "index": i})

    def cluster_levels(levels):
        if not levels:
            return []
        sorted_lvls = sorted(levels, key=lambda x: x["price"])
        clusters = []
        current = [sorted_lvls[0]]
        for lvl in sorted_lvls[1:]:
            if abs(lvl["price"] - current[0]["price"]) / current[0]["price"] <= cluster_pct:
                current.append(lvl)
            else:
                avg_price = sum(l["price"] for l in current) / len(current)
                most_recent = max(l["index"] for l in current)
                clusters.append({"price": round(avg_price, 2), "touches": len(current), "last_index": most_recent})
                current = [lvl]
        avg_price = sum(l["price"] for l in current) / len(current)
        most_recent = max(l["index"] for l in current)
        clusters.append({"price": round(avg_price, 2), "touches": len(current), "last_index": most_recent})
        return sorted(clusters, key=lambda x: x["touches"], reverse=True)

    return {
        "support": cluster_levels(swing_lows)[:5],
        "resistance": cluster_levels(swing_highs)[:5],
    }


def analyze(candles: list[dict]) -> dict:
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    ema_f = ema(closes, EMA_FAST)
    ema_s = ema(closes, EMA_SLOW)
    rsi_vals = rsi(closes)
    macd_line, signal_line, histogram = macd(closes)
    trend = detect_trend(closes)
    vol_spike = volume_spike(volumes)
    breakout = is_breakout(candles)
    support, resistance = support_resistance(candles)

    double_top = detect_double_top(candles)
    double_bottom = detect_double_bottom(candles)
    liq_grab = detect_liquidity_grab(candles)
    swing_levels = find_swing_levels(candles)

    return {
        "price": closes[-1],
        "ema_fast": round(ema_f[-1], 2),
        "ema_slow": round(ema_s[-1], 2),
        "rsi": round(rsi_vals[-1], 1),
        "macd": round(macd_line[-1], 4),
        "macd_signal": round(signal_line[-1], 4),
        "macd_histogram": round(histogram[-1], 4),
        "trend": trend,
        "volume_spike": vol_spike,
        "breakout": breakout,
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "double_top": double_top,
        "double_bottom": double_bottom,
        "liquidity_grab": liq_grab,
        "swing_levels": swing_levels,
    }
