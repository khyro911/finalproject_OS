@echo off
title Process ^& Resource Monitor
rem Start the Process & Resource Monitor on Windows.
rem The first run creates a private Python environment in .venv and installs psutil.
rem Right-click this file and choose "Run as administrator" to manage system processes.
cd /d "%~dp0"

if not exist "app.py" (
    echo app.py was not found next to run.bat.
    echo Extract the ZIP first: right-click it, Extract All, then run run.bat from the extracted folder.
    goto end_error
)

rem ---- find Python ----
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto nopython

rem ---- first-run setup, or repair a broken setup ----
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import psutil" >nul 2>nul || rmdir /s /q .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first run, please wait...
    %PY% -m venv .venv
    if errorlevel 1 goto novenv
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto nopip
)

".venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul || goto notk

if /i "%~1"=="--text" (
    ".venv\Scripts\python.exe" monitor.py
    goto end_ok
)

echo Monitor is running. Closing this window also closes the monitor.
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto end_error
goto end_ok

:nopython
echo Python was not found.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" on the first installer screen.
goto end_error

:novenv
echo Could not create the .venv folder.
echo If this folder is inside "Program Files" or a ZIP, move it to Documents or Desktop and try again.
goto end_error

:nopip
echo Could not install psutil. Check your internet connection and run this file again.
rmdir /s /q .venv 2>nul
goto end_error

:notk
echo Your Python is missing Tkinter (the window library).
echo Re-run the Python installer, choose Modify, and tick "tcl/tk and IDLE".
goto end_error

:end_error
echo.
echo Something went wrong. Take a screenshot of this window.
pause
exit /b 1

:end_ok
exit /b 0
