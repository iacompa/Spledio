@echo off
REM Create a virtual environment with the pinned dependencies (Windows; not yet tested on Windows).
cd /d "%~dp0\.."
py -3 -m venv .venv || python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pip install -e . --no-deps
echo Setup complete. Start the app with scripts\run.bat
