@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================
echo   Build Windows central signal app only
echo ============================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 找不到 Python launcher py.exe
    echo 請先安裝 Python 3.10+，並勾選 Add Python to PATH。
    pause
    exit /b 1
)

echo [1/3] Installing build dependencies...
py -m pip install --upgrade pip
py -m pip install -r one_click_requirements_windows.txt
if errorlevel 1 goto failed

echo [2/3] Building central signal app...
py -m PyInstaller --noconfirm packaging\pyinstaller\central-windows.spec
if errorlevel 1 goto failed

echo [3/3] Building installer if Inno Setup exists...
where ISCC >nul 2>&1
if errorlevel 1 (
    echo [WARN] 找不到 Inno Setup ISCC，已輸出可直接執行資料夾：
    echo        dist\黃金訊號中心
    echo 安裝 Inno Setup 後重新執行本檔，可產出 dist\installers\黃金訊號中心_安裝檔.exe。
) else (
    ISCC packaging\inno\central-windows.iss
    if errorlevel 1 goto failed
)

echo.
echo ============================================
echo   Done
echo ============================================
echo Output:
echo   dist\黃金訊號中心
echo   dist\installers\黃金訊號中心_安裝檔.exe
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Build failed.
pause
exit /b 1
