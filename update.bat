@echo off
title WikiBot Update
cd /d "%~dp0"

echo ============================================
echo   WikiBot Update
echo ============================================
echo.

echo [1/4] Backing up config...
copy /y "config.yaml" "%TEMP%\wikibot_config.yaml" >nul 2>&1

echo [2/4] Downloading latest version...
cd ..
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/YaoJuSongQing/wiki-bot/archive/refs/heads/main.zip' -OutFile 'wikibot_update.zip' } catch { Write-Host 'Download failed! Manually download:' -ForegroundColor Red; Write-Host '  https://github.com/YaoJuSongQing/wiki-bot/archive/refs/heads/main.zip' -ForegroundColor Yellow; Write-Host 'Extract it and copy files into WikiBot folder.' -ForegroundColor Yellow; exit 1 }"
if not exist "wikibot_update.zip" (
    echo [ERROR] Download failed. Check the manual link above.
    pause
    exit /b 1
)
powershell -Command "Expand-Archive -Force -Path 'wikibot_update.zip' -DestinationPath '.'"
del "wikibot_update.zip"

echo [3/4] Updating files...
xcopy /e /y /q "wiki-bot-main\*.py" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\*.txt" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\*.bat" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\config.example.yaml" "WikiBot\"
if exist "wiki-bot-main\VERSION" copy /y "wiki-bot-main\VERSION" "WikiBot\" >nul
REM Data files (.json) are NOT synced — users scrape their own wikis
rmdir /s /q "wiki-bot-main"

cd WikiBot
if exist "%TEMP%\wikibot_config.yaml" (
    copy /y "%TEMP%\wikibot_config.yaml" "config.yaml" >nul
)

echo [4/4] Updating dependencies...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    echo   No venv found, skipping dependency update
)

echo.
echo ============================================
echo   Update complete!
echo   Restart the server if it's running
echo ============================================
pause
