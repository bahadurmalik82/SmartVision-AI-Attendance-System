@echo off
REM ============================================================
REM  Attenova — PRODUCTION launcher (Windows, double-click to run)
REM
REM  Production = the Waitress server (a real web server), NOT the
REM  Flask development server. There is no debugger and no
REM  "Debugger PIN" printed, and the app does not reload on code
REM  changes. Use this when you show the project to anyone.
REM
REM  Daily work still uses:  python run.py
REM  (development mode: debugger on, auto-reload, laptop only.)
REM ============================================================
cd /d "%~dp0"
set FLASK_ENV=production
set HOST=0.0.0.0

if not exist venv\Scripts\activate.bat (
  echo Virtual environment not found in this folder.
  echo Create it first:
  echo   python -m venv venv
  echo   venv\Scripts\activate
  echo   pip install -r requirements.txt
  pause
  exit /b 1
)
call venv\Scripts\activate
python run.py
echo.
echo The app has stopped. Press any key to close this window.
pause
