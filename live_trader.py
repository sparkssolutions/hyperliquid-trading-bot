"""
LiveTrader — same interface as PaperTrader but executes real orders on Hyperliquid.
Swap one import in main.py to go live.

Requires live_config.json in bot directory:
{"private_key": "0x...", "use_mainnet": false}
"""
import json
import time
import os
import fcntl
from datetime import datetime
from zoneinfo import ZoneInfo
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from config import (
    POSITION_SIZE_PCT, MAX_POSITIONS, LEVERAGE,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, TRAILING_STOP_PCT, TRAILING_ACTIVATE_PCT,
)

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BOT_DIR, "live_state.json")
TRADE_LOG = os.path.join(BOT_DIR, "live_trade_log.json")
CONFIG_FILE = os.path.join(BOT_DIR, "live_config.json")

SZ_DECIMALS = {
    "BTC": 4,
    "ETH": 3,
    "SOL": 1,
    "HYPE": 0,
    "ZEC": 2,
    "NEAR": 0,
}


def _locked_write(filepath, data):
    lock_path = filepath + ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _locked_read(filepath):
    lock_path = filepath + ".lock"
    if not os.path.exists(filepath):
        return None
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_SH)
        try:
            with open(filepath) as f:
                return json.load(f)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"Missing {CONFIG_FILE}. Create it with:\n"
            '{"private_key": "0x...", "use_mainnet": false}\n'
            "Set use_mainnet to true for real trading."
        )
    with open(CONFIG_FILE) as f:
        return json.load(f)


class Position:
    def __init__(self, coin, side, entry_price, size_usd, leverage, liq_tp=None):
        self.coin = coin
        self.side = side
        self.entry_price = entry_price
        self.size_usd = size_usd
        self.leverage = leverage
        self.quantity = (size_usd * leverage) / entry_price
        self.stop_loss = self._calc_stop(entry_price)
        self.take_profit = liq_tp if liq_tp else self._calc_tp(entry_price)
        self.trailing_stop = None
        self.highest_pnl_pct = 0
        self.opened_at = time.time()
        self.entry_score = 0
        self.entry_confidence = 0
        self.entry_reasons = []

    def _calc_stop(self, price):
        if self.side == "LONG":
            return round(price * (1 - STOP_LOSS_PCT), 6)
        return round(price * (1 + STOP_LOSS_PCT), 6)

    def _calc_tp(self, price):
        if self.side == "LONG":
            return round(price * (1 + TAKE_PROFIT_PCT), 6)
        return round(price * (1 - TAKE_PROFIT_PCT), 6)

    def pnl(self, current_price):
        if self.side == "LONG":
            return self.quantity * (current_price - self.entry_price)
        return self.quantity * (self.entry_price - current_price)

    def pnl_pct(self, current_price):
        raw = self.pnl(current_price) / self.size_usd
        return round(raw * 100, 2)

    def _get_trail_params(self):
        from config import TIGHT_MODE_EXPIRES
        if time.time() < TIGHT_MODE_EXPIRES:
            return TRAILING_ACTIVATE_PCT, TRAILING_STOP_PCT
        return 2.0, 0.012

    def check_exit(self, current_price) -> str | None:
        pnl_pct = self.pnl_pct(current_price)
        self.highest_pnl_pct = max(self.highest_pnl_pct, pnl_pct)
        activate_pct, trail_pct = self._get_trail_params()

        held_min = (time.time() - self.opened_at) / 60
        if pnl_pct > 0 and held_min > 0:
            velocity = pnl_pct / held_min
            if pnl_pct >= 2.0 and velocity >= 0.3:
                return "RAPID_PROFIT"
            if pnl_pct >= 1.5 and velocity >= 0.5:
                return "RAPID_PROFIT"

        if self.side == "LONG":
            if current_price <= self.stop_loss:
                return "STOP_LOSS"
            if current_price >= self.take_profit:
                return "TAKE_PROFIT"
            if pnl_pct > activate_pct:
                trail_price = current_price * (1 - trail_pct)
                if self.trailing_stop is None or trail_price > self.trailing_stop:
                    self.trailing_stop = trail_price
                if current_price <= self.trailing_stop:
                    return "TRAILING_STOP"
        else:
            if current_price >= self.stop_loss:
                return "STOP_LOSS"
            if current_price <= self.take_profit:
                return "TAKE_PROFIT"
            if pnl_pct > activate_pct:
                trail_price = current_price * (1 + trail_pct)
                if self.trailing_stop is None or trail_price < self.trailing_stop:
                    self.trailing_stop = trail_price
                if current_price >= self.trailing_stop:
                    return "TRAILING_STOP"
        return None

    def to_dict(self):
        return {
            "coin": self.coin, "side": self.side,
            "entry_price": self.entry_price, "size_usd": self.size_usd,
            "leverage": self.leverage, "quantity": self.quantity,
            "stop_loss": self.stop_loss, "take_profit": self.take_profit,
            "trailing_stop": self.trailing_stop,
            "highest_pnl_pct": self.highest_pnl_pct,
            "opened_at": self.opened_at,
            "entry_score": self.entry_score,
            "entry_confidence": self.entry_confidence,
            "entry_reasons": self.entry_reasons,
        }

    @classmethod
    def from_dict(cls, d):
        p = cls(d["coin"], d["side"], d["entry_price"], d["size_usd"], d["leverage"])
        p.quantity = d["quantity"]
        p.stop_loss = d["stop_loss"]
        p.take_profit = d["take_profit"]
        p.trailing_stop = d.get("trailing_stop")
        p.highest_pnl_pct = d.get("highest_pnl_pct", 0)
        p.opened_at = d.get("opened_at", time.time())
        p.entry_score = d.get("entry_score", 0)
        p.entry_confidence = d.get("entry_confidence", 0)
        p.entry_reasons = d.get("entry_reasons", [])
        return p


