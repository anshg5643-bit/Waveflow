@echo off
echo Starting WaveFlow on http://localhost:8000 ...
echo Press Ctrl+C to stop.
echo.
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
pause
