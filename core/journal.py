"""
core/journal.py
===============
Nhật ký giao dịch: lưu/đọc lịch sử, upload ảnh, lưu ảnh biểu đồ lịch sử,
và thống kê hiệu quả phương pháp.
Trade journal: CSV persistence, image uploads, historical chart snapshots, stats.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Đường dẫn lưu trữ / Storage paths
# ----------------------------------------------------------------------------
DATA_DIR = Path("data")
TRADE_CSV = DATA_DIR / "trade_history.csv"
CHART_IMG_DIR = DATA_DIR / "chart_images"          # ảnh upload khi log
HIST_CHART_DIR = DATA_DIR / "historical_charts"    # ảnh biểu đồ tại thời điểm log

# Các phương pháp vào lệnh chuẩn / canonical entry methods
ENTRY_METHODS = [
    "BB Lower Hold (%B then chốt)",
    "BB Upper Hold (%B then chốt)",
    "MACD Histogram trên 0 (xu hướng tăng)",
    "MACD Histogram dưới 0 (xu hướng giảm)",
    "MACD Histogram tách khỏi Signal",
    "ADX <25 + DI Cross",
    "EMA Alignment",
    "Stoch + BB",
    "RSI Confirmation",
    "Multi-TF Confluence cao",
]

# Cột của trade_history.csv / schema
COLUMNS = [
    "id", "timestamp", "chart_timestamp", "ticker", "interval",
    "direction", "methods_used", "entry_price", "sl", "tp",
    "confidence", "chart_image_path", "hist_chart_path",
    "notes", "outcome", "pnl_pips",
]


def ensure_dirs() -> None:
    """Đảm bảo các thư mục lưu trữ tồn tại."""
    for d in (DATA_DIR, CHART_IMG_DIR, HIST_CHART_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_history() -> pd.DataFrame:
    """Đọc toàn bộ lịch sử giao dịch. Trả về DataFrame rỗng đúng schema nếu chưa có."""
    ensure_dirs()
    if not TRADE_CSV.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(TRADE_CSV)
        # Bổ sung cột thiếu (khi nâng cấp schema)
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = None
        return df[COLUMNS]
    except Exception as exc:  # noqa: BLE001
        logger.error("Lỗi đọc lịch sử: %s", exc)
        return pd.DataFrame(columns=COLUMNS)


def save_trade(trade: dict) -> str:
    """
    Lưu một giao dịch mới (hoặc cập nhật nếu trùng id).
    Trả về id của giao dịch.
    """
    ensure_dirs()
    df = load_history()

    trade = {**{c: None for c in COLUMNS}, **trade}
    if not trade.get("id"):
        trade["id"] = uuid.uuid4().hex[:12]
    # methods_used lưu dạng chuỗi ngăn cách "|"
    if isinstance(trade.get("methods_used"), (list, tuple)):
        trade["methods_used"] = "|".join(trade["methods_used"])

    # Cập nhật nếu id đã tồn tại
    if not df.empty and trade["id"] in df["id"].values:
        idx = df.index[df["id"] == trade["id"]][0]
        for c in COLUMNS:
            df.at[idx, c] = trade.get(c)
    else:
        df = pd.concat([df, pd.DataFrame([trade])[COLUMNS]], ignore_index=True)

    df.to_csv(TRADE_CSV, index=False)
    logger.info("Đã lưu giao dịch id=%s", trade["id"])
    return trade["id"]


def delete_trade(trade_id: str) -> bool:
    """Xóa một giao dịch theo id."""
    df = load_history()
    if df.empty or trade_id not in df["id"].values:
        return False
    df = df[df["id"] != trade_id]
    df.to_csv(TRADE_CSV, index=False)
    return True


def save_uploaded_image(uploaded_file, trade_id: str) -> Optional[str]:
    """
    Lưu ảnh người dùng upload vào data/chart_images/.
    Trả về đường dẫn tương đối hoặc None.
    """
    if uploaded_file is None:
        return None
    ensure_dirs()
    ext = Path(uploaded_file.name).suffix or ".png"
    path = CHART_IMG_DIR / f"{trade_id}_upload{ext}"
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    logger.info("Đã lưu ảnh upload: %s", path)
    return str(path)


def save_historical_chart(fig, trade_id: str) -> Optional[str]:
    """
    Lưu ảnh biểu đồ Plotly tại thời điểm log vào data/historical_charts/.
    Yêu cầu kaleido để export PNG; nếu lỗi thì trả về None (không chặn luồng).
    """
    if fig is None:
        return None
    ensure_dirs()
    path = HIST_CHART_DIR / f"{trade_id}_hist.png"
    try:
        fig.write_image(str(path), width=1200, height=900, scale=1)
        logger.info("Đã lưu biểu đồ lịch sử: %s", path)
        return str(path)
    except Exception as exc:  # noqa: BLE001 - kaleido có thể chưa cài
        logger.warning("Không thể lưu ảnh biểu đồ (cần kaleido): %s", exc)
        return None


def parse_methods(methods_str) -> list[str]:
    """Chuyển chuỗi methods_used 'a|b|c' -> list."""
    if not methods_str or pd.isna(methods_str):
        return []
    return [m for m in str(methods_str).split("|") if m]


# ----------------------------------------------------------------------------
# THỐNG KÊ / Statistics
# ----------------------------------------------------------------------------
def filter_by_period(df: pd.DataFrame, period: str = "all") -> pd.DataFrame:
    """Lọc theo 'week' (7 ngày), 'month' (30 ngày), hoặc 'all'."""
    if df.empty or period == "all":
        return df
    df = df.copy()
    df["_ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    now = pd.Timestamp.now()
    days = 7 if period == "week" else 30
    cutoff = now - pd.Timedelta(days=days)
    return df[df["_ts"] >= cutoff].drop(columns=["_ts"])


def compute_stats(df: pd.DataFrame) -> dict:
    """
    Tính thống kê tổng quan.
    Trả về: tổng lệnh, winrate, số lệnh chắc thắng (confidence Cao & win).
    """
    if df.empty:
        return {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                "high_conf_wins": 0}

    closed = df[df["outcome"].isin(["WIN", "LOSS"])]
    total = len(closed)
    wins = int((closed["outcome"] == "WIN").sum())
    losses = int((closed["outcome"] == "LOSS").sum())
    winrate = (wins / total * 100) if total else 0.0
    high_conf_wins = int(
        ((closed["outcome"] == "WIN") & (closed["confidence"] == "Cao")).sum()
    )
    return {
        "total": total, "wins": wins, "losses": losses,
        "winrate": round(winrate, 1), "high_conf_wins": high_conf_wins,
    }


def method_performance(df: pd.DataFrame) -> pd.DataFrame:
    """
    Winrate theo từng phương pháp.
    Trả về DataFrame: method, trades, wins, winrate (giảm dần).
    """
    if df.empty:
        return pd.DataFrame(columns=["method", "trades", "wins", "winrate"])

    closed = df[df["outcome"].isin(["WIN", "LOSS"])]
    rows = []
    for method in ENTRY_METHODS:
        mask = closed["methods_used"].apply(
            lambda s: method in parse_methods(s)
        )
        sub = closed[mask]
        if len(sub) == 0:
            continue
        wins = int((sub["outcome"] == "WIN").sum())
        rows.append({
            "method": method,
            "trades": len(sub),
            "wins": wins,
            "winrate": round(wins / len(sub) * 100, 1),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("winrate", ascending=False).reset_index(drop=True)
    return out


# ============================================================================
# LƯU LỊCH SỬ BÁO CÁO ĐÁNH GIÁ PHƯƠNG PHÁP (file JSON nhẹ)
# ============================================================================
REPORT_JSON = DATA_DIR / "method_reports.json"


def save_method_report(report: dict) -> bool:
    """Lưu 1 báo cáo đánh giá vào lịch sử (JSON). Giữ tối đa 50 báo cáo gần nhất."""
    import json
    ensure_dirs()
    try:
        reports = load_method_reports()
        reports.insert(0, report)  # mới nhất lên đầu
        reports = reports[:50]
        with open(REPORT_JSON, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.warning("Lưu báo cáo lỗi: %s", e)
        return False


def load_method_reports() -> list:
    """Đọc danh sách báo cáo đã lưu (mới nhất trước)."""
    import json
    if not REPORT_JSON.exists():
        return []
    try:
        with open(REPORT_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def delete_method_report(created_at: str) -> bool:
    """Xóa 1 báo cáo theo thời gian tạo."""
    import json
    try:
        reports = [r for r in load_method_reports() if r.get("created_at") != created_at]
        with open(REPORT_JSON, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
