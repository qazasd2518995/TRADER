@echo off
chcp 65001 >nul
echo ============================================
echo   Copy Trader - Windows 安裝
echo ============================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [錯誤] 找不到 Python！
    echo 下載: https://www.python.org/downloads/
    echo 安裝時勾選 "Add Python to PATH"
    pause
    exit /b 1
)

echo [1/3] 建立虛擬環境...
python -m venv venv
if %errorlevel% neq 0 (
    echo [錯誤] 建立虛擬環境失敗
    pause
    exit /b 1
)

echo [2/3] 啟動虛擬環境...
call venv\Scripts\activate.bat

echo [3/3] 安裝套件...
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ============================================
echo   安裝完成！
echo ============================================
echo.
echo 還需要:
echo   1. 安裝 Tesseract-OCR (中文辨識):
echo      https://github.com/UB-Mannheim/tesseract/wiki
echo      選擇 chi_tra + chi_sim 語言包
echo.
echo   2. 確保 MT5 已安裝並登入，Bridge EA 已掛載
echo.
echo   3. 如用 LLM 解析，設定環境變數:
echo      set GROQ_API_KEY=your-key
echo      set ANTHROPIC_API_KEY=your-key
echo.
echo 啟動: 雙擊 run.bat
echo.
pause
