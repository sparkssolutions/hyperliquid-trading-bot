import subprocess
import json
import os
import tempfile

import fcntl

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
TRADE_LOG = os.path.join(BOT_DIR, "trade_log.json")
VETO_LOG = os.path.join(BOT_DIR, "veto_log.json")


def _log_veto(coin: str, side: str, price: float, signal: dict,
              decision: str, reason: str, analysis: dict, trend_1h: dict):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    entry = {
        "timestamp": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        "coin": coin,
        "side": side,
        "price": price,
        "decision": decision,
        "reason": reason,
        "confidence": signal.get("confidence", 0),
        "score": signal.get("score", 0),
        "reasons": signal.get("reasons", [])[:5],
        "trend_15m": analysis.get("trend", ""),
        "rsi": analysis.get("rsi", 0),
        "trend_1h": trend_1h.get("trend", ""),
    }
    lock_path = VETO_LOG + ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            existing = []
            if os.path.exists(VETO_LOG):
                with open(VETO_LOG) as f:
                    existing = json.load(f)
            existing.append(entry)
            with open(VETO_LOG, "w") as f:
                json.dump(existing, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _get_recent_trades(coin: str, limit: int = 10) -> list[dict]:
    if not os.path.exists(TRADE_LOG):
        return []
    try:
        with open(TRADE_LOG) as f:
            trades = json.load(f)
        coin_trades = [t for t in trades if t.get("coin") == coin]
        return coin_trades[-limit:]
    except Exception:
        return []


def _build_prompt(coin: str, side: str, price: float, signal: dict,
                  analysis: dict, trend_1h: dict, cg_ctx: dict | None,
                  positions: dict) -> str:
    recent = _get_recent_trades(coin, 10)

    recent_summary = ""
    if recent:
        lines = []
        for t in recent[-6:]:
            if t["action"] == "CLOSE":
                lines.append(f"  {t['side']} closed: ${t.get('pnl',0):+.2f} ({t.get('reason','')}, held {t.get('held_minutes',0):.0f}m)")
            else:
                lines.append(f"  OPEN {t['side']} @ ${t.get('entry', t.get('entry_price',0)):,.2f}")
        recent_summary = "\n".join(lines)

    open_positions = ""
    portfolio_bias = ""
    if positions:
        parts = []
        longs = []
        shorts = []
        for c, pos in positions.items():
            parts.append(f"  {pos.side} {c} @ ${pos.entry_price:,.2f}")
            if pos.side == "LONG":
                longs.append(c)
            else:
                shorts.append(c)
        open_positions = "\n".join(parts)
        if len(longs) >= 2 and not shorts:
            portfolio_bias = f"PORTFOLIO DIRECTION: All {len(longs)} open positions are LONG ({', '.join(longs)}). Market is trending bullish across assets."
        elif len(shorts) >= 2 and not longs:
            portfolio_bias = f"PORTFOLIO DIRECTION: All {len(shorts)} open positions are SHORT ({', '.join(shorts)}). Market is trending bearish across assets."
        elif longs and shorts:
            portfolio_bias = f"PORTFOLIO DIRECTION: Mixed — LONG {', '.join(longs)} / SHORT {', '.join(shorts)}."

    cg_section = ""
    if cg_ctx:
        cg_section = f"""Coinglass data:
  Bias: {cg_ctx.get('bias', 'neutral')} (score: {cg_ctx.get('score', 0)})
  Reasons: {', '.join(cg_ctx.get('reasons', [])[:4])}"""

    liq_section = ""
    try:
        from liq_estimator import estimate_liq_zones
        liq = estimate_liq_zones(coin)
        if liq and liq.get("top_clusters"):
            clusters = liq["top_clusters"][:6]
            liq_lines = []
            for c in clusters:
                dist = (c["price"] - price) / price * 100
                liq_lines.append(f"  {c['type'].upper()} liq @ ${c['price']:,.2f} ({dist:+.1f}% from entry) strength={c['strength']:.0f}")
            liq_section = "LIQUIDATION CLUSTERS (where liquidations are concentrated):\n" + "\n".join(liq_lines)
    except Exception:
        pass

    swing_section = ""
    swing = analysis.get("swing_levels")
    if swing:
        lines = []
        for lvl in swing.get("support", [])[:3]:
            dist = (price - lvl["price"]) / price * 100
            lines.append(f"  SUPPORT @ ${lvl['price']:,.2f} ({lvl['touches']} touches, {dist:+.1f}% from price)")
        for lvl in swing.get("resistance", [])[:3]:
            dist = (lvl["price"] - price) / price * 100
            lines.append(f"  RESISTANCE @ ${lvl['price']:,.2f} ({lvl['touches']} touches, +{dist:.1f}% from price)")
        if lines:
            swing_section = "SWING SUPPORT/RESISTANCE (recent price bounce zones):\n" + "\n".join(lines)

    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Chicago"))
    hour = now.hour

    prompt = f"""You are a crypto trading risk analyst. A momentum trading bot wants to open a position. Analyze ALL the data and decide: CONFIRM or VETO.

PROPOSED TRADE:
  {side} {coin} @ ${price:,.2f}
  Confidence: {signal.get('confidence', 0):.0%} (score: {signal.get('score', 0)}/7)
  Reasons: {', '.join(signal.get('reasons', [])[:6])}

MARKET DATA (15m):
  Trend: {analysis.get('trend', '?')}
  RSI: {analysis.get('rsi', 0):.1f}
  MACD histogram: {analysis.get('macd_histogram', 0):.6f}
  EMA fast/slow: {analysis.get('ema_fast', 0):.2f} / {analysis.get('ema_slow', 0):.2f}

1H TREND: {trend_1h.get('trend', '?')} (RSI: {trend_1h.get('rsi', 0):.1f})

{cg_section}

{liq_section}

{swing_section}

RECENT {coin} TRADES:
{recent_summary or '  No recent trades'}

CURRENT OPEN POSITIONS:
{open_positions or '  None'}
{portfolio_bias}

TIME: {now.strftime('%I:%M %p CT')} (hour {hour})

RULES:
# ──────────────────────────────────────────────
# Custom trading rules redacted for public repo.
# This section contains proprietary entry/veto
# logic covering confidence thresholds, RSI gates,
# trend alignment, portfolio coherence, R/R eval,
# support/resistance awareness, and market bias.
# See .env.example for configuration.
# ──────────────────────────────────────────────

Respond with EXACTLY this format (no other text):
DECISION: CONFIRM, VETO, or REVERSE
REASON: one sentence why"""

    return prompt


def check_trade(coin: str, side: str, price: float, signal: dict,
                analysis: dict, trend_1h: dict, cg_ctx: dict | None,
                positions: dict) -> dict:
    prompt = _build_prompt(coin, side, price, signal, analysis, trend_1h, cg_ctx, positions)

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            out_path = tmp.name

        result = subprocess.run(
            ["codex", "exec", "--ephemeral", "--skip-git-repo-check",
             "-o", out_path, "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=45,
            cwd=BOT_DIR,
        )

        text = ""
        if os.path.exists(out_path):
            with open(out_path) as f:
                text = f.read().strip()
            os.unlink(out_path)

        if not text:
            text = result.stdout.strip()

        decision = "VETO"
        reason = "Could not parse AI response"

        for line in text.split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("DECISION:"):
                val = line.split(":", 1)[1].strip().upper()
                if "CONFIRM" in val:
                    decision = "CONFIRM"
                elif "REVERSE" in val:
                    decision = "REVERSE"
                else:
                    decision = "VETO"
            elif upper.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()

        if decision in ("VETO", "REVERSE"):
            _log_veto(coin, side, price, signal, decision, reason, analysis, trend_1h)

        return {"decision": decision, "reason": reason, "raw": text[:200]}

    except subprocess.TimeoutExpired:
        return {"decision": "CONFIRM", "reason": "AI filter timed out — defaulting to signal", "raw": "timeout"}
    except FileNotFoundError:
        return {"decision": "CONFIRM", "reason": "codex CLI not found — defaulting to signal", "raw": "not_found"}
    except Exception as e:
        return {"decision": "CONFIRM", "reason": f"AI filter error: {str(e)[:80]}", "raw": str(e)[:200]}


def _build_exit_prompt(coin: str, position: dict, price: float,
                       analysis: dict, trend_1h: dict, cg_ctx: dict | None,
                       all_positions: dict) -> str:
    side = position.side
    entry = position.entry_price
    pnl_pct = position.pnl_pct(price)
    pnl_usd = position.pnl(price)
    held_min = ((__import__("time").time() - position.opened_at) / 60)

    recent = _get_recent_trades(coin, 6)
    recent_summary = ""
    if recent:
        lines = []
        for t in recent[-4:]:
            if t["action"] == "CLOSE":
                lines.append(f"  {t['side']} closed: ${t.get('pnl',0):+.2f} ({t.get('reason','')})")
        recent_summary = "\n".join(lines)

    other_positions = ""
    for c, pos in all_positions.items():
        if c != coin:
            other_pnl = pos.pnl_pct(price) if c == coin else 0
            other_positions += f"  {pos.side} {c} @ ${pos.entry_price:,.2f}\n"

    cg_section = ""
    if cg_ctx:
        cg_section = f"""Coinglass data:
  Bias: {cg_ctx.get('bias', 'neutral')} (score: {cg_ctx.get('score', 0)})
  Reasons: {', '.join(cg_ctx.get('reasons', [])[:4])}"""

    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Chicago"))

    prompt = f"""You are a crypto trading risk analyst. Evaluate whether to HOLD or EXIT this open position.

OPEN POSITION:
  {side} {coin} @ ${entry:,.2f} → current ${price:,.2f}
  P&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%)
  Held: {held_min:.0f} minutes
  Entry confidence: {position.entry_confidence:.0%}
  Stop loss: ${position.stop_loss:,.2f} | Take profit: ${position.take_profit:,.2f}

MARKET DATA (15m):
  Trend: {analysis.get('trend', '?')}
  RSI: {analysis.get('rsi', 0):.1f}
  MACD histogram: {analysis.get('macd_histogram', 0):.6f}
  EMA fast/slow: {analysis.get('ema_fast', 0):.2f} / {analysis.get('ema_slow', 0):.2f}

1H TREND: {trend_1h.get('trend', '?')} (RSI: {trend_1h.get('rsi', 0):.1f})

{cg_section}

RECENT {coin} EXITS:
{recent_summary or '  None'}

OTHER OPEN POSITIONS:
{other_positions or '  None'}

TIME: {now.strftime('%I:%M %p CT')}

REASONING GUIDELINES:
# ──────────────────────────────────────────────
# Custom exit reasoning rules redacted for public
# repo. This section contains proprietary hold/exit
# logic covering trend reversal thresholds, stale
# thesis detection, overnight risk, and drawdown
# tolerance rules.
# ──────────────────────────────────────────────

Respond with EXACTLY this format (no other text):
DECISION: HOLD or EXIT
REASON: one sentence why"""

    return prompt


def check_exit(coin: str, position, price: float,
               analysis: dict, trend_1h: dict, cg_ctx: dict | None,
               all_positions: dict) -> dict:
    prompt = _build_exit_prompt(coin, position, price, analysis, trend_1h, cg_ctx, all_positions)

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            out_path = tmp.name

        result = subprocess.run(
            ["codex", "exec", "--ephemeral", "--skip-git-repo-check",
             "-o", out_path, "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=45,
            cwd=BOT_DIR,
        )

        text = ""
        if os.path.exists(out_path):
            with open(out_path) as f:
                text = f.read().strip()
            os.unlink(out_path)

        if not text:
            text = result.stdout.strip()

        decision = "HOLD"
        reason = "Could not parse AI response"

        for line in text.split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("DECISION:"):
                val = line.split(":", 1)[1].strip().upper()
                if "EXIT" in val:
                    decision = "EXIT"
                else:
                    decision = "HOLD"
            elif upper.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()

        return {"decision": decision, "reason": reason, "raw": text[:200]}

    except subprocess.TimeoutExpired:
        return {"decision": "HOLD", "reason": "AI exit check timed out — holding", "raw": "timeout"}
    except Exception as e:
        return {"decision": "HOLD", "reason": f"AI exit error: {str(e)[:80]}", "raw": str(e)[:200]}
