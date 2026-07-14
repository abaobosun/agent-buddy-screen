@echo off
setlocal
set PORT=8766

powershell -NoProfile -ExecutionPolicy Bypass -Command "$conns = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if (-not $conns) { Write-Host 'Agent Buddy is not running on port %PORT%.'; exit 0 }; foreach ($c in $conns) { $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -like 'python*') { Stop-Process -Id $p.Id -Force; Write-Host ('Stopped Agent Buddy process PID ' + $p.Id) } else { Write-Host ('Port %PORT% is owned by non-python process PID ' + $c.OwningProcess + '; not stopping it.'); exit 2 } }"

echo Done.
pause
