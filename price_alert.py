import yfinance as yf
import requests
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS

CT = ZoneInfo("America/Chicago")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

ALERTS = [
    {"ticker": "GOOGL", "target": 330.0, "direction": "above", "triggered": False},
]

CHECK_INTERVAL = 60


def send_telegram(text):
    for cid in TELEGRAM_CHAT_IDS:
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": cid,
                "text": text,
            }, timeout=10)
        except Exception:
            pass


def check_alerts():
    for alert in ALERTS:
        if alert["triggered"]:
            continue

        try:
            ticker = yf.Ticker(alert["ticker"])
            price = ticker.fast_info.get("lastPrice") or ticker.info.get("regularMarketPrice")
            if price is None:
                continue

            hit = False
            if alert["direction"] == "above" and price >= alert["target"]:
                hit = True
            elif alert["direction"] == "below" and price <= alert["target"]:
                hit = True

            if hit:
                alert["triggered"] = True
                now = datetime.now(CT).strftime("%I:%M %p CT")
                text = (
                    f"🚨 PRICE ALERT: {alert['ticker']}\n"
                    f"Price: ${price:,.2f} crossed {'above' if alert['direction'] == 'above' else 'below'} ${alert['target']:,.2f}\n"
                    f"Time: {now}"
                )
                send_telegram(text)
                print(f"[{now}] ALERT TRIGGERED: {alert['ticker']} @ ${price:,.2f}")
            else:
                now = datetime.now(CT).strftime("%H:%M:%S")
                print(f"[{now}] {alert['ticker']}: ${price:,.2f} (target: ${alert['target']:,.2f})")

        except Exception as e:
            print(f"Error checking {alert['ticker']}: {e}")


def main():
    print(f"Price Alert Monitor started — checking every {CHECK_INTERVAL}s")
    for a in ALERTS:
        print(f"  {a['ticker']} {'>' if a['direction'] == 'above' else '<'} ${a['target']:,.2f}")
    print()

    while True:
        all_triggered = all(a["triggered"] for a in ALERTS)
        if all_triggered:
            print("All alerts triggered. Exiting.")
            break

        check_alerts()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
