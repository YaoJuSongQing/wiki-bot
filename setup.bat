@echo off
title WikiBot Setup
cd /d "%~dp0"

echo ============================================
echo   WikiBot Setup
echo ============================================
echo.

REM Check if Python exists
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python --version
    echo Python found, skipping download...
    goto :install
)

REM Download embeddable Python
echo Python not found. Downloading Python 3.12...
echo.
echo This may take 1-2 minutes...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip' -OutFile 'python.zip'"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Download failed. Please install Python manually:
    echo   https://www.python.org/downloads/
    echo   Check "Add Python to PATH" during install
    pause
    exit /b 1
)

echo Extracting Python...
powershell -Command "Expand-Archive -Force -Path 'python.zip' -DestinationPath 'python'"
del python.zip

REM Enable pip in embeddable Python
echo import site>> python\python312._pth

REM Setup
:install
echo.
echo [1/3] Creating virtual environment...
if exist "python\python.exe" (
    python\python.exe -m venv venv
) else (
    python -m venv venv
)
call venv\Scripts\activate.bat

echo [2/3] Installing dependencies...
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

echo [3/3] Done!
echo.
echo ============================================
echo   Setup complete!
echo.
echo   1. Copy config.example.yaml to config.yaml
echo   2. Edit config.yaml to set your API key
echo   3. Double-click start.bat
echo ============================================
echo.
pause
