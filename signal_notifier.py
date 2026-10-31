import requests
import threading
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, SIGNAL_MIN_CONFIDENCE

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _send_telegram(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
        }, timeout=10)
    except Exception:
        pass


def _send_all(text: str):
    for cid in TELEGRAM_CHAT_IDS:
        threading.Thread(target=_send_telegram, args=(cid, text), daemon=True).start()


def notify_open(coin: str, side: str, price: float, signal: dict, leverage: str):
    confidence = signal.get("confidence", 0)
    if confidence < SIGNAL_MIN_CONFIDENCE:
        return
    score = signal.get("score", 0)
    reasons = signal.get("reasons", [])
    sign = "+" if score > 0 else ""

    emoji = "🟢" if side == "LONG" else "🔴"
    text = (
        f"{emoji} {side} {coin} @ ${price:,.2f}\n"
        f"Confidence: {confidence:.0%} ({sign}{score}/7)\n"
        f"Leverage: {leverage}\n"
        f"Reasons: {', '.join(reasons[:5])}\n"
        f"\n⚠️ Paper trade signal — not financial advice"
    )
    _send_all(text)


def notify_close(coin: str, side: str, entry: float, exit_price: float,
                 pnl: float, pnl_pct: float, reason: str, held_min: float):
    emoji = "✅" if pnl >= 0 else "❌"
    pnl_sign = "+" if pnl >= 0 else ""
    text = (
        f"{emoji} CLOSED {side} {coin} — {reason}\n"
        f"Entry: ${entry:,.2f} → Exit: ${exit_price:,.2f}\n"
        f"P&L: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)\n"
        f"Held: {held_min:.0f}m"
    )
    _send_all(text)


def notify_ai_decision(coin: str, side: str, price: float, signal: dict,
                       decision: str, reason: str):
    score = signal.get("score", 0)
    confidence = signal.get("confidence", 0)
    reasons = signal.get("reasons", [])
    sign = "+" if score > 0 else ""

    if decision == "CONFIRM":
        emoji = "🟢" if side == "LONG" else "🔴"
        header = f"{emoji} AI CONFIRMED {side} {coin}"
    elif decision == "REVERSE":
        rev_side = "SHORT" if side == "LONG" else "LONG"
        emoji = "🔄"
        header = f"{emoji} AI REVERSED {side} → {rev_side} {coin}"
    else:
        return  # don't spam Telegram with vetoes

    text = (
        f"{header} @ ${price:,.2f}\n"
        f"Confidence: {confidence:.0%} ({sign}{score}/7)\n"
        f"Signals: {', '.join(reasons[:4])}\n"
        f"AI: {reason}"
    )
    _send_all(text)


def notify_ai_exit(coin: str, side: str, entry: float, price: float,
                   pnl: float, pnl_pct: float, reason: str):
    pnl_sign = "+" if pnl >= 0 else ""
    text = (
        f"🤖 AI EXIT {side} {coin}\n"
        f"Entry: ${entry:,.2f} → ${price:,.2f}\n"
        f"P&L: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)\n"
        f"AI: {reason}"
    )
    _send_all(text)


def notify_positions_update(positions: list[dict], balance: float, total_pnl: float):
    if not positions:
        return
    lines = ["📊 POSITION UPDATE"]
    for p in positions:
        emoji = "📈" if p["pnl"] >= 0 else "📉"
        pnl_sign = "+" if p["pnl"] >= 0 else ""
        lines.append(
            f"{emoji} {p['side']} {p['coin']}: "
            f"${p['entry']:,.2f} → ${p['price']:,.2f} "
            f"({pnl_sign}{p['pnl_pct']:.1f}%) "
            f"{pnl_sign}${p['pnl']:.2f}"
        )
    lines.append(f"\nBalance: ${balance:,.2f} | Total P&L: ${total_pnl:+,.2f}")
    _send_all("\n".join(lines))


def is_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS)
