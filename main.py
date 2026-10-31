import time
import sys
import os
import fcntl
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from config import COINS, POLL_INTERVAL, CANDLE_INTERVAL, TREND_INTERVAL, SUPPORT_BOUNCE_COOLDOWN, TRADING_SESSIONS

CT = ZoneInfo("America/Chicago")
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(BOT_DIR, ".bot.lock")
from data_feed import get_candles, get_all_prices
from indicators import analyze
from signals import generate_signal
from paper_trader import PaperTrader
from invalidation import check_invalidation
from sheets_logger import queue_trade
from sheets_sync import sync_trades, sync_vetoes, sync_recommendations
from coinglass import get_market_context
from signal_notifier import notify_open, notify_close, notify_ai_decision, notify_ai_exit, notify_positions_update, is_configured
from ai_filter import check_trade, check_exit

CYAN = "\033[96m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

last_analysis: dict[str, dict] = {}
last_signal: dict[str, dict] = {}
last_cg: dict[str, dict] = {}
cooldown_until: dict[str, float] = {}
COOLDOWN_SECONDS = 1800  # 30 min cooldown after any exit
consecutive_stops: dict[str, dict] = {}  # coin -> {"side": str, "count": int}
MAX_CONSECUTIVE_STOPS = 2  # block re-entry after 2 stops in same direction
ai_exit_last_check: dict[str, float] = {}  # coin -> timestamp of last AI exit check
AI_EXIT_INTERVAL = 300  # check AI exit every 5 minutes
veto_cooldown: dict[str, float] = {}  # "COIN-SIDE" -> timestamp, skip AI call for 30min after veto
VETO_COOLDOWN_SECONDS = 1800
last_position_update: float = 0
POSITION_UPDATE_INTERVAL = 900  # send Telegram position update every 15 minutes
last_session: str | None = None  # track session transitions
session_trades: list[dict] = []  # trades during current session
session_vetoes: int = 0


def _track_consecutive_stop(coin, side, reason):
    if reason == "STOP_LOSS":
        prev = consecutive_stops.get(coin)
        if prev and prev["side"] == side:
            consecutive_stops[coin] = {"side": side, "count": prev["count"] + 1}
        else:
            consecutive_stops[coin] = {"side": side, "count": 1}
    else:
        consecutive_stops.pop(coin, None)


def _blocked_by_consecutive_stops(coin, side):
    prev = consecutive_stops.get(coin)
    if prev and prev["side"] == side and prev["count"] >= MAX_CONSECUTIVE_STOPS:
        return True
    return False


SWING_BLOCK_PCT = 0.015  # block entry if within 1.5% of swing support/resistance
SWING_MIN_TOUCHES = 2  # level must have been tested at least twice

def _blocked_by_swing_level(coin: str, side: str, price: float, analysis: dict) -> str | None:
    swing = analysis.get("swing_levels")
    if not swing:
        return None
    if side == "SHORT":
        for lvl in swing.get("support", []):
            if lvl["touches"] >= SWING_MIN_TOUCHES:
                dist = (price - lvl["price"]) / price
                if 0 <= dist <= SWING_BLOCK_PCT:
                    return f"within {dist*100:.1f}% of swing support ${lvl['price']:,.2f} ({lvl['touches']} touches)"
    elif side == "LONG":
        for lvl in swing.get("resistance", []):
            if lvl["touches"] >= SWING_MIN_TOUCHES:
                dist = (lvl["price"] - price) / price
                if 0 <= dist <= SWING_BLOCK_PCT:
                    return f"within {dist*100:.1f}% of swing resistance ${lvl['price']:,.2f} ({lvl['touches']} touches)"
    return None


LIQ_ZONE_BLOCK_PCT = 0.015  # block entry if within 1.5% of major liq zone

def _blocked_by_liq_zone(coin: str, side: str, price: float) -> str | None:
    try:
        from liq_estimator import estimate_liq_zones
        liq = estimate_liq_zones(coin)
        if not liq or not liq.get("top_clusters"):
            return None
        clusters = liq["top_clusters"]
        if side == "LONG":
            resistance = [c for c in clusters if c["type"] == "short" and c["price"] > price]
            for c in resistance:
                dist = (c["price"] - price) / price
                if dist <= LIQ_ZONE_BLOCK_PCT and c["strength"] >= 50:
                    return f"price within {dist*100:.1f}% of resistance liq @ ${c['price']:,.2f} (strength {c['strength']:.0f})"
        else:
            support = [c for c in clusters if c["type"] == "long" and c["price"] < price]
            for c in support:
                dist = (price - c["price"]) / price
                if dist <= LIQ_ZONE_BLOCK_PCT and c["strength"] >= 50:
                    return f"price within {dist*100:.1f}% of support liq @ ${c['price']:,.2f} (strength {c['strength']:.0f})"
    except Exception:
        pass
    return None


