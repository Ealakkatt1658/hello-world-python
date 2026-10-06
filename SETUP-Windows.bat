@echo off
rem Double-click once: installs everything and asks the questions for your profile.
cd /d "%~dp0"
set PY=
py -3 --version >nul 2>nul && set PY=py -3
if not defined PY python --version >nul 2>nul && set PY=python
if not defined PY (
  echo Python isn't installed. Opening the download page...
  echo Install it, tick "Add python.exe to PATH" on the first screen, then double-click this file again.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Installing (this takes a few minutes the first time)...
if not exist .venv %PY% -m venv .venv || goto failed
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || goto failed
python -m playwright install chromium || goto failed
python -m job_autofill --setup
echo.
echo Done. To apply to a job, double-click APPLY-Windows.bat
pause
exit /b 0
:failed
echo.
echo Something went wrong above. Take a screenshot of this window and send it to Claude.
pause
exit /b 1
