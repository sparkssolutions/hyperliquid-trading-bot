import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

SHEETS_LOG = os.path.join(os.path.dirname(__file__), "sheets_queue.json")
CT = ZoneInfo("America/Chicago")


def ct_now():
    return datetime.now(CT).strftime("%Y-%m-%d %I:%M:%S %p")


def queue_trade(trade: dict, coin: str, signal_reasons: list[str], analysis: dict | None = None):
    entry = {
        "timestamp": ct_now(),
        "coin": coin,
        "action": trade.get("action", ""),
        "side": trade.get("side", ""),
        "entry_price": trade.get("entry", 0),
        "exit_price": trade.get("exit", 0),
        "size_usd": trade.get("size_usd", 0),
        "leverage": trade.get("leverage", ""),
        "pnl": trade.get("pnl", 0),
        "pnl_pct": trade.get("pnl_pct", 0),
        "exit_reason": trade.get("reason", ""),
        "held_minutes": trade.get("held_minutes", 0),
        "balance_after": trade.get("balance", 0),
        "score": trade.get("score", trade.get("entry_score", 0)),
        "confidence": trade.get("confidence", trade.get("entry_confidence", 0)),
        "thesis": " | ".join(signal_reasons),
        "trend_15m": analysis.get("trend", "") if analysis else "",
        "rsi": analysis.get("rsi", 0) if analysis else 0,
        "macd_hist": analysis.get("macd_histogram", 0) if analysis else 0,
        "support": analysis.get("support", 0) if analysis else 0,
        "resistance": analysis.get("resistance", 0) if analysis else 0,
        "volume_spike": analysis.get("volume_spike", False) if analysis else False,
        "breakout": analysis.get("breakout", "") if analysis else "",
    }

    queue = []
    if os.path.exists(SHEETS_LOG):
        try:
            with open(SHEETS_LOG) as f:
                queue = json.load(f)
        except Exception:
            queue = []
    queue.append(entry)
    with open(SHEETS_LOG, "w") as f:
        json.dump(queue, f, indent=2)
    return entry
