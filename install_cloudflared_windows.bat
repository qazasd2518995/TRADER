@echo off
chcp 65001 >nul
setlocal

set "TARGET_DIR=%APPDATA%\黃金跟單系統"
set "TARGET=%TARGET_DIR%\cloudflared.exe"
set "URL=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

echo ============================================
echo   Install Cloudflare Tunnel for 黃金訊號中心
echo ============================================
echo.

where powershell >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 找不到 PowerShell，無法自動下載 cloudflared。
    pause
    exit /b 1
)

if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

echo Downloading:
echo   %URL%
echo To:
echo   %TARGET%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%URL%' -OutFile '%TARGET%'"
if errorlevel 1 goto failed

if not exist "%TARGET%" goto failed

echo.
echo Done.
echo 黃金訊號中心會自動找到：
echo   %TARGET%
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] cloudflared 下載失敗。
echo 也可以手動安裝：winget install --id Cloudflare.cloudflared
echo.
pause
exit /b 1
