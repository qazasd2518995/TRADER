@echo off
echo === Building Copy Trader Sidecar ===
cd /d "%~dp0"

REM Use venv if available
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Install PyInstaller if needed
pip show pyinstaller >nul 2>&1 || pip install pyinstaller

REM Build sidecar executable using spec file
pyinstaller --noconfirm copy-trader-sidecar.spec

echo.
if exist "dist\copy-trader-sidecar.exe" (
    echo SUCCESS: dist\copy-trader-sidecar.exe
) else (
    echo FAILED: Build did not produce exe
    exit /b 1
)
