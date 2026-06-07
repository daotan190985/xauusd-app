#!/usr/bin/env bash
# ============================================================
#  TU DONG DAY CODE LEN GITHUB (Mac / Linux)
#  Chay:  bash deploy.sh
# ============================================================
cd "$(dirname "$0")" || exit 1

echo "============================================"
echo "  AUTO DEPLOY - Day code len GitHub"
echo "============================================"
echo

# 1) Kiem tra Git
if ! command -v git >/dev/null 2>&1; then
    echo "[LOI] Chua cai Git. Cai tai: https://git-scm.com/downloads"
    exit 1
fi

# 2) Lay link repo (luu lai cho lan sau)
CONF=".deploy_repo.txt"
if [ -f "$CONF" ]; then
    REPO_URL=$(cat "$CONF")
    echo "Repo da luu: $REPO_URL"
    read -p "Doi repo khac? (go link moi hoac Enter de giu nguyen): " CHANGE
    [ -n "$CHANGE" ] && REPO_URL="$CHANGE"
else
    echo "Lan dau deploy. Dan link repo GitHub (.git):"
    echo "Vi du: https://github.com/tendangnhap/xauusd-app.git"
    read -p "Dan link repo: " REPO_URL
fi

if [ -z "$REPO_URL" ]; then
    echo "[LOI] Chua co link repo."
    exit 1
fi
echo "$REPO_URL" > "$CONF"

# 3) .gitignore
cat > .gitignore << 'EOF'
__pycache__/
*.pyc
data/trade_history.csv
data/chart_images/
data/historical_charts/
.deploy_repo.txt
*.log
EOF

# 4) Khoi tao git
if [ ! -d ".git" ]; then
    echo "Khoi tao Git lan dau..."
    git init
    git branch -M main
    git remote add origin "$REPO_URL"
else
    git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL"
fi

# 5) Commit & push
echo "Dang day code len GitHub..."
git add -A
git commit -m "Cap nhat app $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "(Khong co thay doi moi)"

if ! git push -u origin main; then
    echo
    echo "[CHU Y] Push that bai. Kiem tra: link repo dung chua, da dang nhap GitHub chua,"
    echo "hoac thu: git pull origin main roi chay lai."
    exit 1
fi

echo
echo "============================================"
echo "  THANH CONG! Code da len GitHub."
echo "============================================"
echo "BUOC TIEP THEO (lam 1 lan dau):"
echo "  1. Vao https://share.streamlit.io -> Sign in with GitHub"
echo "  2. Create app -> chon repo -> Branch: main -> Main file: app.py"
echo "  3. Deploy -> co link .streamlit.app"
echo
echo "Nhung lan sau: chi chay lai file nay, app tren mang TU CAP NHAT!"
