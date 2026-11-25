@echo off
rem Start the Process & Resource Monitor on Windows.
rem The first run creates a private Python environment in .venv and installs psutil.
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first run, please wait...
    %PY% -m venv .venv || goto nopython
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto fail
)

if /i "%~1"=="--text" (
    ".venv\Scripts\python.exe" monitor.py
    pause
    exit /b 0
)

start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:nopython
echo Python 3.9 or newer is required.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1

:fail
echo Could not install psutil. Check your internet connection and run this file again.
rmdir /s /q .venv
pause
exit /b 1
