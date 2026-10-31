import json
import os
import time

SHEETS_LOG = os.path.join(os.path.dirname(__file__), "sheets_queue.json")
SPREADSHEET_ID = "1obHX1ahDXreutw8gmlog_keI1Io4aSrK2ZKNCsF16q8"
COINS = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "NEAR"]

HEADERS = [
    "Timestamp", "Action", "Side", "Entry Price", "Exit Price",
    "Size ($)", "Leverage", "P&L ($)", "P&L (%)", "Exit Reason",
    "Held (min)", "Balance After", "Score", "Confidence",
    "Thesis", "Trend 15m", "RSI", "MACD Hist", "Support",
    "Resistance", "Vol Spike", "Breakout",
]


def setup_sheets():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("gspread/google-auth not installed")
        return False

    creds_file = os.path.join(os.path.dirname(__file__), "google_creds.json")
    if not os.path.exists(creds_file):
        print(f"No credentials at {creds_file}")
        print("Download service account JSON from Google Cloud Console")
        print("Save as google_creds.json in the bot directory")
        return False

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    existing = [ws.title for ws in sh.worksheets()]

    for coin in COINS:
        if coin not in existing:
            sh.add_worksheet(title=coin, rows=1000, cols=20)
        ws = sh.worksheet(coin)
        ws.update(values=[HEADERS], range_name="A1:V1")

        # Format header row
        ws.format("A1:V1", {
            "backgroundColor": {"red": 0.1, "green": 0.1, "blue": 0.15},
            "textFormat": {"bold": True, "fontSize": 9,
                           "foregroundColor": {"red": 0.7, "green": 0.7, "blue": 0.7}},
            "horizontalAlignment": "CENTER",
        })

        # Column widths
        ws.columns_auto_resize(0, 22)

    # Add Summary tab
    if "Summary" not in existing:
        sh.add_worksheet(title="Summary", rows=100, cols=10)
    summary = sh.worksheet("Summary")
    summary.update(values=[["Coin", "Total Trades", "Wins", "Losses", "Win Rate"]], range_name="A1:E1")
    summary.format("A1:E1", {
        "backgroundColor": {"red": 0.1, "green": 0.1, "blue": 0.15},
        "textFormat": {"bold": True, "fontSize": 9,
                       "foregroundColor": {"red": 0.7, "green": 0.7, "blue": 0.7}},
    })

    # Add AI Vetoes tab
    veto_headers = [
        "Timestamp", "Coin", "Side", "Price at Veto", "Decision",
        "Reason", "Confidence", "Score", "Signals",
        "Trend 15m", "RSI", "Trend 1h", "Price 30m Later", "Missed P&L"
    ]
    if "AI Vetoes" not in existing:
        sh.add_worksheet(title="AI Vetoes", rows=1000, cols=14)
    veto_ws = sh.worksheet("AI Vetoes")
    veto_ws.update(values=[veto_headers], range_name="A1:N1")
    veto_ws.format("A1:N1", {
        "backgroundColor": {"red": 0.15, "green": 0.1, "blue": 0.1},
        "textFormat": {"bold": True, "fontSize": 9,
                       "foregroundColor": {"red": 0.9, "green": 0.6, "blue": 0.6}},
        "horizontalAlignment": "CENTER",
    })

    # Add Recommendations tab
    rec_headers = [
        "Timestamp", "Type", "Coin", "Side", "Entry Price",
        "Reason", "Confidence", "Status", "Outcome",
    ]
    if "Recommendations" not in existing:
        sh.add_worksheet(title="Recommendations", rows=1000, cols=9)
    rec_ws = sh.worksheet("Recommendations")
    rec_ws.update(values=[rec_headers], range_name="A1:I1")
    rec_ws.format("A1:I1", {
        "backgroundColor": {"red": 0.05, "green": 0.12, "blue": 0.1},
        "textFormat": {"bold": True, "fontSize": 9,
                       "foregroundColor": {"red": 0.4, "green": 0.9, "blue": 0.6}},
        "horizontalAlignment": "CENTER",
    })

    # Remove default Sheet1
    if "Sheet1" in existing:
        default = sh.worksheet("Sheet1")
        sh.del_worksheet(default)

    print("Sheets setup complete")
    return True


