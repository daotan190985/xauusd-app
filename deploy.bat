@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  TU DONG DAY CODE LEN GITHUB - chi can NHAY DUP file nay
REM  Auto-push to GitHub. Lan dau hoi link repo, cac lan sau chi nhap dup.
REM ============================================================
cd /d "%~dp0"

echo ============================================
echo   AUTO DEPLOY - Day code len GitHub
echo ============================================
echo.

REM ----- 1) Kiem tra Git -----
git --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Chua cai Git.
    echo Hay tai Git tai: https://git-scm.com/download/win
    echo Cai xong, chay lai file nay.
    echo.
    pause
    exit /b
)

REM ----- 2) Lay link repo (luu lai cho lan sau) -----
set "CONF=.deploy_repo.txt"
if exist "%CONF%" (
    set /p REPO_URL=<"%CONF%"
    echo Repo da luu: !REPO_URL!
    echo.
    set /p CHANGE="Doi repo khac? (go link moi hoac Enter de giu nguyen): "
    if not "!CHANGE!"=="" set "REPO_URL=!CHANGE!"
) else (
    echo Lan dau deploy. Hay dan link repo GitHub cua anh.
    echo Vi du: https://github.com/tendangnhap/xauusd-app.git
    echo.
    echo  ^> Neu chua co repo: vao github.com -^> New repository
    echo    -^> dat ten -^> Create -^> copy link ket thuc bang .git
    echo.
    set /p REPO_URL="Dan link repo (.git): "
)

if "!REPO_URL!"=="" (
    echo [LOI] Chua co link repo. Dung lai.
    pause
    exit /b
)
REM Luu link cho lan sau
echo !REPO_URL!>"%CONF%"

REM ----- 3) Tao .gitignore (khong day file rac / du lieu ca nhan) -----
(
echo __pycache__/
echo *.pyc
echo data/trade_history.csv
echo data/chart_images/
echo data/historical_charts/
echo .deploy_repo.txt
echo *.log
) > .gitignore

REM ----- 4) Khoi tao git neu chua co -----
if not exist ".git" (
    echo.
    echo Khoi tao Git lan dau...
    git init
    git branch -M main
    git remote add origin !REPO_URL!
) else (
    REM Cap nhat remote phong khi doi repo
    git remote set-url origin !REPO_URL! 2>nul || git remote add origin !REPO_URL!
)

REM ----- 5) Commit & push -----
echo.
echo Dang day code len GitHub...
git add -A
set "MSG=Cap nhat app %date% %time%"
git commit -m "!MSG!" 2>nul
if errorlevel 1 (
    echo (Khong co thay doi moi de commit - van thu push^)
)

git push -u origin main
if errorlevel 1 (
    echo.
    echo ============================================
    echo [CHU Y] Push that bai. Thuong do 1 trong cac ly do:
    echo   1. Sai link repo, hoac repo chua ton tai tren GitHub.
    echo   2. Chua dang nhap GitHub - cua so dang nhap se hien ra,
    echo      hay dang nhap roi chay lai file nay.
    echo   3. Repo da co code khac - thu lenh:  git pull origin main
    echo ============================================
    echo.
    pause
    exit /b
)

echo.
echo ============================================
echo   THANH CONG! Code da len GitHub.
echo ============================================
echo.
echo BUOC TIEP THEO (chi lam 1 LAN DAU):
echo   1. Vao: https://share.streamlit.io
echo   2. Sign in with GitHub
echo   3. Create app -^> chon repo nay -^> Branch: main -^> Main file: app.py
echo   4. Bam Deploy -^> doi vai phut -^> co link .streamlit.app
echo.
echo NHUNG LAN SAU: chi can nhap dup file nay, app tren mang TU CAP NHAT!
echo.
pause