class LiveTrader:
    def __init__(self):
        config = _load_config()
        self.private_key = config["private_key"]
        self.use_mainnet = config.get("use_mainnet", False)

        account = Account.from_key(self.private_key)
        self.address = account.address

        base_url = constants.MAINNET_API_URL if self.use_mainnet else constants.TESTNET_API_URL
        self.info = Info(base_url, skip_ws=True)
        self.exchange = Exchange(account, base_url)

        self.positions: dict[str, Position] = {}
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0
        self.vault = 0.0
        self._cached_balance = 0
        self.trade_history: list[dict] = []
        self._load_state()
        self._sync_from_exchange()

        mode = "MAINNET" if self.use_mainnet else "TESTNET"
        print(f"  LiveTrader initialized ({mode})")
        print(f"  Address: {self.address}")

    @property
    def balance(self):
        try:
            user_state = self.info.user_state(self.address)
            val = float(user_state.get("marginSummary", {}).get("accountValue", 0))
            self._cached_balance = val
            return val
        except Exception:
            return self._cached_balance

    @balance.setter
    def balance(self, val):
        self._cached_balance = val

    def _sync_from_exchange(self):
        try:
            user_state = self.info.user_state(self.address)
            self._cached_balance = float(
                user_state.get("marginSummary", {}).get("accountValue", 0)
            )
            exchange_positions = user_state.get("assetPositions", [])
            for ep in exchange_positions:
                pos_data = ep.get("position", {})
                coin = pos_data.get("coin", "")
                szi = float(pos_data.get("szi", 0))
                if szi == 0:
                    continue
                if coin not in self.positions:
                    side = "LONG" if szi > 0 else "SHORT"
                    entry = float(pos_data.get("entryPx", 0))
                    size_usd = abs(szi) * entry / LEVERAGE
                    pos = Position(coin, side, entry, size_usd, LEVERAGE)
                    pos.quantity = abs(szi)
                    self.positions[coin] = pos
                    print(f"  [sync] Found existing {side} {coin} @ ${entry:,.2f}")
        except Exception as e:
            print(f"  [sync] Exchange sync error: {e}")

    def _get_liq_tp(self, coin: str, side: str, price: float) -> float | None:
        try:
            from liq_estimator import estimate_liq_zones
            result = estimate_liq_zones(coin)
            if not result or not result.get("top_clusters"):
                return None
            clusters = result["top_clusters"]
            if side == "LONG":
                targets = [c for c in clusters if c["type"] == "short" and c["price"] > price]
                if targets:
                    nearest = min(targets, key=lambda c: c["price"])
                    dist_pct = (nearest["price"] - price) / price
                    if 0.01 <= dist_pct <= 0.08:
                        return round(nearest["price"], 6)
            else:
                targets = [c for c in clusters if c["type"] == "long" and c["price"] < price]
                if targets:
                    nearest = max(targets, key=lambda c: c["price"])
                    dist_pct = (price - nearest["price"]) / price
                    if 0.01 <= dist_pct <= 0.08:
                        return round(nearest["price"], 6)
        except Exception:
            pass
        return None

    def _round_size(self, coin: str, size: float) -> float:
        decimals = SZ_DECIMALS.get(coin, 2)
        return round(size, decimals)

    def open_position(self, coin: str, side: str, price: float,
                      signal: dict | None = None) -> dict | None:
        if coin in self.positions:
            return None
        if len(self.positions) >= MAX_POSITIONS:
            return None

        account_value = self.balance
        size_usd = account_value * POSITION_SIZE_PCT
        if size_usd < 10:
            return None

        liq_tp = self._get_liq_tp(coin, side, price)
        is_buy = side == "LONG"
        sz = self._round_size(coin, (size_usd * LEVERAGE) / price)

        if sz <= 0:
            return None

        try:
            self.exchange.update_leverage(LEVERAGE, coin, is_cross=True)

            order_result = self.exchange.market_open(
                coin, is_buy, sz, None, None
            )

            if order_result.get("status") == "ok":
                statuses = order_result.get("response", {}).get("data", {}).get("statuses", [])
                fill_price = price
                for s in statuses:
                    if "filled" in s:
                        fill_price = float(s["filled"]["avgPx"])
                        sz = float(s["filled"]["totalSz"])
                        break

                pos = Position(coin, side, fill_price, size_usd, LEVERAGE, liq_tp=liq_tp)
                pos.quantity = sz
                if signal:
                    pos.entry_score = signal.get("score", 0)
                    pos.entry_confidence = signal.get("confidence", 0)
                    pos.entry_reasons = signal.get("reasons", [])
                self.positions[coin] = pos
                self._save_state()

                result = {
                    "action": "OPEN", "coin": coin, "side": side,
                    "entry": fill_price, "size_usd": round(size_usd, 2),
                    "quantity": round(sz, 6),
                    "stop_loss": pos.stop_loss, "take_profit": pos.take_profit,
                    "leverage": f"{LEVERAGE}x",
                    "score": signal.get("score", 0) if signal else 0,
                    "confidence": signal.get("confidence", 0) if signal else 0,
                }
                self._log_trade(result)
                return result
            else:
                print(f"  [LIVE] Order failed: {order_result}")
                return None

        except Exception as e:
            print(f"  [LIVE] Open error {coin}: {e}")
            return None

    def _close_on_exchange(self, coin: str) -> float | None:
        try:
            pos_data = None
            user_state = self.info.user_state(self.address)
            for ep in user_state.get("assetPositions", []):
                if ep.get("position", {}).get("coin") == coin:
                    pos_data = ep["position"]
                    break

            if not pos_data:
                return None

            szi = float(pos_data.get("szi", 0))
            if szi == 0:
                return None

            is_buy = szi < 0
            sz = self._round_size(coin, abs(szi))

            order_result = self.exchange.market_open(
                coin, is_buy, sz, None, None
            )

            if order_result.get("status") == "ok":
                statuses = order_result.get("response", {}).get("data", {}).get("statuses", [])
                for s in statuses:
                    if "filled" in s:
                        return float(s["filled"]["avgPx"])
                return None
            else:
                print(f"  [LIVE] Close order failed: {order_result}")
                return None

        except Exception as e:
            print(f"  [LIVE] Close error {coin}: {e}")
            return None

    def check_positions(self, prices: dict[str, float]) -> list[dict]:
        closed = []
        for coin in list(self.positions.keys()):
            pos = self.positions[coin]
            price = prices.get(coin)
            if not price:
                continue
            exit_reason = pos.check_exit(price)
            if exit_reason:
                fill_price = self._close_on_exchange(coin)
                if fill_price is None:
                    print(f"  [LIVE] Failed to close {coin} on exchange")
                    continue

                pnl = pos.pnl(fill_price)
                pnl_pct = pos.pnl_pct(fill_price)
                self.total_pnl += pnl
                self.total_trades += 1
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1

                result = {
                    "action": "CLOSE", "coin": coin, "side": pos.side,
                    "reason": exit_reason,
                    "entry": pos.entry_price, "exit": fill_price,
                    "pnl": round(pnl, 2), "pnl_pct": pnl_pct,
                    "balance": round(self.balance, 2),
                    "held_minutes": round((time.time() - pos.opened_at) / 60, 1),
                    "entry_score": pos.entry_score,
                    "entry_confidence": pos.entry_confidence,
                }
                self._log_trade(result)
                closed.append(result)
                del self.positions[coin]
        self._save_state()
        return closed

    def check_vault(self) -> float:
        return 0

    def get_stats(self, prices: dict[str, float]) -> dict:
        unrealized = sum(
            pos.pnl(prices.get(pos.coin, pos.entry_price))
            for pos in self.positions.values()
        )
        account_val = self.balance
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        return {
            "balance": round(account_val, 2),
            "starting": account_val,
            "total_return_pct": 0,
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(self.total_pnl, 2),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(win_rate, 1),
            "open_positions": len(self.positions),
            "vault": 0,
        }

    def _log_trade(self, trade: dict):
        trade["timestamp"] = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %I:%M:%S %p")
        trade["mode"] = "LIVE"
        self.trade_history.append(trade)
        try:
            lock_path = TRADE_LOG + ".lock"
            with open(lock_path, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    existing = []
                    if os.path.exists(TRADE_LOG):
                        with open(TRADE_LOG) as f:
                            existing = json.load(f)
                    existing.append(trade)
                    with open(TRADE_LOG, "w") as f:
                        json.dump(existing, f, indent=2)
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
        except Exception:
            pass

    def _save_state(self):
        state = {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "total_pnl": self.total_pnl,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "mode": "LIVE",
        }
        _locked_write(STATE_FILE, state)

    def _load_state(self):
        state = _locked_read(STATE_FILE)
        if not state:
            return
        try:
            self.total_trades = state["total_trades"]
            self.wins = state["wins"]
            self.losses = state["losses"]
            self.total_pnl = state["total_pnl"]
            self.positions = {
                k: Position.from_dict(v) for k, v in state["positions"].items()
            }
        except Exception:
            pass
