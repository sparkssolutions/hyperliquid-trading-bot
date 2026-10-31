import json
import time
import os
import fcntl
from datetime import datetime
from zoneinfo import ZoneInfo
from config import (
    STARTING_BALANCE, POSITION_SIZE_PCT, MAX_POSITIONS, LEVERAGE,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, TRAILING_STOP_PCT, TRAILING_ACTIVATE_PCT,
    VAULT_DEPOSIT_THRESHOLD, VAULT_DEPOSIT_PCT,
)

STATE_FILE = os.path.join(os.path.dirname(__file__), "paper_state.json")
TRADE_LOG = os.path.join(os.path.dirname(__file__), "trade_log.json")
STATE_LOCK = os.path.join(os.path.dirname(__file__), ".state.lock")


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


class Position:
    def __init__(self, coin, side, entry_price, size_usd, leverage, liq_tp=None):
        self.coin = coin
        self.side = side  # "LONG" or "SHORT"
        self.entry_price = entry_price
        self.size_usd = size_usd
        self.leverage = leverage
        self.quantity = (size_usd * leverage) / entry_price
        self.stop_loss = self._calc_stop(entry_price)
        self.take_profit = liq_tp if liq_tp else self._calc_tp(entry_price)
        self.trailing_stop = None
        self.rapid_trailing = False
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

        RAPID_TRAIL_PCT = 0.004  # 0.4% tight trail once rapid profit triggers

        held_min = (time.time() - self.opened_at) / 60
        if pnl_pct > 0 and held_min > 0:
            velocity = pnl_pct / held_min
            if (pnl_pct >= 2.0 and velocity >= 0.3) or (pnl_pct >= 1.5 and velocity >= 0.5):
                if not self.rapid_trailing:
                    self.rapid_trailing = True
                    if self.side == "LONG":
                        self.trailing_stop = current_price * (1 - RAPID_TRAIL_PCT)
                    else:
                        self.trailing_stop = current_price * (1 + RAPID_TRAIL_PCT)

        if self.rapid_trailing:
            if self.side == "LONG":
                new_trail = current_price * (1 - RAPID_TRAIL_PCT)
                if new_trail > self.trailing_stop:
                    self.trailing_stop = new_trail
                if current_price <= self.trailing_stop:
                    return "RAPID_TRAIL"
            else:
                new_trail = current_price * (1 + RAPID_TRAIL_PCT)
                if new_trail < self.trailing_stop:
                    self.trailing_stop = new_trail
                if current_price >= self.trailing_stop:
                    return "RAPID_TRAIL"

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
            "rapid_trailing": self.rapid_trailing,
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
        p.rapid_trailing = d.get("rapid_trailing", False)
        p.highest_pnl_pct = d.get("highest_pnl_pct", 0)
        p.opened_at = d.get("opened_at", time.time())
        p.entry_score = d.get("entry_score", 0)
        p.entry_confidence = d.get("entry_confidence", 0)
        p.entry_reasons = d.get("entry_reasons", [])
        return p


class PaperTrader:
    def __init__(self):
        self.balance = STARTING_BALANCE
        self.positions: dict[str, Position] = {}
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0
        self.vault = 0.0
        self.trade_history: list[dict] = []
        self._load_state()

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

    def open_position(self, coin: str, side: str, price: float, signal: dict | None = None) -> dict | None:
        if coin in self.positions:
            return None
        if len(self.positions) >= MAX_POSITIONS:
            return None
        size_usd = self.balance * POSITION_SIZE_PCT
        if size_usd < 10:
            return None
        liq_tp = self._get_liq_tp(coin, side, price)
        pos = Position(coin, side, price, size_usd, LEVERAGE, liq_tp=liq_tp)
        if signal:
            pos.entry_score = signal.get("score", 0)
            pos.entry_confidence = signal.get("confidence", 0)
            pos.entry_reasons = signal.get("reasons", [])
        self.positions[coin] = pos
        self._save_state()
        result = {
            "action": "OPEN", "coin": coin, "side": side,
            "entry": price, "size_usd": round(size_usd, 2),
            "quantity": round(pos.quantity, 6),
            "stop_loss": pos.stop_loss, "take_profit": pos.take_profit,
            "leverage": f"{LEVERAGE}x",
            "score": signal.get("score", 0) if signal else 0,
            "confidence": signal.get("confidence", 0) if signal else 0,
        }
        self._log_trade(result)
        return result

    def check_positions(self, prices: dict[str, float]) -> list[dict]:
        closed = []
        disk = _locked_read(STATE_FILE)
        disk_positions = set(disk.get("positions", {}).keys()) if disk else set()
        for coin in list(self.positions.keys()):
            if coin not in disk_positions:
                del self.positions[coin]
                continue
            pos = self.positions[coin]
            price = prices.get(coin)
            if not price:
                continue
            exit_reason = pos.check_exit(price)
            if exit_reason:
                pnl = pos.pnl(price)
                pnl_pct = pos.pnl_pct(price)
                self.balance += pnl
                self.total_pnl += pnl
                self.total_trades += 1
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                result = {
                    "action": "CLOSE", "coin": coin, "side": pos.side,
                    "reason": exit_reason,
                    "entry": pos.entry_price, "exit": price,
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
        profit = self.balance - STARTING_BALANCE
        if profit > VAULT_DEPOSIT_THRESHOLD:
            excess = profit - VAULT_DEPOSIT_THRESHOLD
            deposit = round(excess * VAULT_DEPOSIT_PCT, 2)
            if deposit > 1:
                self.vault += deposit
                self.balance -= deposit
                self._save_state()
                return deposit
        return 0

    def get_stats(self, prices: dict[str, float]) -> dict:
        unrealized = sum(
            pos.pnl(prices.get(pos.coin, pos.entry_price))
            for pos in self.positions.values()
        )
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        return {
            "balance": round(self.balance, 2),
            "starting": STARTING_BALANCE,
            "total_return_pct": round((self.balance + self.vault - STARTING_BALANCE) / STARTING_BALANCE * 100, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(self.total_pnl, 2),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(win_rate, 1),
            "open_positions": len(self.positions),
            "vault": round(self.vault, 2),
        }

    def _log_trade(self, trade: dict):
        from config import STRATEGY_VERSION
        trade["timestamp"] = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %I:%M:%S %p")
        trade["strategy"] = STRATEGY_VERSION
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
            "balance": self.balance,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "total_pnl": self.total_pnl,
            "vault": self.vault,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
        }
        _locked_write(STATE_FILE, state)

    def _load_state(self):
        state = _locked_read(STATE_FILE)
        if not state:
            return
        try:
            self.balance = state["balance"]
            self.total_trades = state["total_trades"]
            self.wins = state["wins"]
            self.losses = state["losses"]
            self.total_pnl = state["total_pnl"]
            self.vault = state.get("vault", 0.0)
            self.positions = {
                k: Position.from_dict(v) for k, v in state["positions"].items()
            }
        except Exception:
            pass
