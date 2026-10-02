@echo off
setlocal
cd /d "%~dp0"
if "%NAV_PORT%"=="" set NAV_PORT=8790

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment ...
  python -m venv .venv || goto :err
  echo [setup] installing dependencies ...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)

start "" /min cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:%NAV_PORT%"

echo Personal Nav running at http://127.0.0.1:%NAV_PORT%  (Ctrl+C to stop)
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %NAV_PORT%
goto :eof

:err
echo [error] setup failed, see messages above.
pause
