@echo off
setlocal
cd /d "%~dp0"

set HOST=0.0.0.0
set PORT=8766
set OUTLOG=%~dp0agent-buddy.out.log
set ERRLOG=%~dp0agent-buddy.err.log

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 10 }"
if %ERRORLEVEL%==10 (
  echo Agent Buddy is already running on port %PORT%.
  echo Local: http://127.0.0.1:%PORT%/
  echo LAN:   http://192.168.3.100:%PORT%/
  echo Portrait: http://192.168.3.100:%PORT%/portrait
  timeout /t 2 /nobreak >nul
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = '%~dp0'; $out = Join-Path $root 'agent-buddy.out.log'; $err = Join-Path $root 'agent-buddy.err.log'; $args = @('%~dp0server.py','--host','%HOST%','--port','%PORT%'); Start-Process -FilePath 'pythonw.exe' -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err"
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = '%~dp0'; $out = Join-Path $root 'agent-buddy.out.log'; $err = Join-Path $root 'agent-buddy.err.log'; $args = @('%~dp0server.py','--host','%HOST%','--port','%PORT%'); Start-Process -FilePath 'python.exe' -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err"
)

timeout /t 2 /nobreak >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  echo Failed to start Agent Buddy. See agent-buddy.err.log.
  pause
  exit /b 1
)

echo Agent Buddy started in background.
echo Local: http://127.0.0.1:%PORT%/
echo LAN:   http://192.168.3.100:%PORT%/
echo Portrait: http://192.168.3.100:%PORT%/portrait
timeout /t 2 /nobreak >nul
exit /b 0
