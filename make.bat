@echo off
REM Windows equivalent of the Makefile targets.
if "%1"=="" goto help
if /i "%1"=="test" (python -m pytest & goto :eof)
if /i "%1"=="verify" (python -m pytest && python scripts\verify_system.py & goto :eof)
if /i "%1"=="verify-live" (python -m pytest -o "addopts=-m live --timeout 300 -v" & goto :eof)
if /i "%1"=="serve" (python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 & goto :eof)
if /i "%1"=="run" (python scripts\run_system.py & goto :eof)
:help
echo usage: make.bat [test^|verify^|verify-live^|serve^|run]
