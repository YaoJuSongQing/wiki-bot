@echo off
title WikiBot Update
cd /d "%~dp0"

echo ============================================
echo   WikiBot Update
echo ============================================
echo.

echo [1/3] Backing up config...
copy /y "config.yaml" "%TEMP%\wikibot_config.yaml" >nul 2>&1

echo [2/3] Downloading latest version...
cd ..
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/YaoJuSongQing/wiki-bot/archive/refs/heads/main.zip' -OutFile 'wikibot_update.zip'"
powershell -Command "Expand-Archive -Force -Path 'wikibot_update.zip' -DestinationPath '.'"
del "wikibot_update.zip"

echo [3/3] Updating files...
xcopy /e /y /q "wiki-bot-main\*.py" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\*.txt" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\*.bat" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\config.example.yaml" "WikiBot\"
if exist "wiki-bot-main\VERSION" copy /y "wiki-bot-main\VERSION" "WikiBot\" >nul
rmdir /s /q "wiki-bot-main"

cd WikiBot
if exist "%TEMP%\wikibot_config.yaml" (
    copy /y "%TEMP%\wikibot_config.yaml" "config.yaml" >nul
)

echo.
echo ============================================
echo   Update complete!
echo   Restart the server if it's running
echo ============================================
pause
