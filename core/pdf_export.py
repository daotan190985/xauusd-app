"""
core/pdf_export.py
==================
Xuất báo cáo PDF chuyên nghiệp từ lịch sử giao dịch.
Professional PDF report export from trade journal.

Sử dụng reportlab (platypus). Báo cáo gồm:
- Bảng tổng kết
- Danh sách trade + phương pháp + ghi chú
- Chèn ảnh biểu đồ đã lưu
- Chú thích phương pháp dùng cho từng lệnh
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.journal import compute_stats, method_performance, parse_methods

logger = logging.getLogger(__name__)


def _styles():
    """Tạo bộ style, hỗ trợ font Unicode cho tiếng Việt nếu có DejaVuSans."""
    styles = getSampleStyleSheet()
    font_name = "Helvetica"
    # Thử đăng ký DejaVuSans (hỗ trợ tiếng Việt) nếu tồn tại trên hệ thống
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        if os.path.exists(candidates[0]):
            pdfmetrics.registerFont(TTFont("DejaVu", candidates[0]))
            if os.path.exists(candidates[1]):
                pdfmetrics.registerFont(TTFont("DejaVu-Bold", candidates[1]))
            font_name = "DejaVu"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đăng ký được font Unicode: %s", exc)

    styles.add(ParagraphStyle(
        "VTitle", fontName=font_name, fontSize=18, spaceAfter=12,
        textColor=colors.HexColor("#B8860B"),
    ))
    styles.add(ParagraphStyle(
        "VHead", fontName=font_name, fontSize=13, spaceAfter=6,
        textColor=colors.HexColor("#333333"),
    ))
    styles.add(ParagraphStyle("VBody", fontName=font_name, fontSize=9, leading=13))
    return styles, font_name


def export_report(
    df: pd.DataFrame,
    output_path: str = "data/report.pdf",
    period_label: str = "Toàn bộ",
) -> str:
    """
    Sinh file PDF báo cáo từ DataFrame các trade.
    Trả về đường dẫn file PDF.
    """
    styles, font_name = _styles()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    elements = []

    # --- Tiêu đề ---
    elements.append(Paragraph("BÁO CÁO GIAO DỊCH XAU/USD", styles["VTitle"]))
    elements.append(Paragraph(
        f"Khoảng thời gian: {period_label} &nbsp;|&nbsp; "
        f"Xuất ngày: {datetime.now():%d/%m/%Y %H:%M}",
        styles["VBody"],
    ))
    elements.append(Spacer(1, 0.5 * cm))

    # --- Bảng tổng kết ---
    stats = compute_stats(df)
    elements.append(Paragraph("1. Tổng kết", styles["VHead"]))
    summary_data = [
        ["Tổng lệnh (đã đóng)", "Thắng", "Thua", "Winrate", "Lệnh chắc thắng"],
        [str(stats["total"]), str(stats["wins"]), str(stats["losses"]),
         f"{stats['winrate']}%", str(stats["high_conf_wins"])],
    ]
    t = Table(summary_data, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B8860B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.4 * cm))

    # --- Top phương pháp ---
    perf = method_performance(df)
    if not perf.empty:
        elements.append(Paragraph("2. Hiệu quả theo phương pháp", styles["VHead"]))
        perf_data = [["Phương pháp", "Số lệnh", "Thắng", "Winrate"]]
        for _, r in perf.iterrows():
            perf_data.append([r["method"], str(r["trades"]),
                              str(r["wins"]), f"{r['winrate']}%"])
        pt = Table(perf_data, hAlign="LEFT", colWidths=[7 * cm, 2.5 * cm, 2.5 * cm, 3 * cm])
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#444444")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F5F5F5")]),
        ]))
        elements.append(pt)
        elements.append(Spacer(1, 0.4 * cm))

    # --- Danh sách trade chi tiết ---
    elements.append(Paragraph("3. Chi tiết các lệnh", styles["VHead"]))
    for _, row in df.iterrows():
        methods = ", ".join(parse_methods(row.get("methods_used"))) or "—"
        block = (
            f"<b>#{row.get('id')}</b> &nbsp; {row.get('timestamp')} &nbsp; "
            f"<b>{row.get('direction')}</b> &nbsp; "
            f"(Độ chắc chắn: {row.get('confidence')}, Kết quả: {row.get('outcome') or 'Đang mở'})<br/>"
            f"<b>Phương pháp:</b> {methods}<br/>"
            f"<b>Entry:</b> {row.get('entry_price')} &nbsp; "
            f"<b>SL:</b> {row.get('sl')} &nbsp; <b>TP:</b> {row.get('tp')} &nbsp; "
            f"<b>PnL:</b> {row.get('pnl_pips')} pips<br/>"
            f"<b>Ghi chú:</b> {row.get('notes') or '—'}"
        )
        elements.append(Paragraph(block, styles["VBody"]))
        elements.append(Spacer(1, 0.15 * cm))

        # Chèn ảnh biểu đồ (ưu tiên ảnh lịch sử, sau đó ảnh upload)
        for img_key in ("hist_chart_path", "chart_image_path"):
            p = row.get(img_key)
            if p and isinstance(p, str) and os.path.exists(p):
                try:
                    elements.append(Image(p, width=15 * cm, height=11 * cm))
                    elements.append(Spacer(1, 0.3 * cm))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Không chèn được ảnh %s: %s", p, exc)
                break
        elements.append(Spacer(1, 0.3 * cm))

    doc.build(elements)
    logger.info("Đã xuất PDF: %s", output_path)
    return output_path
