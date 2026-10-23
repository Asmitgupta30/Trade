# Robust server launcher: kills stale instances, retries until healthy,
# logs outside OneDrive. Usage: powershell -File scripts\server_robust.ps1

$log = "C:\trade_logs\server.log"
$root = "C:\Users\asmit\OneDrive\Desktop\trade"

# 1) clear any stale instance (pid file + port)
$statusFile = Join-Path $root "var\status\backend_server.json"
if (Test-Path $statusFile) {
    try {
        $old = (Get-Content $statusFile | ConvertFrom-Json).pid
        if ($old) { taskkill /PID $old /T /F 2>&1 | Out-Null }
    } catch {}
}
$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($c) { taskkill /PID $c.OwningProcess /T /F 2>&1 | Out-Null }
Get-Process python3.12 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Remove-Item $log -ErrorAction SilentlyContinue

# 2) up to 3 attempts: launch detached, poll health up to 150s each
for ($attempt = 1; $attempt -le 3; $attempt++) {
    Write-Output "=== attempt $attempt ==="
    $p = Start-Process -FilePath "python" `
        -ArgumentList "$root\scripts\run_system.py" `
        -WorkingDirectory $root `
        -RedirectStandardOutput "C:\trade_logs\server_out.log" `
        -RedirectStandardError "C:\trade_logs\server_err.log" `
        -PassThru -WindowStyle Hidden
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 5
        if ($p.HasExited) { Write-Output "process exited early"; break }
        try {
            $h = Invoke-RestMethod "http://127.0.0.1:8000/api/health" -TimeoutSec 4
            if ($h.status -eq "ok") { $healthy = $true; break }
        } catch {}
    }
    if ($healthy) {
        Write-Output "SERVER HEALTHY on attempt $attempt (pid $($p.Id))"
        exit 0
    }
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
}
Write-Output "FAILED after 3 attempts - see $log"
exit 1
