@echo off
:: Run this if you accidentally opened a terminal inside a nested folder
cd /d "%~dp0"
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
pause
