# =====================================================================
#  Indian Markets Trading Advisor — PowerShell Launcher (Windows 10/11)
# =====================================================================
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "   INDIAN MARKETS TRADING ADVISOR - ADVISORY TERMINAL" -ForegroundColor Green
Write-Host "   Autonomous Desk | 3Y Valuation Engine | Market News Catalyst" -ForegroundColor Gray
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify Python
try {
    $pyVersion = & python --version 2>&1
    Write-Host "[*] Python: $pyVersion" -ForegroundColor Gray
} catch {
    Write-Error "Python is not installed or not in PATH! Please install Python 3.10+ from python.org"
    exit 1
}

# 2. Check Virtual Environment
$venvPath = Join-Path $PSScriptRoot "venv"
$activateScript = Join-Path $venvPath "Scripts\Activate.ps1"

if (-not (Test-Path $activateScript)) {
    Write-Host "[*] Creating virtual environment at .\venv ..." -ForegroundColor Yellow
    & python -m venv venv
    & (Join-Path $venvPath "Scripts\pip.exe") install --upgrade pip
    & (Join-Path $venvPath "Scripts\pip.exe") install -r requirements.txt
}

# 3. Ensure .env
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "[*] Created .env from .env.example" -ForegroundColor Gray
    }
}

# 4. Activate Venv
& $activateScript

# 5. Run Verification
Write-Host "[*] Verifying system integrity & permanent advisory locks..." -ForegroundColor Cyan
& python scripts\verify_system.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] System verification failed. Cannot proceed." -ForegroundColor Red
    exit 1
}

# 6. Launch processes
Write-Host "[*] Launching Backend Server + Schedulers..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; .\venv\Scripts\Activate.ps1; python scripts\run_system.py"

Start-Sleep -Seconds 3

Write-Host "[*] Launching Autonomous Trading Desk Daemon..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; .\venv\Scripts\Activate.ps1; python scripts\autonomous_trading_desk.py"

# 7. Open Browser
Start-Process "http://localhost:8000"

Write-Host ""
Write-Host "Trading Advisor is RUNNING! Web terminal opened at http://localhost:8000" -ForegroundColor Green
