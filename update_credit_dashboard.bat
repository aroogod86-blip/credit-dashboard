@echo off
REM ============================================================
REM update_credit_dashboard.bat
REM Daily data refresh for IG Credit Spread Dashboard
REM export_credit_dashboard.py handles git commit/push internally.
REM Run this file from the same folder as export_credit_dashboard.py
REM ============================================================

cd /d %~dp0

echo [1/1] Pulling Bloomberg data and updating dashboard...
python export_credit_dashboard.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Update failed. Check Bloomberg Terminal login status.
    pause
    exit /b 1
)

echo Done. GitHub Pages will update within a minute or two.
pause
