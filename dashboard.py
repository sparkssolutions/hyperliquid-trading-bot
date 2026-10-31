from flask import Flask, jsonify, send_file
import json
import os
import time
import fcntl
from datetime import datetime
from zoneinfo import ZoneInfo
from data_feed import get_candles, get_all_prices, get_funding_rate
from indicators import analyze, ema
from signals import generate_signal
from config import COINS, CANDLE_INTERVAL, TREND_INTERVAL, EMA_FAST, EMA_SLOW, EMA_TREND
from coinglass import get_market_context
from liq_estimator import estimate_liq_zones

app = Flask(__name__)
BOT_DIR = os.path.dirname(__file__)


@app.route("/")
def index():
    return send_file(os.path.join(BOT_DIR, "dashboard.html"))


@app.route("/api/scan")
def scan():
    prices = get_all_prices()
    results = []
    for coin in COINS:
        candles_15m = get_candles(coin, CANDLE_INTERVAL, 200)
        candles_1h = get_candles(coin, TREND_INTERVAL, 100)
        if not candles_15m or not candles_1h:
            continue
        a = analyze(candles_15m)
        t = analyze(candles_1h)
        funding = get_funding_rate(coin)
        try:
            cg = get_market_context(coin)
        except Exception:
            cg = None
        s = generate_signal(a, t, cg, candles_15m)

        results.append({
            "coin": coin,
            "price": prices.get(coin, 0),
            "analysis": a,
            "trend_1h": t["trend"],
            "signal": s,
            "funding": funding,
            "coinglass": cg,
        })
    return jsonify(results)


@app.route("/api/candles/<coin>")
def candles(coin):
    from flask import request
    coin = coin.upper()
    interval = request.args.get("interval", CANDLE_INTERVAL)
    valid = ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]
    if interval not in valid:
        interval = CANDLE_INTERVAL
    limit = 200 if interval in ("5m", "15m") else 150
    data = get_candles(coin, interval, limit)
    closes = [c["close"] for c in data]
    ema_f = ema(closes, EMA_FAST)
    ema_s = ema(closes, EMA_SLOW)
    ema_t = ema(closes, EMA_TREND)
    for i, c in enumerate(data):
        c["ema_fast"] = round(ema_f[i], 2)
        c["ema_slow"] = round(ema_s[i], 2)
        c["ema_trend"] = round(ema_t[i], 2)
    return jsonify(data)


