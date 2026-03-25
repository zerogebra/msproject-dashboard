@echo off
cd /d "C:\Users\mohammadhamzehCubesP\Downloads\Project Monitoring"
echo Starting PRISM PMO Server...
py -3.11 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause