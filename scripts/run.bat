@echo off
REM Launch Spledio (Windows; not yet tested on Windows).
cd /d "%~dp0\.."
.venv\Scripts\python -m spledio %*
