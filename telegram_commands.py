import requests
import json
import os
import time
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
from data_feed import get_all_prices

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BOT_DIR, "paper_state.json")
TRADE_LOG = os.path.join(BOT_DIR, "trade_log.json")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
OFFSET_FILE = os.path.join(BOT_DIR, ".telegram_offset")


def get_updates(offset=None):
    params = {"timeout": 30, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35)
        return r.json().get("result", [])
    except Exception:
        return []


def send_reply(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
        }, timeout=10)
    except Exception:
        pass


def handle_positions(chat_id):
    if not os.path.exists(STATE_FILE):
        send_reply(chat_id, "No state file found.")
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    positions = state.get("positions", {})
    if not positions:
        send_reply(chat_id, "📭 No open positions.")
        return

    prices = get_all_prices()
    lines = ["📊 OPEN POSITIONS\n"]
    total_unrealized = 0

    for coin, pos in positions.items():
        price = prices.get(coin, pos["entry_price"])
        entry = pos["entry_price"]
        size = pos["size_usd"]
        qty = pos["quantity"]

        if pos["side"] == "LONG":
            pnl = qty * (price - entry)
        else:
            pnl = qty * (entry - price)

        pnl_pct = pnl / size * 100
        total_unrealized += pnl
        emoji = "📈" if pnl >= 0 else "📉"
        pnl_sign = "+" if pnl >= 0 else ""

        held_min = (time.time() - pos.get("opened_at", time.time())) / 60
        lines.append(
            f"{emoji} {pos['side']} {coin}\n"
            f"   Entry: ${entry:,.2f} → ${price:,.2f}\n"
            f"   P&L: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)\n"
            f"   SL: ${pos['stop_loss']:,.2f} | TP: ${pos['take_profit']:,.2f}\n"
            f"   Held: {held_min:.0f}m"
        )

    lines.append(f"\nUnrealized: ${total_unrealized:+,.2f}")
    send_reply(chat_id, "\n".join(lines))


def handle_balance(chat_id):
    if not os.path.exists(STATE_FILE):
        send_reply(chat_id, "No state file found.")
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    balance = state.get("balance", 0)
    total_pnl = state.get("total_pnl", 0)
    trades = state.get("total_trades", 0)
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    wr = (wins / trades * 100) if trades > 0 else 0

    prices = get_all_prices()
    unrealized = 0
    for coin, pos in state.get("positions", {}).items():
        price = prices.get(coin, pos["entry_price"])
        qty = pos["quantity"]
        if pos["side"] == "LONG":
            unrealized += qty * (price - pos["entry_price"])
        else:
            unrealized += qty * (pos["entry_price"] - price)

    equity = balance + unrealized

    text = (
        f"💰 ACCOUNT SUMMARY\n\n"
        f"Balance: ${balance:,.2f}\n"
        f"Unrealized: ${unrealized:+,.2f}\n"
        f"Equity: ${equity:,.2f}\n\n"
        f"Realized P&L: ${total_pnl:+,.2f}\n"
        f"Trades: {trades} ({wins}W/{losses}L)\n"
        f"Win Rate: {wr:.0f}%\n"
        f"Open Positions: {len(state.get('positions', {}))}"
    )
    send_reply(chat_id, text)


def handle_trades(chat_id):
    if not os.path.exists(TRADE_LOG):
        send_reply(chat_id, "No trades yet.")
        return

    with open(TRADE_LOG) as f:
        trades = json.load(f)

    recent = [t for t in trades if t["action"] == "CLOSE"][-5:]
    if not recent:
        send_reply(chat_id, "No closed trades yet.")
        return

    lines = ["📋 LAST 5 TRADES\n"]
    for t in recent:
        pnl = t.get("pnl", 0)
        emoji = "✅" if pnl >= 0 else "❌"
        pnl_sign = "+" if pnl >= 0 else ""
        lines.append(
            f"{emoji} {t['side']} {t['coin']} {pnl_sign}${pnl:.2f} ({t.get('reason', '')})"
        )
    send_reply(chat_id, "\n".join(lines))


def handle_open(chat_id, text, side):
    parts = text.strip().split()
    if len(parts) < 2:
        send_reply(chat_id, f"Usage: /{side.lower()} <COIN>\nExample: /{side.lower()} BTC")
        return

    coin = parts[1].upper()

    try:
        r = requests.post(f"http://127.0.0.1:5555/api/open/{coin}/{side}", timeout=10)
        data = r.json()
        if data.get("ok"):
            t = data["trade"]
            send_reply(chat_id,
                f"✅ OPENED {side} {coin}\n"
                f"Entry: ${t['entry']:,.2f}\n"
                f"Size: ${t['size_usd']:,.2f} ({t['leverage']})\n"
                f"SL: ${t['stop_loss']:,.2f}\n"
                f"TP: ${t['take_profit']:,.2f}"
            )
        else:
            send_reply(chat_id, f"❌ Open failed: {data.get('error', 'unknown')}")
    except Exception as e:
        send_reply(chat_id, f"❌ Error opening {side} {coin}: {str(e)[:100]}")


def handle_close(chat_id, text):
    parts = text.strip().split()
    if len(parts) < 2:
        send_reply(chat_id, "Usage: /close <COIN>\nExample: /close BTC")
        return

    coin = parts[1].upper()

    if not os.path.exists(STATE_FILE):
        send_reply(chat_id, "No state file found.")
        return
    with open(STATE_FILE) as f:
        state = json.load(f)
    if coin not in state.get("positions", {}):
        open_coins = list(state.get("positions", {}).keys())
        send_reply(chat_id, f"❌ No open {coin} position.\nOpen: {', '.join(open_coins) or 'None'}")
        return

    try:
        r = requests.post(f"http://127.0.0.1:5555/api/close/{coin}", timeout=10)
        data = r.json()
        if data.get("ok"):
            t = data["trade"]
            pnl = t["pnl"]
            emoji = "✅" if pnl >= 0 else "❌"
            send_reply(chat_id,
                f"{emoji} CLOSED {t['side']} {coin}\n"
                f"Entry: ${t['entry']:,.2f} → Exit: ${t['exit']:,.2f}\n"
                f"P&L: ${pnl:+.2f} ({t['pnl_pct']:+.1f}%)\n"
                f"Held: {t['held_minutes']:.0f}m\n"
                f"Balance: ${t['balance']:,.2f}"
            )
        else:
            send_reply(chat_id, f"❌ Close failed: {data.get('error', 'unknown')}")
    except Exception as e:
        send_reply(chat_id, f"❌ Error closing {coin}: {str(e)[:100]}")


COMMANDS = {
    "/positions": handle_positions,
    "/balance": handle_balance,
    "/trades": handle_trades,
}


def load_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return int(f.read().strip())
    return None


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def main():
    print("Telegram command bot started.")
    print(f"Commands: {', '.join(COMMANDS.keys())}")
    offset = load_offset()

    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            save_offset(offset)

            msg = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "").strip().lower()

            if chat_id not in TELEGRAM_CHAT_IDS:
                continue

            cmd = text.split()[0] if text else ""
            if cmd == "/close":
                handle_close(chat_id, text)
            elif cmd == "/long":
                handle_open(chat_id, text, "LONG")
            elif cmd == "/short":
                handle_open(chat_id, text, "SHORT")
            elif cmd in COMMANDS:
                COMMANDS[cmd](chat_id)
            elif cmd.startswith("/"):
                send_reply(chat_id, f"Commands: {', '.join(COMMANDS.keys())}, /long <COIN>, /short <COIN>, /close <COIN>")

        if not updates:
            time.sleep(1)


if __name__ == "__main__":
    main()
