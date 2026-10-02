@echo off
setlocal
rem personal-navigation boot autostart installer (ASCII only)
rem registers a logon scheduled task that starts the server silently via pythonw.
rem no admin rights needed: onlogon task for the current user.
set "PNW_DIR=%~dp0"
set "PNW_PYW=%PNW_DIR%.venv\Scripts\pythonw.exe"
set "PNW_RUN=%PNW_DIR%run_server.pyw"

if not exist "%PNW_PYW%" (
  echo [pnw-autostart] venv not found, run start.bat once first.
  exit /b 1
)

schtasks /query /tn PersonalNav >NUL 2>&1
if %errorlevel%==0 schtasks /delete /tn PersonalNav /f >NUL 2>&1

schtasks /create /f /tn PersonalNav /sc onlogon /rl limited /tr "\"%PNW_PYW%\" \"%PNW_RUN%\""
if errorlevel 1 (
  echo [pnw-autostart] schtasks failed, falling back to HKCU Run key.
  reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PersonalNav /t REG_SZ /d "\"%PNW_PYW%\" \"%PNW_RUN%\"" /f
  if errorlevel 1 (
    echo [pnw-autostart] both methods failed.
    exit /b 1
  )
  echo [pnw-autostart] registered via HKCU Run key.
) else (
  echo [pnw-autostart] registered as scheduled task "PersonalNav" (runs at logon, no window^).
)
echo [pnw-autostart] to remove, run: "%PNW_DIR%uninstall_autostart.cmd"
exit /b 0
