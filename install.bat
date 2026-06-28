@echo off
title WikiBot Setup
cd /d "%~dp0"

echo ============================================
echo   WikiBot - Setup
echo ============================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found.
    echo   Download: https://www.python.org/downloads/
    echo   Check "Add Python to PATH" during install
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/3] Installing dependencies...
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

echo [3/3] Done!
echo.
echo ============================================
echo   Setup complete!
echo   Copy config.example.yaml to config.yaml
echo   Edit config.yaml to set your API key
echo   Then double-click start.bat
echo ============================================
echo.
echo Open config.example.yaml now?
set /p OPEN="Type y to open, anything else to skip: "
if /i "%OPEN%"=="y" notepad config.example.yaml
pause
