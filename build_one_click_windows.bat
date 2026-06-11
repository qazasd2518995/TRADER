@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================
echo   Build one-click Windows installers
echo ============================================
echo.

rem 用「實際能執行」來偵測 Python — py.exe 存在但指向已移除的安裝時 where 仍會成功
set "PY_CMD="
py --version >nul 2>&1 && set "PY_CMD=py"
if not defined PY_CMD python --version >nul 2>&1 && set "PY_CMD=python"

if not defined PY_CMD (
    echo [ERROR] 找不到可執行的 Python（py / python 都無法執行）
    echo 請先安裝 Python 3.10+，並勾選 Add Python to PATH。
    pause
    exit /b 1
)

echo Using Python command: %PY_CMD%
%PY_CMD% --version
echo.

echo [1/4] Installing build dependencies...
%PY_CMD% -m pip install --upgrade pip
%PY_CMD% -m pip install -r one_click_requirements_windows.txt
if errorlevel 1 goto failed

echo [2/4] Building central signal app...
%PY_CMD% -m PyInstaller --noconfirm packaging\pyinstaller\central-windows.spec
if errorlevel 1 goto failed

echo [3/4] Building member client app...
%PY_CMD% -m PyInstaller --noconfirm packaging\pyinstaller\client-windows.spec
if errorlevel 1 goto failed

echo [4/4] Building Inno Setup installers if ISCC exists...
where ISCC >nul 2>&1
if errorlevel 1 (
    echo [WARN] 找不到 Inno Setup ISCC，已輸出可直接執行資料夾：
    echo        dist\黃金訊號中心
    echo        dist\黃金跟單會員端
    echo 安裝 Inno Setup 後重新執行本檔，可產出 dist\installers\*.exe 安裝檔。
) else (
    ISCC packaging\inno\central-windows.iss
    if errorlevel 1 goto failed
    ISCC packaging\inno\client-windows.iss
    if errorlevel 1 goto failed
)

echo.
echo ============================================
echo   Done
echo ============================================
echo Output:
echo   dist\黃金訊號中心
echo   dist\黃金跟單會員端
echo   dist\installers
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Build failed.
pause
exit /b 1
