@echo off
REM ============================================================
REM  CHAY TU DONG APP XAU/USD - chi can NHAY DUP vao file nay
REM  One-click launcher for Windows
REM ============================================================
cd /d "%~dp0"

echo ============================================
echo   XAU/USD Trading Journal - Dang khoi dong
echo ============================================
echo.

REM Kiem tra Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Chua cai Python. Hay tai tai https://www.python.org/downloads/
    echo Nho tick "Add Python to PATH" khi cai dat.
    pause
    exit /b
)

REM Cai thu vien lan dau (neu thieu se tu cai, co roi thi bo qua nhanh)
echo Dang kiem tra / cai dat thu vien...
python -m pip install -r requirements.txt --quiet

echo.
echo Mo trinh duyet tai dia chi: http://localhost:8501
echo De TAT app: dong cua so nay hoac bam Ctrl + C
echo.

REM Tu dong mo trinh duyet sau 3 giay (app can vai giay de khoi dong)
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8501"

REM Chay app (--server.headless=false de uu tien tu mo tren may local)
python -m streamlit run app.py --server.headless=false

pause
