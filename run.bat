@echo off
rem Windows launcher: double-click to run against the real Allegro account.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo First run: setting up, takes a minute...
    python -m venv .venv || goto :fail
    .venv\Scripts\pip install -q -r requirements.txt || goto :fail
)
.venv\Scripts\python main.py --env prod --ask
echo.
pause
exit /b
:fail
echo Setup failed. Is Python installed and "Add to PATH" ticked?
pause
