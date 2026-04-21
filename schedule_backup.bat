@echo off
:: ── Run this file ONCE as Administrator to register the daily 9 AM backup task ──

set "TASK_NAME=PM System Daily Backup"
set "BAT_FILE=C:\Users\mohammadhamzehCubesP\Downloads\Project Monitoring\backup.bat"

echo Registering scheduled task: "%TASK_NAME%"
echo Runs daily at 09:00 AM

schtasks /Create /F /TN "%TASK_NAME%" ^
    /TR "cmd /c \"%BAT_FILE%\"" ^
    /SC DAILY ^
    /ST 09:00 ^
    /RL HIGHEST ^
    /RU "%USERNAME%"

if errorlevel 1 (
    echo.
    echo ERROR: Failed to register task. Make sure you run this as Administrator.
) else (
    echo.
    echo SUCCESS! Task registered.
    echo The backup will run automatically every day at 09:00 AM.
    echo.
    echo To verify:  schtasks /Query /TN "%TASK_NAME%"
    echo To remove:  schtasks /Delete /TN "%TASK_NAME%" /F
    echo To run now: schtasks /Run /TN "%TASK_NAME%"
)

pause
