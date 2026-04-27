@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "ROOT=%~dp0"
set "LOG=%ROOT%build_signal_center_windows.log"

call :main > "%LOG%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

type "%LOG%"
echo.
if "%EXIT_CODE%"=="0" (
    echo [OK] Build completed.
) else (
    echo [ERROR] Build failed. 請把下面這個 log 檔內容傳回來：
    echo %LOG%
)
echo.
pause
exit /b %EXIT_CODE%

:main
cd /d "%ROOT%"

echo ============================================
echo   Build Windows central signal app only
echo ============================================
echo.

set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py"
if not defined PY_CMD where python >nul 2>&1 && set "PY_CMD=python"

if not defined PY_CMD (
    echo [ERROR] 找不到 Python。
    echo 請先安裝 Python 3.10+，並勾選 Add Python to PATH。
    echo 下載：https://www.python.org/downloads/windows/
    exit /b 1
)

echo Using Python command: %PY_CMD%
%PY_CMD% --version
echo.

echo [1/3] Installing build dependencies...
%PY_CMD% -m pip install --upgrade pip
%PY_CMD% -m pip install -r one_click_requirements_windows.txt
if errorlevel 1 goto failed

echo [2/3] Building central signal app...
%PY_CMD% -m PyInstaller --noconfirm packaging\pyinstaller\central-windows.spec
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
exit /b 0

:failed
echo.
echo [ERROR] Build failed.
exit /b 1
