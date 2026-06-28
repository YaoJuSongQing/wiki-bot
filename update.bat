@echo off
title WikiBot 更新
cd /d "%~dp0"

echo ============================================
echo   WikiBot 自动更新
echo ============================================
echo.

REM 备份 config.yaml
echo [1/3] 备份配置...
copy /y "config.yaml" "%TEMP%\wikibot_config.yaml" >nul 2>&1

REM 下载最新版
echo [2/3] 下载最新版本...
cd ..
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/YaoJuSongQing/wiki-bot/archive/refs/heads/main.zip' -OutFile 'wikibot_update.zip'"
powershell -Command "Expand-Archive -Force -Path 'wikibot_update.zip' -DestinationPath '.'"
del "wikibot_update.zip"

REM 只覆盖代码，保留 data 和 venv
echo [3/3] 更新代码...
xcopy /e /y /q "wiki-bot-main\*.py" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\*.txt" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\*.bat" "WikiBot\"
xcopy /e /y /q "wiki-bot-main\config.example.yaml" "WikiBot\"
if exist "wiki-bot-main\VERSION" copy /y "wiki-bot-main\VERSION" "WikiBot\" >nul
rmdir /s /q "wiki-bot-main"

REM 恢复配置
cd WikiBot
if exist "%TEMP%\wikibot_config.yaml" (
    copy /y "%TEMP%\wikibot_config.yaml" "config.yaml" >nul
)

echo.
echo ============================================
echo   更新完成！
echo   如果服务正在运行，按 Ctrl+C 后重新启动
echo ============================================
pause
