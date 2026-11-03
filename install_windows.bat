@echo off
REM =====================================================================
REM  Indian Markets Trading Advisor — Windows Setup & Installer
REM =====================================================================
title Indian Markets Trading Advisor - Installer
color 0B
cd /d "%~dp0"

echo =====================================================================
echo    INDIAN MARKETS TRADING ADVISOR - WINDOWS INSTALLATION
echo =====================================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please download and install Python 3.10+ from https://python.org
    echo Make sure to check "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [*] Python detected:
python --version

echo [*] Creating virtual environment 'venv' ...
if exist venv rd /s /q venv
python -m venv venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)

echo [*] Activating virtual environment...
call venv\Scripts\activate.bat

echo [*] Upgrading pip...
python -m pip install --upgrade pip

echo [*] Installing requirements.txt (this may take 2-4 minutes)...
pip install -r requirements.txt
if errorlevel 1 (
    echo [WARNING] Some dependencies failed to install.
    echo If smartapi-python had issues, the system will fall back to Yahoo Finance data.
)

if not exist ".env" (
    copy .env.example .env >nul
    echo [*] Created default .env config file.
)

echo [*] Running full test suite...
pytest tests\test_audit_fixes_and_strategies.py tests\test_valuation.py tests\test_desk_turnaround.py

echo [*] Running institutional verification script...
python scripts\verify_system.py

echo.
echo =====================================================================
echo  Installation COMPLETE!
echo.
echo  To run the system anytime:
echo    Double-click 'run_windows.bat' or run 'make.bat all'
echo =====================================================================
echo.
pause