@app.route("/api/state")
def state():
    state_file = os.path.join(BOT_DIR, "paper_state.json")
    if os.path.exists(state_file):
        with open(state_file) as f:
            data = json.load(f)
        prices = get_all_prices()
        for coin, pos in data.get("positions", {}).items():
            p = prices.get(coin, pos["entry_price"])
            if pos["side"] == "LONG":
                pnl = pos["quantity"] * (p - pos["entry_price"])
            else:
                pnl = pos["quantity"] * (pos["entry_price"] - p)
            pos["current_price"] = p
            pos["pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl / pos["size_usd"] * 100, 2)
        data["unrealized"] = round(sum(
            pos.get("pnl", 0) for pos in data.get("positions", {}).values()
        ), 2)
        return jsonify(data)
    return jsonify({"balance": 10000, "positions": {}, "total_trades": 0})


@app.route("/api/open/<coin>/<side>", methods=["POST"])
def open_position(coin, side):
    from config import POSITION_SIZE_PCT, LEVERAGE, STOP_LOSS_PCT, TAKE_PROFIT_PCT, MAX_POSITIONS
    coin = coin.upper()
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        return jsonify({"error": "side must be LONG or SHORT"}), 400
    if coin not in COINS:
        return jsonify({"error": f"{coin} not in COINS list"}), 400

    state_file = os.path.join(BOT_DIR, "paper_state.json")
    trade_log = os.path.join(BOT_DIR, "trade_log.json")

    lock_path = state_file + ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            if os.path.exists(state_file):
                with open(state_file) as f:
                    data = json.load(f)
            else:
                return jsonify({"error": "no state file"}), 400

            positions = data.get("positions", {})
            if coin in positions:
                return jsonify({"error": f"already have {coin} position"}), 400
            if len(positions) >= MAX_POSITIONS:
                return jsonify({"error": f"max {MAX_POSITIONS} positions"}), 400

            balance = data.get("balance", 0)
            size_usd = balance * POSITION_SIZE_PCT
            if size_usd < 10:
                return jsonify({"error": "balance too low"}), 400

            prices = get_all_prices()
            price = prices.get(coin)
            if not price:
                return jsonify({"error": f"no price for {coin}"}), 400

            quantity = (size_usd * LEVERAGE) / price
            if side == "LONG":
                stop_loss = round(price * (1 - STOP_LOSS_PCT), 6)
                take_profit = round(price * (1 + TAKE_PROFIT_PCT), 6)
            else:
                stop_loss = round(price * (1 + STOP_LOSS_PCT), 6)
                take_profit = round(price * (1 - TAKE_PROFIT_PCT), 6)

            liq_tp = None
            try:
                liq = estimate_liq_zones(coin)
                if liq and liq.get("top_clusters"):
                    if side == "LONG":
                        targets = [c for c in liq["top_clusters"] if c["type"] == "short" and c["price"] > price]
                        if targets:
                            nearest = min(targets, key=lambda c: c["price"])
                            dist = (nearest["price"] - price) / price
                            if 0.01 <= dist <= 0.08:
                                liq_tp = round(nearest["price"], 6)
                    else:
                        targets = [c for c in liq["top_clusters"] if c["type"] == "long" and c["price"] < price]
                        if targets:
                            nearest = max(targets, key=lambda c: c["price"])
                            dist = (price - nearest["price"]) / price
                            if 0.01 <= dist <= 0.08:
                                liq_tp = round(nearest["price"], 6)
            except Exception:
                pass

            if liq_tp:
                take_profit = liq_tp

            pos_data = {
                "coin": coin, "side": side,
                "entry_price": price, "size_usd": size_usd,
                "leverage": LEVERAGE, "quantity": quantity,
                "stop_loss": stop_loss, "take_profit": take_profit,
                "trailing_stop": None, "highest_pnl_pct": 0,
                "opened_at": time.time(),
                "entry_score": 0, "entry_confidence": 0,
                "entry_reasons": ["MANUAL"],
            }

            positions[coin] = pos_data
            data["positions"] = positions

            with open(state_file, "w") as f:
                json.dump(data, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    trade = {
        "action": "OPEN", "coin": coin, "side": side,
        "entry": price, "size_usd": round(size_usd, 2),
        "quantity": round(quantity, 6),
        "stop_loss": stop_loss, "take_profit": take_profit,
        "leverage": f"{LEVERAGE}x",
        "score": 0, "confidence": 0,
        "timestamp": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %I:%M:%S %p"),
    }

    log_lock = trade_log + ".lock"
    with open(log_lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            existing = []
            if os.path.exists(trade_log):
                with open(trade_log) as f:
                    existing = json.load(f)
            existing.append(trade)
            with open(trade_log, "w") as f:
                json.dump(existing, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    return jsonify({"ok": True, "trade": trade})


@app.route("/api/close/<coin>", methods=["POST"])
def close_position(coin):
    coin = coin.upper()
    state_file = os.path.join(BOT_DIR, "paper_state.json")
    trade_log = os.path.join(BOT_DIR, "trade_log.json")
    if not os.path.exists(state_file):
        return jsonify({"error": "no state"}), 400

    lock_path = state_file + ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(state_file) as f:
                data = json.load(f)

            positions = data.get("positions", {})
            if coin not in positions:
                return jsonify({"error": f"no {coin} position"}), 400

            pos = positions[coin]
            prices = get_all_prices()
            price = prices.get(coin, pos["entry_price"])

            if pos["side"] == "LONG":
                pnl = pos["quantity"] * (price - pos["entry_price"])
            else:
                pnl = pos["quantity"] * (pos["entry_price"] - price)

            pnl = round(pnl, 2)
            pnl_pct = round(pnl / pos["size_usd"] * 100, 2)

            data["balance"] += pnl
            data["total_pnl"] += pnl
            data["total_trades"] += 1
            if pnl > 0:
                data["wins"] += 1
            else:
                data["losses"] += 1

            held_min = round((time.time() - pos.get("opened_at", time.time())) / 60, 1)

            trade = {
                "action": "CLOSE", "coin": coin, "side": pos["side"],
                "reason": "MANUAL_CLOSE",
                "entry": pos["entry_price"], "exit": price,
                "pnl": pnl, "pnl_pct": pnl_pct,
                "balance": round(data["balance"], 2),
                "held_minutes": held_min,
                "entry_score": pos.get("entry_score", 0),
                "entry_confidence": pos.get("entry_confidence", 0),
                "timestamp": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %I:%M:%S %p"),
            }

            del positions[coin]
            data["positions"] = positions

            with open(state_file, "w") as f:
                json.dump(data, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    log_lock = trade_log + ".lock"
    with open(log_lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            existing = []
            if os.path.exists(trade_log):
                with open(trade_log) as f:
                    existing = json.load(f)
            existing.append(trade)
            with open(trade_log, "w") as f:
                json.dump(existing, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    from sheets_logger import queue_trade
    queue_trade(trade, coin, ["MANUAL"], None)

    return jsonify({"ok": True, "trade": trade})


@app.route("/api/liq-zones/<coin>")
def liq_zones(coin):
    coin = coin.upper()
    try:
        data = estimate_liq_zones(coin)
        if data:
            return jsonify(data)
        return jsonify({"error": "no data"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
def trades():
    log_file = os.path.join(BOT_DIR, "trade_log.json")
    if os.path.exists(log_file):
        with open(log_file) as f:
            return jsonify(json.load(f))
    return jsonify([])


@app.route("/api/strategies")
def strategies():
    from config import STRATEGY_LOG, STRATEGY_VERSION
    from collections import defaultdict

    log_file = os.path.join(BOT_DIR, "trade_log.json")
    all_trades = []
    if os.path.exists(log_file):
        with open(log_file) as f:
            all_trades = json.load(f)

    closes = [t for t in all_trades if t.get("action") == "CLOSE"]

    by_strat = defaultdict(lambda: {
        "wins": 0, "losses": 0, "pnl": 0, "trades": 0,
        "by_reason": defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0}),
        "by_coin": defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0}),
        "by_side": defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0}),
        "avg_held": [],
    })

    for t in closes:
        strat = t.get("strategy", "v1")
        pnl = t.get("pnl", 0)
        d = by_strat[strat]
        d["trades"] += 1
        if pnl > 0:
            d["wins"] += 1
        else:
            d["losses"] += 1
        d["pnl"] += pnl
        d["avg_held"].append(t.get("held_minutes", 0))

        reason = t.get("reason", "?")
        if pnl > 0:
            d["by_reason"][reason]["w"] += 1
        else:
            d["by_reason"][reason]["l"] += 1
        d["by_reason"][reason]["pnl"] += pnl

        coin = t.get("coin", "?")
        if pnl > 0:
            d["by_coin"][coin]["w"] += 1
        else:
            d["by_coin"][coin]["l"] += 1
        d["by_coin"][coin]["pnl"] += pnl

        side = t.get("side", "?")
        if pnl > 0:
            d["by_side"][side]["w"] += 1
        else:
            d["by_side"][side]["l"] += 1
        d["by_side"][side]["pnl"] += pnl

    result = []
    for s in STRATEGY_LOG:
        v = s["version"]
        d = by_strat[v]
        wr = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] > 0 else 0
        avg_h = round(sum(d["avg_held"]) / len(d["avg_held"]), 1) if d["avg_held"] else 0
        result.append({
            "version": v,
            "active": v == STRATEGY_VERSION,
            "start": s["start"],
            "end": s["end"],
            "changes": s["changes"],
            "trades": d["trades"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": wr,
            "pnl": round(d["pnl"], 2),
            "avg_pnl": round(d["pnl"] / d["trades"], 2) if d["trades"] > 0 else 0,
            "avg_held": avg_h,
            "by_reason": {k: dict(v) for k, v in d["by_reason"].items()},
            "by_coin": {k: dict(v) for k, v in d["by_coin"].items()},
            "by_side": {k: dict(v) for k, v in d["by_side"].items()},
        })
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=False)
