@echo off
chcp 65001 >nul
echo ============================================
echo   Copy Trader - 自動跟單 + 馬丁格爾
echo ============================================
echo.

if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo [錯誤] 請先執行 install.bat
    pause
    exit /b 1
)

cd copy_trader
python app.py
cd ..
pause
