from indicators import analyze, ema, support_resistance


MIN_HOLD_SECONDS = 300  # 5 minutes


def check_invalidation(position_side: str, candles: list[dict], entry_price: float, opened_at: float = 0, trend_1h: str | None = None) -> dict | None:
    if len(candles) < 50:
        return None

    import time
    held_seconds = time.time() - opened_at if opened_at else 999999
    if held_seconds < MIN_HOLD_SECONDS:
        return None

    a = analyze(candles)
    closes = [c["close"] for c in candles]
    price = closes[-1]
    pnl_pct = ((price - entry_price) / entry_price) * 100
    if position_side == "SHORT":
        pnl_pct = -pnl_pct

    # TREND_REVERSED and 15M_TREND_FLIP removed — 0W/28L all-time, pure loss generators.
    # Let SL handle downside instead.

    # 2. RSI DIVERGENCE — momentum exhausted against our position
    if position_side == "LONG" and a["rsi"] > 75:
        if pnl_pct > 1.0:
            return {"reason": "RSI_EXTREME", "detail": f"RSI {a['rsi']} overbought on LONG, securing +{pnl_pct:.1f}% before reversal"}

    if position_side == "SHORT" and a["rsi"] < 25:
        if pnl_pct > 1.0:
            return {"reason": "RSI_EXTREME", "detail": f"RSI {a['rsi']} oversold on SHORT, securing +{pnl_pct:.1f}% before bounce"}

    # 3. SUPPORT/RESISTANCE REJECTION — price bounced off key level
    support, resistance = support_resistance(candles, 50)

    if position_side == "SHORT" and a["trend"] != "STRONG_DOWN":
        near_support = price < support * 1.003
        last_4 = candles[-4:]
        bounce = all(c["close"] > c["open"] for c in last_4[-3:])
        low_of_bounce = min(c["low"] for c in last_4)
        bounce_size = (price - low_of_bounce) / low_of_bounce * 100
        if near_support and bounce and bounce_size > 0.5 and pnl_pct > 0:
            return {"reason": "SUPPORT_BOUNCE", "detail": f"Price bounced {bounce_size:.1f}% off support ${support:.2f} with 3 green candles, securing +{pnl_pct:.1f}%"}

    if position_side == "LONG" and a["trend"] != "STRONG_UP":
        near_resistance = price > resistance * 0.997
        last_4 = candles[-4:]
        rejection = all(c["close"] < c["open"] for c in last_4[-3:])
        high_of_rejection = max(c["high"] for c in last_4)
        reject_size = (high_of_rejection - price) / high_of_rejection * 100
        if near_resistance and rejection and reject_size > 0.5 and pnl_pct > 0:
            return {"reason": "RESISTANCE_REJECTION", "detail": f"Price rejected {reject_size:.1f}% from resistance ${resistance:.2f} with 3 red candles, securing +{pnl_pct:.1f}%"}

    # 4. MOMENTUM DEATH — MACD flipped against us while in profit
    if position_side == "LONG" and a["macd_histogram"] < 0 and pnl_pct > 0.5:
        ema_fast = a["ema_fast"]
        ema_slow = a["ema_slow"]
        if ema_fast < ema_slow:
            return {"reason": "MOMENTUM_DIED", "detail": f"EMA cross bearish + MACD flipped negative, securing +{pnl_pct:.1f}%"}

    if position_side == "SHORT" and a["macd_histogram"] > 0 and pnl_pct > 0.5:
        ema_fast = a["ema_fast"]
        ema_slow = a["ema_slow"]
        if ema_fast > ema_slow:
            return {"reason": "MOMENTUM_DIED", "detail": f"EMA cross bullish + MACD flipped positive, securing +{pnl_pct:.1f}%"}

    # 5. DOUBLE TOP/BOTTOM — reversal pattern against position
    dt = a.get("double_top")
    db = a.get("double_bottom")
    if position_side == "LONG" and dt and dt["confirmed"]:
        if pnl_pct > -0.5:
            return {"reason": "DOUBLE_TOP", "detail": f"Confirmed double top @ ${dt['peak']}, neckline ${dt['neckline']} broken. P&L {pnl_pct:+.1f}%"}

    if position_side == "SHORT" and db and db["confirmed"]:
        if pnl_pct > -0.5:
            return {"reason": "DOUBLE_BOTTOM", "detail": f"Confirmed double bottom @ ${db['trough']}, neckline ${db['neckline']} broken. P&L {pnl_pct:+.1f}%"}

    # 6. LIQUIDITY GRAB — stop hunt reversal against position (exit only at >3% profit)
    liq = a.get("liquidity_grab")
    if liq:
        if position_side == "LONG" and liq["type"] == "GRAB_HIGH":
            if pnl_pct > 3.0:
                return {"reason": "LIQ_GRAB", "detail": f"Liquidity grabbed above ${liq['level']} (swept to ${liq['swept_to']}), smart money reversing. Securing +{pnl_pct:.1f}%"}

        if position_side == "SHORT" and liq["type"] == "GRAB_LOW":
            if pnl_pct > 3.0:
                return {"reason": "LIQ_GRAB", "detail": f"Liquidity grabbed below ${liq['level']} (swept to ${liq['swept_to']}), smart money reversing. Securing +{pnl_pct:.1f}%"}

    # 7. STALE TRADE — been open too long without meaningful move
    # If held > 2 hours and P&L between -0.5% and +0.5%, thesis isn't playing out
    held_candles = len(candles)  # rough proxy
    if held_candles > 8 and -0.5 < pnl_pct < 0.5:
        # Check if volatility dried up (last 8 candles range < 0.3%)
        recent = candles[-8:]
        high = max(c["high"] for c in recent)
        low = min(c["low"] for c in recent)
        range_pct = (high - low) / low * 100
        if range_pct < 0.3:
            return {"reason": "STALE_TRADE", "detail": f"Held {held_candles} candles, P&L {pnl_pct:+.1f}%, volatility died ({range_pct:.2f}% range)"}

    return None
