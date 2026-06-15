@echo off
title PriceGuard AI Server
cd /d "C:\price alert"
set TELEGRAM_BOT_TOKEN=8935357738:AAGL7L0tuXBcPd47iIPczv-JUvpGYiyuru4
echo ==========================================================
echo           Starting PriceGuard AI Price Tracker
echo ==========================================================
echo.
echo Launching dashboard in your default browser...
start "" http://127.0.0.1:8000
echo.
echo Starting backend server process...
.\venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000
pause
