@echo off
REM =====================================================================
REM  Indian Markets Trading Advisor — Windows One-Click Launcher
REM  Advisory Only — Institutional Trading Terminal
REM =====================================================================
title Indian Markets Trading Advisor - Launchpad
color 0A
cd /d "%~dp0"

echo =====================================================================
echo    INDIAN MARKETS TRADING ADVISOR - ADVISORY TERMINAL
echo    Autonomous Desk ^| 3Y Valuation Engine ^| Market News Catalyst
echo =====================================================================
echo.

REM 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH!
    echo Please install Python 3.10 or newer from https://www.python.org/
    echo Ensure "Add Python to PATH" is checked during installation.
    pause
    exit /b 1
)

REM 2. Setup Virtual Environment
if not exist "venv\Scripts\activate.bat" (
    echo [*] Setting up virtual environment in .\venv ...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [*] Installing dependencies from requirements.txt ...
    call venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation encountered issues.
        pause
        exit /b 1
    )
) else (
    call venv\Scripts\activate.bat
)

REM 3. Ensure .env exists
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [*] Initialized .env configuration from template.
    )
)

REM 4. Verify system invariant & hard locks
echo [*] Running system integrity and governance verification...
python scripts\verify_system.py
if errorlevel 1 (
    echo.
    echo [ALERT] System verification failed. Aborting launch.
    pause
    exit /b 1
)
echo [OK] All system governance checks passed!
echo.

REM 5. Launch Backend Server in separate window
echo [*] Launching FastAPI backend server + market schedulers...
start "Trading Advisor - Backend Server" cmd /k "title Trading Advisor - Backend Server && cd /d "%~dp0" && call venv\Scripts\activate.bat && python scripts\run_system.py"

REM 6. Wait for backend startup
timeout /t 3 /nobreak >nul

REM 7. Launch Autonomous Desk Daemon in separate window
echo [*] Launching Autonomous Trading Desk Daemon...
start "Trading Advisor - Autonomous Desk" cmd /k "title Trading Advisor - Autonomous Desk && cd /d "%~dp0" && call venv\Scripts\activate.bat && python scripts\autonomous_trading_desk.py"

REM 8. Open Terminal in Browser
echo [*] Opening Institutional Terminal at http://localhost:8000 ...
start http://localhost:8000

echo.
echo =====================================================================
echo  Trading Advisor is now RUNNING!
echo  - Web Dashboard:  http://localhost:8000
echo  - Backend Logs:   Check the "Trading Advisor - Backend Server" window
echo  - Desk Logs:      Check the "Trading Advisor - Autonomous Desk" window
echo.
echo  To shut down: Close the server windows or press Ctrl+C in each.
echo =====================================================================
echo.
pause