def _in_trading_session() -> str | None:
    hour = datetime.now(CT).hour
    for name, (start, end) in TRADING_SESSIONS.items():
        if start < end:
            if start <= hour < end:
                return name
        else:
            if hour >= start or hour < end:
                return name
    return None


def color_pnl(val):
    if val > 0:
        return f"{GREEN}+${val:.2f}{RESET}"
    elif val < 0:
        return f"{RED}-${abs(val):.2f}{RESET}"
    return f"${val:.2f}"


def color_pct(val):
    if val > 0:
        return f"{GREEN}+{val:.1f}%{RESET}"
    elif val < 0:
        return f"{RED}{val:.1f}%{RESET}"
    return f"{val:.1f}%"


def print_header():
    print(f"\n{BOLD}{CYAN}{'=' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  HYPERLIQUID PAPER TRADER  {DIM}BTC · ETH · SOL · HYPE{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}\n")


def print_scan(coin, price, analysis, signal, trend_analysis):
    trend_colors = {
        "STRONG_UP": GREEN, "UP": GREEN,
        "STRONG_DOWN": RED, "DOWN": RED,
        "SIDEWAYS": YELLOW,
    }
    tc = trend_colors.get(analysis["trend"], RESET)
    action_colors = {"LONG": GREEN, "SHORT": RED, "HOLD": DIM}
    ac = action_colors.get(signal["action"], RESET)

    print(f"  {BOLD}{coin:>4}{RESET}  ${price:>10,.2f}  "
          f"│ {tc}{analysis['trend']:>11}{RESET}  "
          f"│ RSI {analysis['rsi']:>5.1f}  "
          f"│ MACD {'+' if analysis['macd_histogram'] > 0 else ''}{analysis['macd_histogram']:.4f}  "
          f"│ {ac}{BOLD}{signal['action']}{RESET} "
          f"({signal['score']:+d}, {signal['confidence']:.0%})")

    if signal["reasons"]:
        print(f"         {DIM}{', '.join(signal['reasons'])}{RESET}")


def print_positions(trader, prices):
    if not trader.positions:
        return
    print(f"\n  {BOLD}OPEN POSITIONS{RESET}")
    print(f"  {'─' * 60}")
    for coin, pos in trader.positions.items():
        price = prices.get(coin, pos.entry_price)
        pnl = pos.pnl(price)
        pnl_pct = pos.pnl_pct(price)
        side_c = GREEN if pos.side == "LONG" else RED
        print(f"  {side_c}{pos.side:>5}{RESET} {coin:>4}  "
              f"entry ${pos.entry_price:,.2f} → ${price:,.2f}  "
              f"│ {color_pnl(pnl)} ({color_pct(pnl_pct)})  "
              f"│ SL ${pos.stop_loss:,.2f}  TP ${pos.take_profit:,.2f}")


def print_stats(stats):
    print(f"\n  {BOLD}ACCOUNT{RESET}")
    print(f"  {'─' * 60}")
    print(f"  Balance: {BOLD}${stats['balance']:,.2f}{RESET}  "
          f"│ P&L: {color_pnl(stats['realized_pnl'])}  "
          f"│ Unrealized: {color_pnl(stats['unrealized_pnl'])}")
    vault = stats.get('vault', 0)
    vault_str = f"  │ {BOLD}🔒 Vault: ${vault:,.2f}{RESET}" if vault > 0 else ""
    print(f"  Trades: {stats['total_trades']}  "
          f"│ W/L: {GREEN}{stats['wins']}{RESET}/{RED}{stats['losses']}{RESET}  "
          f"│ Win rate: {color_pct(stats['win_rate'])}  "
          f"│ Return: {color_pct(stats['total_return_pct'])}{vault_str}")


def _check_session_end(trader: PaperTrader):
    global last_session, session_trades, session_vetoes
    current = _in_trading_session()
    if last_session and not current:
        _send_session_summary(last_session, trader)
        session_trades = []
        session_vetoes = 0
    elif last_session and current and last_session != current:
        _send_session_summary(last_session, trader)
        session_trades = []
        session_vetoes = 0
    last_session = current


