@echo off
setlocal
rem personal-navigation quick launcher (ASCII only: cmd parses batch files in ANSI/GBK codepage)
rem usage:  pnw        start server silently via pythonw (NO window), then open browser
rem         pnw stop   stop the nav server only; tool backends are separate
rem                    hidden-console processes and keep running
rem
rem install: copy this file to "%USERPROFILE%\bin" (a PATH dir) and make sure
rem          PNW_DIR below points to your personal-navigation checkout.
set "PNW_DIR=X:\personal-navigation"
set "PNW_PYW=%PNW_DIR%\.venv\Scripts\pythonw.exe"
set "PNW_RUN=%PNW_DIR%\run_server.pyw"
set "PNW_LOG=%PNW_DIR%\logs\server.log"
if "%NAV_PORT%"=="" set "NAV_PORT=8790"
set "PNW_URL=http://127.0.0.1:%NAV_PORT%"

if /i "%~1"=="stop" goto :stop

curl -s -o NUL -m 2 "%PNW_URL%/api/health" >NUL 2>&1
if %errorlevel%==0 (
  echo [pnw] already running, opening browser: %PNW_URL%
  start "" "%PNW_URL%"
  exit /b 0
)

if not exist "%PNW_PYW%" (
  echo [pnw] venv pythonw not found: %PNW_PYW%
  echo [pnw] run %PNW_DIR%\start.bat once to create it.
  exit /b 1
)

echo [pnw] starting silently (pythonw, no window)...
start "" /b "%PNW_PYW%" "%PNW_RUN%"
rem wait up to ~15s for the health endpoint
set /a tries=0
:wait
ping -n 2 127.0.0.1 >NUL
curl -s -o NUL -m 2 "%PNW_URL%/api/health" >NUL 2>&1
if %errorlevel%==0 goto :up
set /a tries+=1
if %tries% lss 15 goto :wait
echo [pnw] server did not come up within 15s, check log: %PNW_LOG%
exit /b 1

:up
echo [pnw] started: %PNW_URL%
start "" "%PNW_URL%"
exit /b 0

:stop
set "FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":%NAV_PORT% " ^| findstr /C:"LISTENING"') do (
  set FOUND=1
  taskkill /PID %%a /F >NUL 2>&1 && echo [pnw] stopped server pid %%a
)
if not defined FOUND echo [pnw] not running
exit /b 0
