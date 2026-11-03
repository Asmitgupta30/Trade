# 🚀 Quickstart & Push Instructions — Indian Markets Trading Advisor v2.0

This package contains the complete **Indian Markets Trading Advisor v2.0** repository, pre-initialized with Git and committed to cleanly wipe out and replace `https://github.com/Asmitgupta30/Trade.git`.

---

## 📌 STEP 1: Push to GitHub (Replace & Wipe Old History)

Since you have the GitHub Personal Access Token on Windows, run **ONE** of the following options in the extracted project directory:

### Option A: Direct Push via Token (Recommended)
Replace `<YOUR_GITHUB_TOKEN>` with your GitHub Personal Access Token:

#### Windows Command Prompt (CMD):
```cmd
git remote set-url origin https://<YOUR_GITHUB_TOKEN>@github.com/Asmitgupta30/Trade.git
git push --force -u origin main
```

#### Windows PowerShell:
```powershell
git remote set-url origin "https://<YOUR_GITHUB_TOKEN>@github.com/Asmitgupta30/Trade.git"
git push --force -u origin main
```

#### Git Bash:
```bash
git remote set-url origin https://<YOUR_GITHUB_TOKEN>@github.com/Asmitgupta30/Trade.git
git push --force -u origin main
```

---

### Option B: Interactive Login (GitHub Credential Manager)
If your Windows Git has GitHub Credential Manager installed, simply run:
```cmd
git push --force -u origin main
```
*A browser pop-up or token prompt will appear to authorize the push.*

---

## 💻 STEP 2: Running the Project on Windows

### 1. One-Click Launch (Easiest)
Double-click:
```
run_windows.bat
```
This script automatically:
1. Detects Python 3.10+
2. Creates `venv` and installs all packages from `requirements.txt`
3. Copies `.env.example` to `.env`
4. Runs `scripts\verify_system.py` to confirm zero execution paths
5. Launches the **FastAPI Backend Server + Schedulers** in Window 1
6. Launches the **Autonomous Turnaround Trading Desk** in Window 2
7. Opens `http://localhost:8000` in your default browser

---

### 2. Manual Commands via `make.bat`
```cmd
:: Install dependencies
make.bat install

:: Verify system integrity & run tests
make.bat verify

:: Run tests only
make.bat test

:: Launch entire system (Backend + Desk + Browser)
make.bat all

:: Or launch components individually:
make.bat run     REM Starts Backend Server + Schedulers
make.bat desk    REM Starts Autonomous Trading Desk Daemon
```

---

### 3. PowerShell Launch
```powershell
.\run_windows.ps1
```

---

## 🛡️ Key Features Included in This Build

1. **3-Year Historical Valuation Screener (`advisor/valuation.py`)**:
   - Bloomberg Relative Valuation (RV) Model comparing trailing P/E & P/B vs 3-year rolling distribution $(\mu \pm \sigma)$.
   - Graham Intrinsic Value ($\sqrt{22.5 \times \text{EPS} \times \text{BVPS}}$) & Margin of Safety.
   - Piotroski quality / ROE filter.
   - Accessible at UI tab: **Valuation & P/E**.

2. **Market News Catalyst Engine (`advisor/news_agent.py`)**:
   - Ingests real-time morning feeds from Moneycontrol, Economic Times, Livemint.
   - Sentiment scoring ($-1.0$ to $+1.0$) with automated symbol catalyst tagging.
   - Gates the trading desk against entering long on negative news.

3. **Autonomous Paper Trading Desk (`scripts/autonomous_trading_desk.py`)**:
   - Daily hard cap: max 4 trades/day.
   - 1 trade per symbol per session guard.
   - Ratchet breakeven stops (eliminated the 90-minute time-stall exit).
   - Target expectancy filter ($\ge 1.2\%$ distance).

4. **Full-Screen Responsive Institutional Terminal**:
   - Full-width fluid responsive layout (no cropped margins or narrow 960px constraints).
   - High-contrast OLED dark theme with Inter Tight and JetBrains Mono fonts.

5. **Strict Advisory Lock (Permanent Guarantee)**:
   - `assert_no_execution_path()` verified. No live broker order API exists.
   - `risk_config.yaml` is immutable from code.
