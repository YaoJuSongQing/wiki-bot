@echo off
title WikiBot
cd /d "%~dp0"

echo ============================================
echo   WikiBot Starting...
echo ============================================

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] Not installed. Run install.bat first.
    pause
    exit /b 1
)

python server.py

echo.
echo Server stopped.
pause
