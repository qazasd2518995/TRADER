@echo off
echo ============================================
echo  黃金跟單系統 - Tauri Build
echo ============================================

cd /d "%~dp0"

echo.
echo [1/3] Building Python sidecar...
call build_sidecar.bat
if errorlevel 1 (
    echo FAILED: Sidecar build failed
    exit /b 1
)

echo.
echo [2/3] Copying sidecar binary...
if not exist "src-tauri\binaries" mkdir "src-tauri\binaries"
copy /y "dist\copy-trader-sidecar.exe" "src-tauri\binaries\copy-trader-sidecar-x86_64-pc-windows-msvc.exe"
if errorlevel 1 (
    echo FAILED: Could not copy sidecar binary
    exit /b 1
)
echo Sidecar binary placed in src-tauri/binaries/

echo.
echo [3/3] Building Tauri application...
npm run tauri build
if errorlevel 1 (
    echo FAILED: Tauri build failed
    exit /b 1
)

echo.
echo ============================================
echo  BUILD COMPLETE
echo  Output: src-tauri\target\release\bundle\
echo ============================================
