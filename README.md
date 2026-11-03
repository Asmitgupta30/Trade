# 🦅 AuraQuant: Institutional Quantitative Research Platform & Market Risk Engine

[![Architecture](https://img.shields.io/badge/Architecture-Modular%20Quant%20Pipeline-blue.svg?style=flat-square&logo=diagramsdotnet)](configs/risk_config.yaml)
[![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-10b981.svg?style=flat-square&logo=python)](requirements.txt)
[![UI Terminal](https://img.shields.io/badge/UI-Full%20Screen%20Fluid%20OLED-38bdf8.svg?style=flat-square&logo=googlechrome)](frontend/index.html)
[![Valuation Engine](https://img.shields.io/badge/Screener-Multi--Year%20Relative%20Valuation-8b5cf6.svg?style=flat-square)](advisor/valuation.py)
[![Sentiment Engine](https://img.shields.io/badge/Sentiment-Pre--Market%20Catalysts-f59e0b.svg?style=flat-square)](advisor/news_agent.py)
[![Test Suite](https://img.shields.io/badge/Tests-186%20Passed%20%7C%20100%25-success.svg?style=flat-square&logo=pytest)](tests/)

**AuraQuant** is an institutional-grade quantitative equity research platform and autonomous risk engine. Designed in the tradition of advanced quantitative toolkits, it combines Point-in-Time (PIT) safe feature engineering, hybrid machine learning ensembles, multi-year statistical relative valuation models, real-time financial news sentiment gating, and automated position sizing with strict capital preservation invariants.

> [!IMPORTANT]
> **PERMANENT INSTITUTIONAL INVARIANT — ADVISORY ONLY**
> This system **NEVER executes market orders directly**. It computes, screens, simulates, and generates quantitative suggestions. Every execution decision remains entirely in the hands of the human portfolio manager. Zero automated broker connectivity exists or can ever be registered (`assert_no_execution_path()`). Furthermore, `configs/risk_config.yaml` is **read-only from code**—no self-optimizing algorithm can alter or loosen its own risk parameters.

---

## 🏛️ System Architecture

AuraQuant is structured into a modular, decoupled pipeline:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              AuraQuant Core Pipeline                                   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│   [ 1. Data Ingestion ] ──────► Immutable Bar Store & EOD Bhavcopy Archives           │
│             │                                                                          │
│             ▼                                                                          │
│   [ 2. Factor Engineering ] ──► Point-in-Time (PIT) Multi-Timeframe Feature Pipeline   │
│             │                                                                          │
│             ▼                                                                          │
│   [ 3. Predictive Modeling ] ─► LightGBM + Temporal Sequence Ensembles & HPO           │
│             │                                                                          │
│             ▼                                                                          │
│   [ 4. Factor Screening ] ────► Relative Valuation Discrepancies + News Catalysts      │
│             │                                                                          │
│             ▼                                                                          │
│   [ 5. Risk & Allocation ] ───► Volatility Sizing, Ratchet Stops & Expectancy Hurdles │
│             │                                                                          │
│             ▼                                                                          │
│   [ 6. Presentation Layer ] ──► Edge-to-Edge OLED Terminal SPA & REST Endpoints        │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ Core Institutional Modules

### 1. Multi-Year Historical Relative Valuation Screener (`advisor/valuation.py`)
- **Distributional Valuation Analysis**: Continuously benchmarks current equity multiples against long-term rolling historical distributions to separate cyclical market noise from structural value opportunities.
- **Sector-Adapted Quality Filtering**: Implements tailored balance sheet health and return-on-equity criteria across non-financial and banking sectors to filter out fundamental value traps.
- **Dual Valuation Modeling**: Synthesizes multiple asset pricing metrics to compute conservative intrinsic margins of safety across the liquid universe.

### 2. Real-Time Financial News Sentiment Engine (`advisor/news_agent.py`)
- **Pre-Market Ingestion**: Automated extraction of financial news headlines from primary market feeds prior to market open.
- **Multi-Factor Catalyst Scoring**: Uses financial natural language processing to detect major macro, earnings, and regulatory developments.
- **Directional Catalyst Gating**: Dynamically suppresses execution suggestions that contradict underlying institutional news flow.

### 3. Quantitative Autonomous Simulation Desk (`scripts/autonomous_trading_desk.py`)
- **Session Hard Cap**: Strictly throttles daily activity to preserve capital against churn and execution friction.
- **Anti-Churn Guard**: Enforces single-allocation rules per symbol per session to eliminate whipsaw drawdowns.
- **Ratchet Breakeven Mechanism**: Protects accumulated profits by dynamically shifting protective thresholds once target thresholds are reached.
- **Expectancy Hurdles**: Ensures every prospective setup demonstrates favorable risk-to-reward asymmetry exceeding all exchange and operational hurdles.

### 4. Technical Factor & Pattern Engine (`advisor/strategies.py`)
- **Volatility Contraction Analysis**: Identifies progressive price consolidation in Stage-2 structural trends.
- **Momentum & Range Expansion**: Detects early volatility breakouts supported by anomalous volume accumulation.
- **Exhaustion Reversals**: Screens for seller absorption and liquidity reclaims following multi-session pullbacks.
- **Institutional Liquidity Blocks**: Maps key footprint accumulation zones and market structure shifts.

### 5. Multi-Scenario Forward Simulation (`advisor/scenario_simulator.py`)
- **Stochastic Path Modeling**: Runs thousands of randomized forward paths across selectable time horizons to evaluate payoff probabilities and Value-at-Risk (95% VaR).
- **Macro Regime Allocation**: Dynamic equity-versus-cash allocation recommendations adapting to market volatility and institutional participation breadth.

---

## 🖥️ Terminal Display Interface

AuraQuant provides a full-width, zero-margin terminal designed for low-light desk environments:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 📈 AURAQUANT — INSTITUTIONAL QUANTITATIVE TERMINAL              [ADVISORY] [SESSION ACTIVE] [LIVE] ☼ ☽ │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Profile: [Conservative ₹50k] [Growth Allocation ₹1L] [Capital Builder ₹5L]      Theme: (● Emerald) (○ Sky)│
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [Quantitative Ideas] [Order Blocks] [Intraday Scalp] [Time Machine] [Valuation] [Playbook] [Portfolio] │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ MULTI-YEAR HISTORICAL VALUATION & RELATIVE VALUE SCREENER (INSTITUTIONAL METRICS)                       │
│ ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐ ┌─────────────────────────┐ │
│ │  UNIVERSE MEDIAN P/E │ │ DISCOUNTED MULTIPLES │ │ DEEP VALUE METRICS   │ │ NEWS SENTIMENT CATALYSTS│ │
│ │  22.4x (Liquid Top)  │ │ 18 Symbols Screened  │ │ 7 Quality Leaders    │ │ 24 Live Morning Feeds   │ │
│ └──────────────────────┘ └──────────────────────┘ └──────────────────────┘ └─────────────────────────┘ │
│                                                                                                        │
│ SYMBOL     SECTOR       PRICE (₹)   HISTORICAL SPREAD   DISCOUNT %   METRIC STATUS   RECOMMENDATION    │
│ ASSET-A    Energy       ₹1,274.60   -1.77 StdDev        -30.0%       Favorable       ACCUMULATE        │
│ ASSET-B    Financials   ₹6,881.30   -2.15 StdDev        -39.2%       Deep Value      HIGH CONVICTION ★ │
│ ASSET-C    Technology   ₹1,850.00   -0.85 StdDev        -14.5%       Discounted      FAVORABLE         │
│ ASSET-D    Technology   ₹4,120.00   -0.12 StdDev         -2.0%       Equilibrium     FAIR VALUE        │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Installation & Reproduction Guide

### 🪟 Windows Setup

#### 1. One-Click Automated Launch
Double-click:
```cmd
run_windows.bat
```
*Creates the virtual environment, installs dependencies, executes system integrity audits, launches the backend and simulation desk, and opens `http://localhost:8000`.*

#### 2. PowerShell
```powershell
.\run_windows.ps1
```

#### 3. Command Prompt (`make.bat`)
```cmd
make.bat install   REM Create virtual environment and install requirements
make.bat verify    REM Execute integrity checks and full test suite
make.bat all       REM Launch backend service and simulation desk
```

---

### 🐧 Linux & macOS Setup

```bash
# 1. Clone repository
git clone <YOUR_REPOSITORY_URL>
cd <REPOSITORY_FOLDER>

# 2. Virtual environment initialization
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env

# 3. System integrity audit (must output: "verify: ALL CHECKS GREEN")
python scripts/verify_system.py

# 4. Execute test suite (186 verification tests)
python -m pytest -q

# 5. Launch AuraQuant Backend & Terminal UI
python scripts/run_system.py
# Access dashboard at: http://localhost:8000

# 6. (Optional) Run Autonomous Paper Simulation Desk in background
python scripts/autonomous_trading_desk.py
```

#### 🌙 Overnight Background Model Training (Linux Server)
```bash
# Launch governed Bayesian Hyperparameter Optimization in background:
nohup python scripts/retrain_challenger.py --hpo-trials 25 > var/logs/challenger_retrain.log 2>&1 &
echo $! > var/status/challenger_pid.txt

# Monitor live training progress:
tail -f var/logs/challenger_retrain.log
```

---

## 📁 Repository Scaffolding

```
AuraQuant/
├── advisor/                        # Quantitative valuation & signal engines
│   ├── engine.py                   # Multi-strategy scoring and prioritization
│   ├── live_intraday.py            # Opening range breakout & VWAP momentum
│   ├── mutualfunds.py              # Portfolio allocation & compounding
│   ├── news_agent.py               # Pre-market news sentiment engine
│   ├── orderblock.py               # Institutional order blocks & BOS retests
│   ├── scenario_simulator.py       # Monte Carlo forward time projection
│   ├── strategies.py               # Volatility contraction & momentum rules
│   └── valuation.py                # Multi-year relative valuation screener
├── backend/                        # FastAPI REST API & scheduled market jobs
├── common/                         # Configuration, precision time utilities, types
├── configs/                        # Configuration & governance policies
│   └── risk_config.yaml            # Permanent read-only risk rules
├── data/                           # Data ingestors, providers & archive parsers
├── features/                       # Point-in-time feature engineering pipeline
├── frontend/                       # Institutional Terminal SPA (HTML/CSS/JS)
├── models/                         # LightGBM + Sequence ensembles & walk-forward CV
├── paper_trading/                  # Dual-track paper ledger & simulation engine
├── scripts/                        # Autonomous desk, snapshot builder, runner
├── tests/                          # 186 automated pytest verification tests
├── make.bat / Makefile             # Windows & Unix build automation
├── run_windows.bat / .ps1          # Windows one-click launchers
└── requirements.txt                # Production Python dependencies
```

---

## 🔒 Security, Integrity & Compliance

- **Zero-Execution Architecture**: Verified on startup by `assert_no_execution_path()`. No broker order placement code path exists.
- **Config Hard Lock**: `configs/risk_config.yaml` is immutable from code; self-modifications are strictly blocked.
- **Data Privacy**: No confidential keys, broker PINs, or proprietary tokens are committed.
- **License**: MIT License. See [LICENSE](LICENSE) for details.
