@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   DAY LEN - Day code len GitHub
echo ============================================
echo.

REM Lay ban moi nhat truoc de tranh xung dot
echo [1/4] Lay ban moi nhat ve truoc...
git pull
echo.

echo [2/4] Gom tat ca file thay doi...
git add -A
echo.

echo [3/4] Luu lai (commit)...
REM Tu dat message theo ngay gio
set "MSG=Cap nhat %date% %time%"
git commit -m "%MSG%"
echo.

echo [4/4] Day len GitHub...
git push
echo.

echo ============================================
echo   XONG! Code da len GitHub.
echo   Web Streamlit se tu cap nhat sau vai phut.
echo ============================================
echo.
pause