def _send_session_summary(session_name: str, trader: PaperTrader):
    if not session_trades and session_vetoes == 0:
        return

    closes = [t for t in session_trades if t.get("action") == "CLOSE"]
    opens = [t for t in session_trades if t.get("action") == "OPEN"]
    total_pnl = sum(t.get("pnl", 0) for t in closes)
    wins = len([t for t in closes if t.get("pnl", 0) > 0])
    losses = len(closes) - wins
    wr = (wins / len(closes) * 100) if closes else 0

    by_reason = {}
    for t in closes:
        r = t.get("reason", "?")
        if r not in by_reason:
            by_reason[r] = {"count": 0, "pnl": 0}
        by_reason[r]["count"] += 1
        by_reason[r]["pnl"] += t.get("pnl", 0)

    reason_lines = []
    for r, d in sorted(by_reason.items(), key=lambda x: x[1]["pnl"], reverse=True):
        reason_lines.append(f"  {r}: {d['count']} trades, ${d['pnl']:+.2f}")

    by_coin = {}
    for t in closes:
        c = t.get("coin", "?")
        if c not in by_coin:
            by_coin[c] = {"count": 0, "pnl": 0}
        by_coin[c]["count"] += 1
        by_coin[c]["pnl"] += t.get("pnl", 0)

    coin_lines = []
    for c, d in sorted(by_coin.items(), key=lambda x: x[1]["pnl"], reverse=True):
        coin_lines.append(f"  {c}: {d['count']} trades, ${d['pnl']:+.2f}")

    open_pos = []
    from data_feed import get_all_prices
    prices = get_all_prices()
    for coin, pos in trader.positions.items():
        p = prices.get(coin, pos.entry_price)
        pnl_pct = pos.pnl_pct(p)
        open_pos.append(f"  {pos.side} {coin} @ ${pos.entry_price:,.2f} ({pnl_pct:+.1f}%)")

    emoji = "📈" if total_pnl >= 0 else "📉"
    text = (
        f"{emoji} {session_name.upper()} SESSION SUMMARY\n\n"
        f"Trades: {len(opens)} opened, {len(closes)} closed\n"
        f"P&L: ${total_pnl:+.2f} ({wins}W/{losses}L, {wr:.0f}%)\n"
        f"Vetoes: {session_vetoes}\n"
        f"Balance: ${trader.balance:,.2f}\n\n"
        f"By exit type:\n" + ("\n".join(reason_lines) if reason_lines else "  None") + "\n\n"
        f"By coin:\n" + ("\n".join(coin_lines) if coin_lines else "  None") + "\n\n"
        f"Open positions:\n" + ("\n".join(open_pos) if open_pos else "  None")
    )

    from signal_notifier import _send_all
    _send_all(text)
    print(f"\n  {BOLD}{CYAN}{'═'*50}{RESET}")
    print(f"  {BOLD}{session_name.upper()} SESSION ENDED{RESET}")
    print(f"  {text}")
    print(f"  {BOLD}{CYAN}{'═'*50}{RESET}")


def _sync_external_changes(trader: PaperTrader):
    from paper_trader import _locked_read, STATE_FILE, Position
    disk = _locked_read(STATE_FILE)
    if not disk:
        return
    disk_positions = set(disk.get("positions", {}).keys())
    mem_positions = set(trader.positions.keys())
    removed = mem_positions - disk_positions
    added = disk_positions - mem_positions
    for coin in removed:
        del trader.positions[coin]
        cooldown_until[coin] = time.time() + COOLDOWN_SECONDS
        print(f"  {DIM}[sync] {coin} closed externally — 30min cooldown{RESET}")
    for coin in added:
        pos = Position.from_dict(disk["positions"][coin])
        trader.positions[coin] = pos
        print(f"  {CYAN}[sync] {coin} {pos.side} opened externally @ ${pos.entry_price:,.2f}{RESET}")
    # Always sync stats from disk to prevent double-counting
    trader.balance = disk.get("balance", trader.balance)
    trader.total_pnl = disk.get("total_pnl", trader.total_pnl)
    trader.total_trades = disk.get("total_trades", trader.total_trades)
    trader.wins = disk.get("wins", trader.wins)
    trader.losses = disk.get("losses", trader.losses)


