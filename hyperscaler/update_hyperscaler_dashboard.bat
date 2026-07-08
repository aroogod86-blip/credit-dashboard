@echo off
REM ============================================================
REM update_hyperscaler_dashboard.bat
REM Daily data refresh + GitHub push for Hyperscaler Credit Dashboard
REM Run this file from the same folder as export_hyperscaler_dashboard.py
REM and hyperscaler_bond_universe.csv
REM ============================================================

cd /d %~dp0

echo [1/3] Pulling Bloomberg data...
python export_hyperscaler_dashboard.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Data pull failed. Check Bloomberg Terminal login status.
    pause
    exit /b 1
)

echo [2/3] Git commit / push...
git add data.json hyperscaler_history.json
if exist news-data.json git add news-data.json
git commit -m "daily update %date% %time%"
git push origin main

echo [3/3] Done. GitHub Pages will update within a minute or two.
pause
