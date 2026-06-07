#!/usr/bin/env bash
# ============================================================
#  CHAY TU DONG APP XAU/USD - cho Mac / Linux
#  Chay bang lenh:  bash run.sh   (hoac nhay dup sau khi chmod +x)
# ============================================================
cd "$(dirname "$0")" || exit 1

echo "============================================"
echo "  XAU/USD Trading Journal - Dang khoi dong"
echo "============================================"
echo

# Kiem tra Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "[LOI] Chua cai Python3. Cai tai https://www.python.org/downloads/"
    exit 1
fi

# Cai thu vien (lan dau se cai, lan sau bo qua nhanh)
echo "Dang kiem tra / cai dat thu vien..."
python3 -m pip install -r requirements.txt --quiet

echo
echo "Mo trinh duyet tai: http://localhost:8501"
echo "De TAT app: bam Ctrl + C"
echo

# Chay app
python3 -m streamlit run app.py --server.headless=false
