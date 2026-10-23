# Trading Advisor — Advisory-Only ML System for Indian Markets

> 🤖 **Autonomous AI Agent Quickstart (Linux)**:
> If an AI agent (Claude Code, Antigravity, Cursor, Codex) is running this repo on a Linux machine to set up and train overnight, immediately refer to:
> 📖 **[AI_AGENT_RUNBOOK.md](AI_AGENT_RUNBOOK.md)**

---

**This system NEVER places orders. It only suggests. You place every order manually in your broker app.** 
There is no order-execution code path — this is a permanent architectural constraint enforced by config hard locks, a startup assertion, an API-route test, and a grep audit (`make verify`). A second hard lock makes `risk_config.yaml` **read-only from code**: no module may write YAML or open the ruleset for writing, so a self-improving model cannot loosen its own passing criteria.

---

## Supported Trading Styles & Indian Market Strategies

The advisory engine screens NSE equities and applies institutional quantitative filters adapted from classic market strategies:

1. **Intraday Momentum / ORB (`intraday_breakout`)**:
   - High relative volume (RVOL ≥ 1.5×), ATR-normalized stop-loss (1.0× ATR), 2.0× target (1:2 R:R).
   - Enforces 15:15 IST strict auto-squareoff reminder.
2. **Minervini Volatility Contraction Pattern (`swing_vcp`)**:
   - Progressive contraction of price volatility (T1 → T2 → T3) near 52-week or 20-day highs in Stage 2 uptrend.
   - Sizing capped at 8% maximum portfolio allocation.
3. **Stockbee Momentum Burst (`stockbee_burst`)**:
   - 4%+ breakout with range expansion (1.5× 10-day ATR) and volume expansion (2× 20-day SMA).
   - Designed for 3–5 day swing moves with trailing stops.
4. **Stockbee Selling Exhaustion Hammer (`stockbee_hammer`)**:
   - Reversal setup after multi-day pullback: long lower wick (≥ 60% of candle range) reclaiming prior day's low on high volume.
   - Tight risk defined by the candle's low.
5. **Dividend Growth Pullback (`dividend_pullback`)**:
   - Top-tier dividend yield/growth companies (large caps) pulling back into oversold RSI (RSI-14 ≤ 40).
   - Multi-week holding horizon with conservative stop-loss.

---

## Architecture & Governance

| Component | Status | Details |
|---|---|---|
| **Data Layer** | Hybrid | Angel One SmartAPI (free streaming) or Yahoo Finance NSE fallback; AMFI/mfapi.in for Mutual Funds |
| **Feature Pipeline** | PIT Safe | Point-in-time safe, lookahead-bias tested, multi-timeframe indicators |
| **Model** | Ensemble | LightGBM tabular + Sequence (LSTM/Torch) confidence-weighted |
| **Governance** | Champion/Challenger | Weekly retrain ledger (`var/models/history.jsonl`), walk-forward validation |
| **Risk Gate** | Immutable | Position sizing formula in `risk_config.yaml` hard-locked against programmatic tampering |
| **UI** | Modern SPA | Interactive terminal dashboard with strategy filter pills, 4-box key levels, and risk position sizing |

---

## Linux / Cross-Platform Quickstart

### 1. Installation
```bash
# Linux / macOS
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Verify system integrity & hard locks
make verify   # or python scripts/verify_system.py
```

```powershell
# Windows PowerShell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
.\make.bat verify
```

### 2. Model Training (Overnight or Instant)
```bash
# Fast train using pre-packaged features
python scripts/train_model.py

# OR: Launch Governed Bayesian HPO Retrain in background for overnight run
nohup python scripts/retrain_challenger.py --hpo-trials 25 > var/logs/challenger_retrain.log 2>&1 &
```

### 3. Launch System
```bash
# Starts Market Streamer + FastAPI Backend + Background Schedulers
python scripts/run_system.py

# Open web dashboard: http://localhost:8000
```

---

## SmartAPI Credentials (Optional Upgrade)

Without credentials the system runs on Yahoo Finance NSE data (~15-min delayed, marked "delayed/stale" in the UI). To switch to Angel One SmartAPI live streaming:

1. Copy `.env.example` → `.env`
2. Add: `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PASSWORD` (PIN), `ANGEL_TOTP_SECRET`
3. Restart `python scripts/run_system.py` — feed indicator shows **LIVE**.

*Historical data and market feed are free on SmartAPI; only actual trades cost brokerage, and this system never executes trades.*

---

## The Honest Track Record Contract

* Every suggestion is logged with timestamp, confidence, rationale, and estimated friction.
* Hypothetical P&L ("if you took every suggestion") is tracked separately from self-reported actual trades. They are never merged.
* Losing streaks and drawdown are permanently visible.
* **Live-Ready Gate** (from `risk_config.yaml`): 28 paper-trading days AND 40 tracked suggestions AND hit-rate ≥ 50% AND Sharpe ≥ 1.0 AND max-DD ≤ 8%. Until achieved, the UI badge shows **NOT YET LIVE-READY**.

---

## Directory Scaffolding

```
advisor/        Engine, rules (risk_config enforcement), costs, rationale, MF advisor
backend/        FastAPI application + APScheduler check-in jobs
common/         Config loader (hard locks), IST time utils, procinfo, shared types
data/           DataClient (cache/stream/reconnect), providers, MFClient, scrip master
features/       Universe + liquidity filter, indicators, PIT pipeline, lookahead tests
frontend/       Modern static SPA (no node/npm build step needed)
models/         Tabular (LightGBM), sequence (LSTM/Torch), ensemble, walk-forward, bootstrap
paper_trading/  SQLite store, dual-track engine, gate math, circuit breakers
scripts/        Snapshot builders, trainer, challenger retrain, system runner, verification
tests/          Complete test suites for cross-platform, lookahead, rules, and strategies
var/            Scaffolding for logs, status, cache, reports, and model artifacts
```

---

## License & Disclaimer
This software is for educational, research, and advisory purposes only. It is not financial advice. No financial liability is assumed for decisions made based on this software.
