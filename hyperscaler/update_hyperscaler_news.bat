@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "REPO=C:\Users\Hana_FI\Desktop\credit-dashboard"
set "DOWNLOADS=C:\Users\Hana_FI\Downloads"
set "TARGET=%REPO%\hyperscaler\news-data.json"

echo.
echo ============================================
echo   Hyperscaler News Update
echo ============================================
echo.

REM Downloads 폴더에서 news-data 로 시작하는 가장 최근 json 파일 찾기
set "LATEST="
for /f "delims=" %%f in ('dir /b /o-d "%DOWNLOADS%\news-data*.json" 2^>nul') do (
    if not defined LATEST set "LATEST=%%f"
)

if not defined LATEST (
    echo   ERROR: Downloads 폴더에서 news-data*.json 파일을 찾을 수 없습니다.
    echo   먼저 채팅에서 "하이퍼스케일러 뉴스"를 실행하고 파일을 다운로드하세요.
    echo.
    pause
    exit /b 1
)

echo   찾은 파일: %LATEST%
echo.

copy /y "%DOWNLOADS%\%LATEST%" "%TARGET%" >nul

if errorlevel 1 (
    echo   ERROR: 파일 복사에 실패했습니다. 경로를 확인하세요.
    pause
    exit /b 1
)

echo   복사 완료: hyperscaler\news-data.json
echo.
echo ============================================
echo   Pushing to GitHub...
echo ============================================
echo.

cd /d "%REPO%"
git add hyperscaler/news-data.json

for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set TODAY=%%a-%%b-%%c
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set NOW=%%a:%%b

git commit -m "hyperscaler news update %TODAY% %NOW%"
git push

echo.
echo ============================================
echo   Done! Press any key to close.
echo ============================================
pause
