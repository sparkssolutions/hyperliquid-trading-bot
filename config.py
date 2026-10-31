import os

COINS = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "NEAR"]
STARTING_BALANCE = 10000.0  # paper trading balance
POSITION_SIZE_PCT = 0.10    # 10% of balance per trade
MAX_POSITIONS = 3           # one per coin max
LEVERAGE = 5                # 5x leverage

# Timeframes for analysis
CANDLE_INTERVAL = "15m"     # primary signal timeframe
TREND_INTERVAL = "1h"       # trend confirmation timeframe

# Indicator settings
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOLUME_SPIKE_MULT = 1.5     # volume must be 1.5x average to confirm

# Risk management
STOP_LOSS_PCT = 0.012       # 1.2% stop loss (6% at 5x)
TAKE_PROFIT_PCT = 0.04      # 4% take profit (fallback if no liq target)
TRAILING_STOP_PCT = 0.008   # 0.8% trailing stop — tight to lock profit
TRAILING_ACTIVATE_PCT = 1.5 # activate at 1.5% profit
TIGHT_MODE_EXPIRES = 1790913720  # 2026-10-01 22:02 CT — after this, revert to moderate

# Vault
VAULT_DEPOSIT_THRESHOLD = 200   # deposit when profit exceeds this
VAULT_DEPOSIT_PCT = 0.50        # deposit 50% of excess profit

# Loop timing
POLL_INTERVAL = 30          # seconds between checks

# Anti-churn
MIN_HOLD_TREND_REVERSED = 1800  # 30 min before TREND_REVERSED can exit
SUPPORT_BOUNCE_COOLDOWN = 600   # 10 min cooldown after bounce exit
CHOP_RANGE_PCT = 0.005          # 0.5% — if last 6 candles range < this, market is chopping

# Telegram signal alerts
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [int(x) for x in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
SIGNAL_MIN_CONFIDENCE = 0.57

# Trading sessions (CT hours, 24h format)
# Only open new trades during active sessions
TRADING_SESSIONS = {
    "asia":    (19, 4),   # 7 PM - 4 AM CT
    "london":  (2, 11),   # 2 AM - 11 AM CT
    "new_york": (8, 17),  # 8 AM - 5 PM CT
}

STRATEGY_VERSION = "v2"
STRATEGY_LOG = [
    {"version": "v1", "start": "2026-09-30", "end": "2026-10-05",
     "changes": "Initial: score≥4, 10min AI hold, 1.5% SL, hard RAPID_PROFIT exit, no RSI block, no veto cooldown"},
    {"version": "v2", "start": "2026-10-05", "end": None,
     "changes": "Score≥5 (choppy≥6), 15min AI hold, 1.2% SL, RAPID_TRAIL 0.4%, RSI>70 LONG block / RSI<30 SHORT block, 30min veto cooldown, tighter AI_EXIT prompt"},
]

HYPERLIQUID_API = "https://api.hyperliquid.xyz"
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY", "")
COINGLASS_API = "https://open-api-v4.coinglass.com"

