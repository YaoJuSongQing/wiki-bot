@echo off
chcp 65001 >nul
title WikiBot 更新
cd /d "%~dp0"

echo ============================================
echo   WikiBot 自动更新
echo ============================================
echo.

REM Check git
where git >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未安装 Git，请先安装 https://git-scm.com/
    pause
    exit /b 1
)

echo 正在从 GitHub 拉取最新版本...
echo.

REM Stash local changes (protects config.yaml)
git stash 2>nul
git pull origin main
git stash pop 2>nul

echo.
echo ============================================
echo   更新完成！
echo   如果服务正在运行，按 Ctrl+C 后重新启动
echo ============================================
pause
