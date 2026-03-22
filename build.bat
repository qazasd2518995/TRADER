@echo off
chcp 65001 >nul
echo ====================================
echo   黃金跟單系統 - 打包建置
echo ====================================
echo.

REM 啟動虛擬環境
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [WARNING] 未找到虛擬環境，使用系統 Python
)

REM 確認 PyInstaller 已安裝
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [INFO] 安裝 PyInstaller...
    pip install pyinstaller
)

REM 執行打包
echo [INFO] 開始打包...
python -m PyInstaller build.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] 打包失敗！
    pause
    exit /b 1
)

echo.
echo ====================================
echo   打包完成！
echo   輸出位置: dist\黃金跟單系統\
echo ====================================
echo.
pause
