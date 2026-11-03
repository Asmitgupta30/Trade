@echo off
REM Windows Institutional Terminal & Makefile Helper for Indian Markets Trading Advisor
setlocal enabledelayedexpansion

if "%1"=="" goto help
if /i "%1"=="help" goto help
if /i "%1"=="install" goto install
if /i "%1"=="verify" goto verify
if /i "%1"=="test" goto test
if /i "%1"=="verify-live" goto verify_live
if /i "%1"=="serve" goto serve
if /i "%1"=="run" goto run
if /i "%1"=="desk" goto desk
if /i "%1"=="all" goto run_all
if /i "%1"=="clean" goto clean

:install
echo [make] Installing dependencies from requirements.txt...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist .env (
    copy .env.example .env
    echo [make] Created .env from .env.example
)
goto :eof

:verify
echo [make] Running test suite and system integrity checks...
python -m pytest
if errorlevel 1 exit /b 1
python scripts\verify_system.py
goto :eof

:test
python -m pytest %2 %3 %4 %5
goto :eof

:verify_live
python -m pytest -o "addopts=-m live --timeout 300 -v"
goto :eof

:serve
echo [make] Starting FastAPI backend on http://127.0.0.1:8000 ...
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
goto :eof

:run
echo [make] Starting full Trading Advisor system (Streamer + Backend + Schedulers)...
python scripts\run_system.py
goto :eof

:desk
echo [make] Starting Autonomous Turnaround Trading Desk daemon...
python scripts\autonomous_trading_desk.py
goto :eof

:run_all
echo [make] Verifying system and launching Trading Advisor...
python scripts\verify_system.py
if errorlevel 1 exit /b 1
echo [make] Starting backend server in separate window...
start "Trading Advisor - Backend" python scripts\run_system.py
timeout /t 3 /nobreak >nul
echo [make] Starting autonomous trading desk in separate window...
start "Trading Advisor - Desk Daemon" python scripts\autonomous_trading_desk.py
start http://localhost:8000
goto :eof

:clean
echo [make] Cleaning temporary build caches...
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
if exist .pytest_cache rd /s /q .pytest_cache
goto :eof

:help
echo ======================================================================
echo  Indian Markets Trading Advisor - Windows Command Utility
echo ======================================================================
echo  make.bat install       - Install required Python dependencies
echo  make.bat verify        - Run full test suite and verify system locks
echo  make.bat test          - Run pytest test suite
echo  make.bat run           - Launch backend server, data streamer and schedulers
echo  make.bat desk          - Launch autonomous paper-trading desk daemon
echo  make.bat all           - Verify system and launch both backend + desk
echo  make.bat serve         - Launch standalone FastAPI uvicorn server
echo  make.bat clean         - Remove pycache and temporary test caches
echo ======================================================================
goto :eof
