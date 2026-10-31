from config import RSI_OVERBOUGHT, RSI_OVERSOLD, CHOP_RANGE_PCT


def generate_signal(analysis: dict, trend_analysis: dict | None = None, cg_context: dict | None = None, candles: list[dict] | None = None) -> dict:
    score = 0
    reasons = []
    trend = analysis["trend"]
    rsi_val = analysis["rsi"]
    macd_hist = analysis["macd_histogram"]
    vol_spike = analysis["volume_spike"]
    breakout = analysis["breakout"]
    is_choppy = False

    # Chop filter: if last 6 candles range < threshold, market is ranging
    if candles and len(candles) >= 6:
        recent = candles[-6:]
        high = max(c["high"] for c in recent)
        low = min(c["low"] for c in recent)
        range_pct = (high - low) / low
        if range_pct < CHOP_RANGE_PCT:
            is_choppy = True
            reasons.append(f"CHOP({range_pct*100:.2f}%)")

    # Trend alignment (+/- 2)
    if trend in ("STRONG_UP", "UP"):
        score += 2 if trend == "STRONG_UP" else 1
        reasons.append(f"trend={trend}")
    elif trend in ("STRONG_DOWN", "DOWN"):
        score -= 2 if trend == "STRONG_DOWN" else -1
        reasons.append(f"trend={trend}")

    # Higher timeframe trend confirmation (+/- 1)
    if trend_analysis:
        ht = trend_analysis["trend"]
        if ht in ("STRONG_UP", "UP"):
            score += 1
            reasons.append(f"1h_trend={ht}")
        elif ht in ("STRONG_DOWN", "DOWN"):
            score -= 1
            reasons.append(f"1h_trend={ht}")

    # RSI momentum (+/- 1)
    if rsi_val < RSI_OVERSOLD:
        score += 1
        reasons.append(f"RSI_oversold={rsi_val}")
    elif rsi_val > RSI_OVERBOUGHT:
        score -= 1
        reasons.append(f"RSI_overbought={rsi_val}")
    elif 40 < rsi_val < 60:
        reasons.append(f"RSI_neutral={rsi_val}")

    # MACD momentum (+/- 1)
    if macd_hist > 0:
        score += 1
        reasons.append("MACD_bullish")
    elif macd_hist < 0:
        score -= 1
        reasons.append("MACD_bearish")

    # Volume confirmation (+/- 1)
    if vol_spike:
        if score > 0:
            score += 1
            reasons.append("vol_spike_confirms_long")
        elif score < 0:
            score -= 1
            reasons.append("vol_spike_confirms_short")

    # Breakout (+/- 2)
    if breakout == "BREAKOUT_UP":
        score += 2
        reasons.append("BREAKOUT_UP")
    elif breakout == "BREAKOUT_DOWN":
        score -= 2
        reasons.append("BREAKOUT_DOWN")

    # Double top/bottom (+/- 2 confirmed, +/- 1 forming)
    dt = analysis.get("double_top")
    db = analysis.get("double_bottom")
    if dt:
        if dt["confirmed"]:
            score -= 2
            reasons.append(f"DOUBLE_TOP_confirmed@{dt['peak']}")
        else:
            score -= 1
            reasons.append(f"DOUBLE_TOP_forming@{dt['peak']}")
    if db:
        if db["confirmed"]:
            score += 2
            reasons.append(f"DOUBLE_BOTTOM_confirmed@{db['trough']}")
        else:
            score += 1
            reasons.append(f"DOUBLE_BOTTOM_forming@{db['trough']}")

    # Liquidity grab (+/- 2) — reversal signal
    liq = analysis.get("liquidity_grab")
    if liq:
        if liq["type"] == "GRAB_HIGH":
            score -= 2
            reasons.append(f"LIQ_GRAB_HIGH@{liq['level']}")
        elif liq["type"] == "GRAB_LOW":
            score += 2
            reasons.append(f"LIQ_GRAB_LOW@{liq['level']}")

    # Coinglass market context (+/- 2)
    if cg_context:
        cg_score = cg_context.get("score", 0)
        if cg_score >= 2:
            score += 2
            reasons.append(f"CG_bullish({cg_score})")
        elif cg_score <= -2:
            score -= 2
            reasons.append(f"CG_bearish({cg_score})")
        elif cg_score != 0:
            if cg_score > 0:
                score += 1
            else:
                score -= 1
            reasons.append(f"CG_lean({cg_score})")
        for r in cg_context.get("reasons", []):
            reasons.append(f"cg:{r}")

    # Timeframe conflict detection — if 15m and 1h trends oppose, require higher conviction
    tf_conflict = False
    if trend_analysis:
        ht = trend_analysis["trend"]
        bullish_15m = trend in ("STRONG_UP", "UP")
        bearish_15m = trend in ("STRONG_DOWN", "DOWN")
        bullish_1h = ht in ("STRONG_UP", "UP")
        bearish_1h = ht in ("STRONG_DOWN", "DOWN")
        if (bullish_15m and bearish_1h) or (bearish_15m and bullish_1h):
            tf_conflict = True
            reasons.append("TF_CONFLICT")

    # Determine action — require stronger signal when uncertain
    if is_choppy and tf_conflict:
        entry_threshold = 7
    elif is_choppy or tf_conflict:
        entry_threshold = 6
    else:
        entry_threshold = 5

    if score >= entry_threshold:
        action = "LONG"
        confidence = min(score / 7, 1.0)
    elif score <= -entry_threshold:
        action = "SHORT"
        confidence = min(abs(score) / 7, 1.0)
    else:
        action = "HOLD"
        confidence = 0

    return {
        "action": action,
        "score": score,
        "confidence": round(confidence, 2),
        "reasons": reasons,
        "tf_conflict": tf_conflict,
    }