def sync_trades():
    if not os.path.exists(SHEETS_LOG):
        return 0

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return 0

    creds_file = os.path.join(os.path.dirname(__file__), "google_creds.json")
    if not os.path.exists(creds_file):
        return 0

    with open(SHEETS_LOG) as f:
        queue = json.load(f)

    if not queue:
        return 0

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    synced = 0
    for entry in queue:
        coin = entry.get("coin", "BTC")
        if coin not in COINS:
            continue
        try:
            ws = sh.worksheet(coin)
        except gspread.exceptions.WorksheetNotFound:
            continue

        row = [
            entry.get("timestamp", ""),
            entry.get("action", ""),
            entry.get("side", ""),
            entry.get("entry_price", 0),
            entry.get("exit_price", 0),
            entry.get("size_usd", 0),
            entry.get("leverage", ""),
            entry.get("pnl", 0),
            entry.get("pnl_pct", 0),
            entry.get("exit_reason", ""),
            entry.get("held_minutes", 0),
            entry.get("balance_after", 0),
            entry.get("score", 0),
            f"{entry.get('confidence', 0):.0%}" if entry.get("confidence") else "0%",
            entry.get("thesis", ""),
            entry.get("trend_15m", ""),
            entry.get("rsi", 0),
            entry.get("macd_hist", 0),
            entry.get("support", 0),
            entry.get("resistance", 0),
            str(entry.get("volume_spike", False)),
            entry.get("breakout", ""),
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")

        # Color the P&L cell green/red
        all_values = ws.get_all_values()
        row_num = len(all_values)
        pnl = entry.get("pnl", 0)
        if entry.get("action") == "CLOSE":
            if pnl > 0:
                ws.format(f"H{row_num}:I{row_num}", {
                    "textFormat": {"bold": True,
                                   "foregroundColor": {"red": 0, "green": 0.8, "blue": 0.4}},
                })
            elif pnl < 0:
                ws.format(f"H{row_num}:I{row_num}", {
                    "textFormat": {"bold": True,
                                   "foregroundColor": {"red": 0.9, "green": 0.2, "blue": 0.2}},
                })

        synced += 1
        time.sleep(1.1)  # rate limit

    # Clear queue after sync
    with open(SHEETS_LOG, "w") as f:
        json.dump([], f)

    print(f"Synced {synced} trades to Google Sheets")
    return synced


VETO_LOG = os.path.join(os.path.dirname(__file__), "veto_log.json")


def sync_vetoes():
    if not os.path.exists(VETO_LOG):
        return 0

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return 0

    creds_file = os.path.join(os.path.dirname(__file__), "google_creds.json")
    if not os.path.exists(creds_file):
        return 0

    with open(VETO_LOG) as f:
        vetoes = json.load(f)

    if not vetoes:
        return 0

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet("AI Vetoes")
    except gspread.exceptions.WorksheetNotFound:
        setup_sheets()
        ws = sh.worksheet("AI Vetoes")

    synced = 0
    for v in vetoes:
        row = [
            v.get("timestamp", ""),
            v.get("coin", ""),
            v.get("side", ""),
            v.get("price", 0),
            v.get("decision", ""),
            v.get("reason", ""),
            f"{v.get('confidence', 0):.0%}" if v.get("confidence") else "0%",
            v.get("score", 0),
            ", ".join(v.get("reasons", [])),
            v.get("trend_15m", ""),
            v.get("rsi", 0),
            v.get("trend_1h", ""),
            "",  # Price 30m Later — fill manually or via follow-up script
            "",  # Missed P&L — fill manually
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")

        all_values = ws.get_all_values()
        row_num = len(all_values)
        if v.get("decision") == "VETO":
            ws.format(f"E{row_num}", {
                "textFormat": {"bold": True,
                               "foregroundColor": {"red": 0.9, "green": 0.3, "blue": 0.3}},
            })
        elif v.get("decision") == "REVERSE":
            ws.format(f"E{row_num}", {
                "textFormat": {"bold": True,
                               "foregroundColor": {"red": 0.3, "green": 0.6, "blue": 0.9}},
            })

        synced += 1
        time.sleep(1.1)

    with open(VETO_LOG, "w") as f:
        json.dump([], f)

    print(f"Synced {synced} vetoes to Google Sheets")
    return synced


REC_LOG = os.path.join(os.path.dirname(__file__), "recommendations.json")


def log_recommendation(rec_type: str, coin: str, side: str, price: float,
                       reason: str, confidence: float = 0):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    entry = {
        "timestamp": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        "type": rec_type,
        "coin": coin,
        "side": side,
        "price": price,
        "reason": reason,
        "confidence": confidence,
        "status": "PENDING",
        "outcome": "",
    }
    existing = []
    if os.path.exists(REC_LOG):
        with open(REC_LOG) as f:
            existing = json.load(f)
    existing.append(entry)
    with open(REC_LOG, "w") as f:
        json.dump(existing, f, indent=2)
    return entry


def sync_recommendations():
    if not os.path.exists(REC_LOG):
        return 0

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return 0

    creds_file = os.path.join(os.path.dirname(__file__), "google_creds.json")
    if not os.path.exists(creds_file):
        return 0

    with open(REC_LOG) as f:
        recs = json.load(f)

    if not recs:
        return 0

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet("Recommendations")
    except gspread.exceptions.WorksheetNotFound:
        setup_sheets()
        ws = sh.worksheet("Recommendations")

    synced = 0
    for r in recs:
        row = [
            r.get("timestamp", ""),
            r.get("type", ""),
            r.get("coin", ""),
            r.get("side", ""),
            r.get("price", 0),
            r.get("reason", ""),
            f"{r.get('confidence', 0):.0%}" if r.get("confidence") else "",
            r.get("status", "PENDING"),
            r.get("outcome", ""),
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        synced += 1
        time.sleep(1.1)

    with open(REC_LOG, "w") as f:
        json.dump([], f)

    return synced


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_sheets()
    elif len(sys.argv) > 1 and sys.argv[1] == "vetoes":
        synced = sync_vetoes()
        if synced == 0:
            print("No vetoes to sync")
    else:
        synced = sync_trades()
        if synced == 0:
            print("No trades to sync")
        vsynced = sync_vetoes()
        if vsynced == 0:
            print("No vetoes to sync")
