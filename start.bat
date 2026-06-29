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

echo Starting server (takes ~30s first time)...

REM Fix SSL + HuggingFace mirror for Windows users
for /f "delims=" %%i in ('venv\Scripts\python.exe -c "import certifi; print(certifi.where())" 2^>nul') do set SSL_CERT_FILE=%%i
set HF_ENDPOINT=https://hf-mirror.com

start "WikiBot Server" /min cmd /c "cd /d %~dp0 && set SSL_CERT_FILE=%SSL_CERT_FILE% && set HF_ENDPOINT=%HF_ENDPOINT% && venv\Scripts\python.exe server.py"

echo Waiting for server to be ready...
:wait
timeout /t 3 /nobreak >nul
powershell -Command "try {$r=Invoke-WebRequest -Uri 'http://localhost:8080' -TimeoutSec 2; exit 0} catch {exit 1}" >nul 2>&1
if errorlevel 1 goto wait

start "" http://localhost:8080
echo.
echo Browser opened at http://localhost:8080
echo Close the server window to stop.
pause