def run_cycle(trader: PaperTrader):
    global session_vetoes, session_trades
    prices = get_all_prices()
    _sync_external_changes(trader)
    ts = datetime.now(CT).strftime("%I:%M:%S %p")
    session = _in_trading_session()
    session_tag = f" | {session.upper()}" if session else f" | {YELLOW}OFF-SESSION{RESET}"
    print(f"\n{DIM}[{ts}{session_tag}] scanning...{RESET}")

    closed = trader.check_positions(prices)
    for c in closed:
        emoji = "✓" if c["pnl"] > 0 else "✗"
        print(f"\n  {MAGENTA}{BOLD}>>> CLOSED {c['coin']} {c['side']} — {c['reason']}{RESET}")
        print(f"      {emoji} ${c['entry']:,.2f} → ${c['exit']:,.2f}  "
              f"│ {color_pnl(c['pnl'])} ({color_pct(c['pnl_pct'])})  "
              f"│ held {c['held_minutes']:.0f}m")
        coin = c["coin"]
        _track_consecutive_stop(coin, c["side"], c["reason"])
        cooldown_until[coin] = time.time() + COOLDOWN_SECONDS
        session_trades.append(c)
        reasons = last_signal.get(coin, {}).get("reasons", [])
        a = last_analysis.get(coin)
        queue_trade(c, coin, reasons, a)
        notify_close(coin, c["side"], c["entry"], c["exit"], c["pnl"], c["pnl_pct"], c["reason"], c["held_minutes"])

    for coin in COINS:
        price = prices.get(coin)
        if not price:
            continue
        candles_15m = get_candles(coin, CANDLE_INTERVAL, 200)
        candles_1h = get_candles(coin, TREND_INTERVAL, 100)
        if not candles_15m or not candles_1h:
            continue
        analysis = analyze(candles_15m)
        trend_analysis = analyze(candles_1h)

        try:
            cg_ctx = get_market_context(coin)
            last_cg[coin] = cg_ctx
        except Exception:
            cg_ctx = last_cg.get(coin)

        signal = generate_signal(analysis, trend_analysis, cg_ctx, candles_15m)

        last_analysis[coin] = analysis
        last_signal[coin] = signal

        print_scan(coin, price, analysis, signal, trend_analysis)
        if cg_ctx and cg_ctx.get("reasons"):
            print(f"         {DIM}CG: {cg_ctx['bias'].upper()} ({cg_ctx['score']:+d}) — {', '.join(cg_ctx['reasons'][:3])}{RESET}")

        if coin in trader.positions:
            pos = trader.positions[coin]
            inv = check_invalidation(pos.side, candles_15m, pos.entry_price, pos.opened_at, trend_analysis["trend"])
            if inv:
                pnl = pos.pnl(price)
                pnl_pct = pos.pnl_pct(price)
                trader.balance += pnl
                trader.total_pnl += pnl
                trader.total_trades += 1
                if pnl > 0:
                    trader.wins += 1
                else:
                    trader.losses += 1
                result = {
                    "action": "CLOSE", "coin": coin, "side": pos.side,
                    "reason": inv["reason"],
                    "entry": pos.entry_price, "exit": price,
                    "pnl": round(pnl, 2), "pnl_pct": pnl_pct,
                    "balance": round(trader.balance, 2),
                    "held_minutes": round((time.time() - pos.opened_at) / 60, 1),
                    "entry_score": pos.entry_score,
                    "entry_confidence": pos.entry_confidence,
                }
                trader._log_trade(result)
                del trader.positions[coin]
                trader._save_state()
                cd = SUPPORT_BOUNCE_COOLDOWN if inv["reason"] == "SUPPORT_BOUNCE" else COOLDOWN_SECONDS
                cooldown_until[coin] = time.time() + cd
                _track_consecutive_stop(coin, pos.side, inv["reason"])
                print(f"\n  {MAGENTA}{BOLD}>>> INVALIDATED {coin} {pos.side} — {inv['reason']}{RESET}")
                print(f"      {inv['detail']}")
                print(f"      {color_pnl(pnl)} ({color_pct(pnl_pct)})  "
                      f"│ held {result['held_minutes']:.0f}m  │ cooldown 10m")
                session_trades.append(result)
                reasons = last_signal.get(coin, {}).get("reasons", [])
                queue_trade(result, coin, reasons, analysis)
                notify_close(coin, pos.side, pos.entry_price, price, pnl, pnl_pct, inv["reason"], result["held_minutes"])
            elif coin in trader.positions:
                now_ts = time.time()
                held_min = (now_ts - pos.opened_at) / 60
                last_ai = ai_exit_last_check.get(coin, 0)
                if held_min >= 15 and now_ts - last_ai >= AI_EXIT_INTERVAL:
                    ai_exit_last_check[coin] = now_ts
                    ai_ex = check_exit(coin, pos, price, analysis, trend_analysis, cg_ctx, trader.positions)
                    if ai_ex["decision"] == "EXIT":
                        pnl = pos.pnl(price)
                        pnl_pct = pos.pnl_pct(price)
                        trader.balance += pnl
                        trader.total_pnl += pnl
                        trader.total_trades += 1
                        if pnl > 0:
                            trader.wins += 1
                        else:
                            trader.losses += 1
                        result = {
                            "action": "CLOSE", "coin": coin, "side": pos.side,
                            "reason": "AI_EXIT",
                            "entry": pos.entry_price, "exit": price,
                            "pnl": round(pnl, 2), "pnl_pct": pnl_pct,
                            "balance": round(trader.balance, 2),
                            "held_minutes": round((now_ts - pos.opened_at) / 60, 1),
                            "entry_score": pos.entry_score,
                            "entry_confidence": pos.entry_confidence,
                        }
                        trader._log_trade(result)
                        del trader.positions[coin]
                        trader._save_state()
                        cooldown_until[coin] = now_ts + COOLDOWN_SECONDS
                        print(f"\n  {MAGENTA}{BOLD}>>> AI EXIT {coin} {pos.side}{RESET}")
                        print(f"      {DIM}🤖 {ai_ex['reason']}{RESET}")
                        print(f"      {color_pnl(pnl)} ({color_pct(pnl_pct)})  "
                              f"│ held {result['held_minutes']:.0f}m")
                        session_trades.append(result)
                        reasons = last_signal.get(coin, {}).get("reasons", [])
                        queue_trade(result, coin, reasons, analysis)
                        notify_close(coin, pos.side, pos.entry_price, price, pnl, pnl_pct, "AI_EXIT", result["held_minutes"])
                        notify_ai_exit(coin, pos.side, pos.entry_price, price, pnl, pnl_pct, ai_ex["reason"])
                    else:
                        print(f"         {DIM}🤖 {coin}: {ai_ex['reason']}{RESET}")

        on_cooldown = coin in cooldown_until and time.time() < cooldown_until[coin]

        stopped_out = _blocked_by_consecutive_stops(coin, signal["action"])
        session = _in_trading_session()

        if not session and signal["action"] in ("LONG", "SHORT") and coin not in trader.positions:
            pass  # silent — outside trading sessions, no new entries

        elif signal["action"] in ("LONG", "SHORT") and coin not in trader.positions and not on_cooldown and not stopped_out and session:
            side = signal["action"]
            rsi_val = analysis.get("rsi", 50)
            if side == "LONG" and rsi_val > 70:
                print(f"         {YELLOW}📊 {coin} LONG blocked — RSI {rsi_val:.1f} overbought{RESET}")
                continue
            if side == "SHORT" and rsi_val < 30:
                print(f"         {YELLOW}📊 {coin} SHORT blocked — RSI {rsi_val:.1f} oversold{RESET}")
                continue
            liq_blocked = _blocked_by_liq_zone(coin, side, price)
            if liq_blocked:
                print(f"         {YELLOW}🧱 {coin} {side} blocked — {liq_blocked}{RESET}")
                continue
            swing_blocked = _blocked_by_swing_level(coin, side, price, analysis)
            if swing_blocked:
                print(f"         {YELLOW}📊 {coin} {side} blocked — {swing_blocked}{RESET}")
                continue
            veto_key = f"{coin}-{side}"
            if veto_key in veto_cooldown and time.time() < veto_cooldown[veto_key]:
                remain = int(veto_cooldown[veto_key] - time.time())
                print(f"         {DIM}🚫 {coin} {side} veto cooldown ({remain}s){RESET}")
                continue
            ai = check_trade(coin, side, price, signal, analysis, trend_analysis, cg_ctx, trader.positions)
            notify_ai_decision(coin, side, price, signal, ai["decision"], ai["reason"])

            open_side = None
            if ai["decision"] == "CONFIRM":
                open_side = side
            elif ai["decision"] == "REVERSE":
                open_side = "SHORT" if side == "LONG" else "LONG"
                rev_stopped = _blocked_by_consecutive_stops(coin, open_side)
                if rev_stopped:
                    print(f"         {YELLOW}🤖 AI REVERSE {coin} → {open_side} blocked by consecutive stops{RESET}")
                    open_side = None
                else:
                    print(f"         {CYAN}🤖 AI REVERSE {coin}: {side} → {open_side} — {ai['reason']}{RESET}")
            else:
                session_vetoes += 1
                veto_cooldown[veto_key] = time.time() + VETO_COOLDOWN_SECONDS
                print(f"         {YELLOW}🤖 AI VETO {coin} {side}: {ai['reason']} (30min cooldown){RESET}")

            if open_side:
                result = trader.open_position(coin, open_side, price, signal)
                if result:
                    side_color = GREEN if open_side == "LONG" else RED
                    print(f"\n  {side_color}{BOLD}>>> OPENED {open_side} {coin}{RESET} "
                          f"@ ${price:,.2f} | size ${result['size_usd']} | {result['leverage']} | conf {signal['confidence']:.0%}")
                    print(f"         {DIM}🤖 AI: {ai['reason']}{RESET}")
                    session_trades.append(result)
                    queue_trade(result, coin, signal["reasons"], analysis)
                    notify_open(coin, open_side, price, signal, result['leverage'])

        elif stopped_out and signal["action"] in ("LONG", "SHORT") and coin not in trader.positions:
            prev = consecutive_stops[coin]
            print(f"         {DIM}🛑 {coin} blocked — {prev['count']}x consecutive {prev['side']} stops{RESET}")

        elif on_cooldown and signal["action"] in ("LONG", "SHORT") and coin not in trader.positions:
            remain = int(cooldown_until[coin] - time.time())
            print(f"         {DIM}⏸ {coin} on cooldown ({remain}s remaining){RESET}")

    print_positions(trader, prices)
    print_stats(trader.get_stats(prices))

    global last_position_update
    if trader.positions and time.time() - last_position_update >= POSITION_UPDATE_INTERVAL:
        last_position_update = time.time()
        pos_data = []
        for coin, pos in trader.positions.items():
            p = prices.get(coin, pos.entry_price)
            pos_data.append({
                "coin": coin, "side": pos.side, "entry": pos.entry_price,
                "price": p, "pnl": round(pos.pnl(p), 2), "pnl_pct": pos.pnl_pct(p),
            })
        notify_positions_update(pos_data, trader.balance, trader.total_pnl)

    vault_deposit = trader.check_vault()
    if vault_deposit > 0:
        print(f"  {GREEN}{BOLD}🔒 VAULT DEPOSIT: +${vault_deposit:.2f} secured{RESET}  "
              f"│ Total vault: ${trader.vault:.2f}")

    try:
        synced = sync_trades()
        if synced > 0:
            print(f"  {DIM}↑ synced {synced} trade(s) to Google Sheets{RESET}")
        vsynced = sync_vetoes()
        if vsynced and vsynced > 0:
            print(f"  {DIM}↑ synced {vsynced} veto(es) to Google Sheets{RESET}")
        rsynced = sync_recommendations()
        if rsynced and rsynced > 0:
            print(f"  {DIM}↑ synced {rsynced} rec(s) to Google Sheets{RESET}")
    except Exception as e:
        print(f"  {DIM}sheets sync error: {e}{RESET}")

    _check_session_end(trader)


def main():
    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"{RED}Another bot instance is already running. Exiting.{RESET}")
        sys.exit(1)
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()

    from dashboard import app as dash_app
    dash_thread = threading.Thread(
        target=lambda: dash_app.run(host="0.0.0.0", port=5555, debug=False, use_reloader=False),
        daemon=True,
    )
    dash_thread.start()
    print(f"  {CYAN}Dashboard running at http://localhost:5555{RESET}\n")

    print_header()
    trader = PaperTrader()
    print(f"  {DIM}Starting balance: ${trader.balance:,.2f}{RESET}")
    print(f"  {DIM}Polling every {POLL_INTERVAL}s | Coins: {', '.join(COINS)}{RESET}")
    print(f"  {DIM}Press Ctrl+C to stop{RESET}")

    while True:
        try:
            run_cycle(trader)
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print(f"\n\n{YELLOW}Stopped.{RESET}")
            stats = trader.get_stats(get_all_prices())
            print_stats(stats)
            fcntl.flock(lock_fp, fcntl.LOCK_UN)
            lock_fp.close()
            sys.exit(0)
        except Exception as e:
            print(f"\n  {RED}Error: {e}{RESET}")
            time.sleep(10)


if __name__ == "__main__":
    main()
