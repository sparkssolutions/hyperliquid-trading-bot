# Hyperliquid Trading Bot

Autonomous crypto trading bot for [Hyperliquid DEX](https://hyperliquid.xyz) with AI-powered trade filtering, real-time Telegram alerts, and a live dashboard.

## Features

- **Multi-coin support** — trades BTC, ETH, SOL, HYPE, ZEC, NEAR simultaneously
- **Technical analysis engine** — EMA crossovers, RSI, MACD, volume spikes across multiple timeframes (15m + 1h trend confirmation)
- **AI trade filter** — every signal passes through an LLM risk analyst that evaluates market structure, liquidation data, portfolio coherence, and support/resistance before confirming or vetoing
- **Liquidation cluster analysis** — estimates where leveraged positions will get liquidated to find high-probability reversal zones
- **Swing level detection** — identifies support/resistance from recent price action
- **Coinglass integration** — funding rates, open interest, and liquidation data for directional bias
- **Paper + live trading** — identical interfaces; swap one import to go live
- **Risk management** — stop losses, take profits, trailing stops with configurable activation thresholds
- **Telegram bot** — real-time alerts, position monitoring, and command interface (`/status`, `/positions`, `/pnl`, etc.)
- **Web dashboard** — Flask-based real-time dashboard showing positions, P&L, signals, and trade history
- **Google Sheets logging** — automatic trade logging to Google Sheets for record keeping
- **Trading session awareness** — only opens new positions during active market sessions (Asia, London, New York)

## Architecture

```
main.py              — entry point, starts bot + dashboard
├── signals.py       — technical signal generation (EMA/RSI/MACD/volume)
├── indicators.py    — TA calculations + swing level detection
├── ai_filter.py     — LLM-based trade confirmation/veto system
├── paper_trader.py  — paper trading execution engine
├── live_trader.py   — live trading via Hyperliquid SDK
├── data_feed.py     — market data from Hyperliquid API
├── coinglass.py     — Coinglass API integration (funding, OI, liquidations)
├── liq_estimator.py — liquidation zone estimation
├── dashboard.py     — Flask web dashboard
├── telegram_commands.py — Telegram bot command handler
├── signal_notifier.py   — Telegram alert dispatcher
├── sheets_logger.py     — Google Sheets trade logger
├── price_alert.py       — stock/crypto price alert monitor
└── config.py            — all configuration (loads secrets from env)
```

## Setup

```bash
# Clone and install dependencies
pip install hyperliquid-python-sdk requests flask yfinance eth-account

# Configure environment
cp .env.example .env
# Edit .env with your Telegram bot token, chat IDs, and Coinglass API key

# For live trading, create live_config.json:
# {"private_key": "0x...", "use_mainnet": true}

# Run (paper trading by default)
python3 main.py
```

The dashboard runs on port 5555 by default.

## Built With

- Python
- Hyperliquid Python SDK
- Flask (dashboard)
- OpenAI Codex CLI (AI trade filter)
- Coinglass API
- Telegram Bot API
- Google Sheets API
