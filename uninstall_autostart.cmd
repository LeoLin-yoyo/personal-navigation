@echo off
rem remove personal-navigation boot autostart (ASCII only)
schtasks /query /tn PersonalNav >NUL 2>&1
if %errorlevel%==0 (
  schtasks /delete /tn PersonalNav /f >NUL 2>&1 && echo [pnw-autostart] scheduled task "PersonalNav" removed.
)
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PersonalNav /f >NUL 2>&1
if %errorlevel%==0 echo [pnw-autostart] HKCU Run key removed.
echo [pnw-autostart] done.
exit /b 0
