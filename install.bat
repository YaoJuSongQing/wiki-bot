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

echo Python found:
python --version
echo.

echo [1/3] Creating virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/3] Installing dependencies...
echo   This may take a few minutes...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Install failed. Check your internet connection.
    pause
    exit /b 1
)

echo [3/3] Done!
echo.
echo ============================================
echo   Setup complete!
echo   Copy config.example.yaml to config.yaml
echo   Edit config.yaml to set your API key
echo   Then double-click start.bat
echo ============================================
echo.
pause
