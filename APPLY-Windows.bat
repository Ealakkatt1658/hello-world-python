@echo off
rem Double-click to apply: it asks for the job link, then fills in the application.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run SETUP-Windows.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m job_autofill %*
pause
