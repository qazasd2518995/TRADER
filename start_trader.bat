@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title Copy Trader - Live Log
venv\Scripts\python.exe -m copy_trader.app
pause
