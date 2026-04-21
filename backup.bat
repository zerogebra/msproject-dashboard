@echo off
setlocal enabledelayedexpansion

:: ── CONFIG ──────────────────────────────────────────────────────────────────
set "SOURCE=C:\Users\mohammadhamzehCubesP\Downloads\Project Monitoring"
set "DEST_ROOT=D:\backup pm system"

:: ── Get ISO date and time via PowerShell (works on all Windows 10/11) ────────
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATESTR=%%i"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set "TIMESTR=%%i"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format HH:mm:ss"') do set "TIMEDISP=%%i"

set "DEST=!DEST_ROOT!\!DATESTR!"

:: If folder already exists today, append time so we don't overwrite
if exist "!DEST!" (
    set "DEST=!DEST_ROOT!\!DATESTR!_!TIMESTR!"
)

:: ── WAL checkpoint — flush SQLite WAL to prism.db before copy ───────────────
echo [1/3] Flushing SQLite WAL...
cd /d "!SOURCE!"
python3.11 -c "from app.database import get_conn; conn=get_conn(); conn.execute('PRAGMA wal_checkpoint(FULL)'); print('  WAL flushed')" 2>nul
if errorlevel 1 (
    python -c "from app.database import get_conn; conn=get_conn(); conn.execute('PRAGMA wal_checkpoint(FULL)'); print('  WAL flushed')" 2>nul
)

:: ── Create destination folders ───────────────────────────────────────────────
echo [2/3] Creating backup folder: !DEST!
mkdir "!DEST!\data" 2>nul

:: ── Copy files ───────────────────────────────────────────────────────────────
echo [3/3] Copying files...

xcopy /E /I /Y /Q "!SOURCE!\app"             "!DEST!\app\"
xcopy /E /I /Y /Q "!SOURCE!\static"          "!DEST!\static\"
copy  /Y          "!SOURCE!\data\prism.db"   "!DEST!\data\prism.db" >nul
if exist "!SOURCE!\requirements.txt"  copy /Y "!SOURCE!\requirements.txt"  "!DEST!\" >nul
if exist "!SOURCE!\render.yaml"       copy /Y "!SOURCE!\render.yaml"        "!DEST!\" >nul
if exist "!SOURCE!\Procfile"          copy /Y "!SOURCE!\Procfile"           "!DEST!\" >nul

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo ============================================================
echo  Backup complete!
echo  Location : !DEST!
echo  Date/Time: !DATESTR! !TIMEDISP!
echo ============================================================
echo.

endlocal
