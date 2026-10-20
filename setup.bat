@echo off
setlocal enabledelayedexpansion
echo ============================================================
echo  WaveFlow - Windows Setup
echo ============================================================

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://python.org
    pause & exit /b 1
)
echo [OK] Python found

:: Install deps
echo.
echo Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 ( echo ERROR: pip install failed & pause & exit /b 1 )
echo [OK] Dependencies installed

:: Create .env if missing
if not exist .env (
    copy .env.example .env
    echo.
    echo [!] .env file created from template.
    echo     Open .env in Notepad and fill in:
    echo       GROQ_API_KEY   = your Groq key  (https://console.groq.com/keys)
    echo       GOOGLE_CLIENT_ID = your Google OAuth client ID
    echo       JWT_SECRET     = any long random string
    echo.
    echo     Then run  start.bat  to launch the server.
    notepad .env
) else (
    echo [OK] .env already exists
)

:: Train ML model
echo.
echo Training the stealth-check ML model (takes about 60 seconds)...
python train_detector.py
if errorlevel 1 ( echo ERROR: training failed & pause & exit /b 1 )
echo [OK] ML model trained

echo.
echo ============================================================
echo  Setup complete!  Run  start.bat  to launch WaveFlow.
echo ============================================================
pause
