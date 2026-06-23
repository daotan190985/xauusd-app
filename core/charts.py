"""
core/charts.py
==============
Tạo biểu đồ Plotly động cho mỗi khung thời gian, hỗ trợ BẬT/TẮT indicator.
Dynamic Plotly chart builder with per-indicator toggles (NOT cached — saves RAM).

Phong cách gần OANDA: sạch, giá bên phải, MACD histogram nổi bật, thoáng,
zoom/pan mượt (scrollZoom + dragmode='pan'), tốt trên mobile.

Số hàng (panel phụ) được tính ĐỘNG theo các indicator được bật:
  - Hàng giá (luôn có): Candlestick + EMA + BB tùy chọn
  - %B, ADX+DI, MACD, Stoch+RSI: chỉ thêm hàng khi được bật
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.analyzer import detect_band_hold

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Bảng màu nhất quán (tông OANDA: nền tối, đường thanh thoát)
# ----------------------------------------------------------------------------
C_UP = "#089981"          # nến tăng (xanh OANDA-ish)
C_DOWN = "#F23645"        # nến giảm (đỏ)
C_EMA100 = "#2962FF"
C_EMA200 = "#FF9800"
C_EMA1200 = "#AB47BC"
C_BB = "rgba(120,144,156,0.45)"
C_PCTB = "#FFD54F"
C_ADX = "#5C6BC0"
C_DIP = "#26A69A"
C_DIM = "#EF5350"
C_STOCHK = "#29B6F6"
C_STOCHD = "#EC407A"
C_RSI = "#FFB300"
C_MACD = "#2962FF"
C_MACD_SIG = "#FF6D00"
C_HIST_UP = "#089981"     # histogram > 0 — đậm, nổi bật
C_HIST_DOWN = "#F23645"   # histogram < 0
C_KC = "rgba(255,167,38,0.5)"  # Keltner Channel (cam nhạt)


# ----------------------------------------------------------------------------
# Cấu hình bật/tắt indicator / Indicator visibility flags
# ----------------------------------------------------------------------------
@dataclass
class ChartOptions:
    """
    Cờ bật/tắt từng indicator. Mặc định chỉ hiện những thứ quan trọng
    để biểu đồ không rối khi mới mở (OANDA-style: sạch).
    """
    ema100: bool = True
    ema200: bool = True
    ema1200: bool = False        # tắt mặc định cho thoáng
    bollinger: bool = True
    keltner: bool = False        # Keltner Channel — tắt mặc định cho thoáng
    percent_b: bool = False      # panel phụ — tắt mặc định
    adx: bool = False            # panel phụ — tắt mặc định
    macd: bool = True            # MACD là xu hướng chính -> bật
    stoch_rsi: bool = False      # panel phụ — tắt mặc định
    scale: str = "linear"        # linear / log / percent
    lock_scale: bool = False     # True = khóa trục Y (không auto-fit khi pan/zoom)

    # MÀU TÙY CHỈNH (mặc định = bảng màu chuẩn). Đổi màu -> vẽ lại ngay.
    col_ema100: str = C_EMA100
    col_ema200: str = C_EMA200
    col_ema1200: str = C_EMA1200
    col_bb: str = "#7890a0"
    col_kc: str = "#FFA726"
    col_macd: str = C_MACD
    col_macd_sig: str = C_MACD_SIG
    col_hist_up: str = C_HIST_UP
    col_hist_down: str = C_HIST_DOWN
    col_dip: str = C_DIP
    col_dim: str = C_DIM
    col_adx: str = C_ADX
    col_stochk: str = C_STOCHK
    col_stochd: str = C_STOCHD
    col_rsi: str = C_RSI

    @staticmethod
    def from_selection(
        selected: list[str], scale: str = "linear", lock_scale: bool = False,
        colors: Optional[dict] = None,
    ) -> "ChartOptions":
        """Tạo ChartOptions từ danh sách tên indicator được chọn (multiselect)."""
        s = set(selected)
        opt = ChartOptions(
            ema100="EMA 100" in s,
            ema200="EMA 200" in s,
            ema1200="EMA 1200" in s,
            bollinger="Bollinger Bands" in s,
            keltner="Keltner Channel" in s,
            percent_b="%B" in s,
            adx="ADX + DI" in s,
            macd="MACD" in s,
            stoch_rsi="Stochastic + RSI" in s,
            scale=scale,
            lock_scale=lock_scale,
        )
        if colors:
            for k, v in colors.items():
                if hasattr(opt, k) and v:
                    setattr(opt, k, v)
        return opt


# Tên indicator cho UI multiselect + mặc định bật
ALL_INDICATORS = [
    "EMA 100", "EMA 200", "EMA 1200", "Bollinger Bands", "Keltner Channel",
    "%B", "ADX + DI", "MACD", "Stochastic + RSI",
]
DEFAULT_INDICATORS = ["EMA 100", "EMA 200", "Bollinger Bands", "MACD"]


def build_full_chart(
    df: pd.DataFrame,
    title: str = "",
    options: Optional[ChartOptions] = None,
    height: Optional[int] = None,
    max_bars: int = 180,
) -> Optional[go.Figure]:
    """
    Dựng figure Plotly ĐỘNG theo các indicator được bật trong `options`.

    max_bars: chỉ hiển thị N nến gần nhất cho dễ nhìn (mặc định 180).
              Người dùng vẫn pan ngược lại để xem lịch sử cũ hơn.
              Chỉ báo vẫn được tính trên TOÀN BỘ dữ liệu trước khi cắt hiển thị.

    LƯU Ý: KHÔNG cache figure — luôn tái tạo khi cần (tiết kiệm RAM).
    Trả về None nếu df rỗng.
    """
    if df is None or df.empty:
        return None
    opt = options or ChartOptions()

    # Cắt hiển thị N nến gần nhất (chỉ báo đã tính sẵn trên full df) -> đỡ bị nén
    if max_bars and len(df) > max_bars:
        df = df.tail(max_bars)

    # --- Xác định các panel phụ cần vẽ (ngoài hàng giá) ---
    sub_panels = []
    if opt.percent_b:
        sub_panels.append("pctb")
    if opt.adx:
        sub_panels.append("adx")
    if opt.macd:
        sub_panels.append("macd")
    if opt.stoch_rsi:
        sub_panels.append("stoch")

    n_rows = 1 + len(sub_panels)

    # Tỉ lệ chiều cao: hàng giá lớn, panel phụ nhỏ gọn (thoáng như OANDA)
    if n_rows == 1:
        row_heights = [1.0]
    else:
        price_h = 0.52
        each = (1 - price_h) / len(sub_panels)
        row_heights = [price_h] + [each] * len(sub_panels)

    # Tiêu đề panel
    titles = ["Giá"]
    panel_title = {
        "pctb": "%B (then chốt)",
        "adx": "ADX + DI",
        "macd": "MACD (24,52,14)",
        "stoch": "Stochastic + RSI",
    }
    titles += [panel_title[p] for p in sub_panels]

    fig = make_subplots(
        rows=n_rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=row_heights,
        subplot_titles=titles,
    )

    # Làm nhỏ + canh trái các tiêu đề panel để không đè vào legend/giá
    for ann in fig.layout.annotations:
        ann.font = dict(size=11, color="#8b95a5")
        ann.x = 0.01
        ann.xanchor = "left"

    # Map panel -> số hàng
    row_of = {"price": 1}
    for i, p in enumerate(sub_panels, start=2):
        row_of[p] = i

    # ---- HÀNG GIÁ: Candlestick + EMA + BB ----
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            name="Giá", increasing_line_color=C_UP, decreasing_line_color=C_DOWN,
            increasing_fillcolor=C_UP, decreasing_fillcolor=C_DOWN,
            showlegend=False,
        ),
        row=1, col=1,
    )
    # ---- ĐƯỜNG GIÁ HIỆN TẠI (giá cây nến cuối) — dễ nhìn, nổi bật ----
    last_close = float(df["Close"].iloc[-1])
    last_open = float(df["Open"].iloc[-1])
    # màu theo nến tăng/giảm
    price_color = C_UP if last_close >= last_open else C_DOWN
    # số chữ số thập phân hợp lý theo độ lớn giá (vàng/btc khác forex)
    if last_close >= 1000:
        price_txt = f"{last_close:,.2f}"
    elif last_close >= 10:
        price_txt = f"{last_close:.3f}"
    else:
        price_txt = f"{last_close:.5f}"
    fig.add_hline(
        y=last_close,
        line=dict(color=price_color, width=1.2, dash="dot"),
        annotation_text=f"  ● {price_txt}  ",
        annotation_position="right",
        annotation_font=dict(size=14, color="#ffffff"),
        annotation_bgcolor=price_color,
        row=1, col=1,
    )

    ema_map = [
        (opt.ema100, "EMA_100", opt.col_ema100, "EMA 100"),
        (opt.ema200, "EMA_200", opt.col_ema200, "EMA 200"),
        (opt.ema1200, "EMA_1200", opt.col_ema1200, "EMA 1200"),
    ]
    for show, col, color, name in ema_map:
        if show and col in df:
            fig.add_trace(
                go.Scatter(x=df.index, y=df[col], name=name,
                           line=dict(color=color, width=1.3)),
                row=1, col=1,
            )
    if opt.bollinger and "BB_UP" in df and "BB_LOW" in df:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["BB_UP"], name="BB Upper",
                       line=dict(color=opt.col_bb, width=1), showlegend=False),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["BB_LOW"], name="BB Lower",
                       line=dict(color=opt.col_bb, width=1), fill="tonexty",
                       fillcolor="rgba(120,144,156,0.06)", showlegend=False),
            row=1, col=1,
        )
    if opt.keltner and "KC_UP" in df and "KC_LOW" in df:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["KC_UP"], name="KC Upper",
                       line=dict(color=opt.col_kc, width=1, dash="dot"),
                       showlegend=False),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["KC_LOW"], name="Keltner",
                       line=dict(color=opt.col_kc, width=1, dash="dot")),
            row=1, col=1,
        )

    # ---- PANEL %B ----
    if "pctb" in row_of and "PCT_B" in df:
        r = row_of["pctb"]
        fig.add_trace(
            go.Scatter(x=df.index, y=df["PCT_B"], name="%B",
                       line=dict(color=C_PCTB, width=2)),
            row=r, col=1,
        )
        for yv, dash in [(1.0, "dot"), (0.5, "dash"), (0.0, "dot")]:
            fig.add_hline(y=yv, line=dict(color="gray", width=0.8, dash=dash),
                          row=r, col=1)
        band = detect_band_hold(df)
        if band["lower_hold"] or band["upper_hold"]:
            fig.add_annotation(
                x=df.index[-1], y=df["PCT_B"].iloc[-1], text="THEN CHỐT ✓",
                showarrow=True, arrowhead=2, arrowcolor=C_PCTB,
                font=dict(color="#000", size=11),
                bgcolor=C_PCTB, bordercolor="#000", borderwidth=1,
                row=r, col=1,
            )

    # ---- PANEL ADX + DI ----
    if "adx" in row_of:
        r = row_of["adx"]
        for col, color, name in [
            ("ADX", opt.col_adx, "ADX"), ("DI_PLUS", opt.col_dip, "DI+"),
            ("DI_MINUS", opt.col_dim, "DI-"),
        ]:
            if col in df:
                fig.add_trace(
                    go.Scatter(x=df.index, y=df[col], name=name,
                               line=dict(color=color, width=1.4)),
                    row=r, col=1,
                )
        fig.add_hline(y=25, line=dict(color="white", width=1, dash="dash"),
                      row=r, col=1)

    # ---- PANEL MACD (histogram nổi bật trên/dưới 0) ----
    if "macd" in row_of:
        r = row_of["macd"]
        if "MACD_HIST" in df:
            hist_colors = [
                opt.col_hist_up if (pd.notna(v) and v >= 0) else opt.col_hist_down
                for v in df["MACD_HIST"]
            ]
            fig.add_trace(
                go.Bar(x=df.index, y=df["MACD_HIST"], name="Histogram",
                       marker_color=hist_colors, marker_line_width=0,
                       showlegend=False),
                row=r, col=1,
            )
        if "MACD" in df:
            fig.add_trace(
                go.Scatter(x=df.index, y=df["MACD"], name="MACD",
                           line=dict(color=opt.col_macd, width=1.5)),
                row=r, col=1,
            )
        if "MACD_SIGNAL" in df:
            fig.add_trace(
                go.Scatter(x=df.index, y=df["MACD_SIGNAL"], name="Signal",
                           line=dict(color=opt.col_macd_sig, width=1.5)),
                row=r, col=1,
            )
        fig.add_hline(y=0, line=dict(color="gray", width=0.9, dash="dash"),
                      row=r, col=1)

    # ---- PANEL Stochastic + RSI ----
    if "stoch" in row_of:
        r = row_of["stoch"]
        for col, color, name in [
            ("STOCH_K", opt.col_stochk, "Stoch %K"), ("STOCH_D", opt.col_stochd, "Stoch %D"),
            ("RSI", opt.col_rsi, "RSI"),
        ]:
            if col in df:
                fig.add_trace(
                    go.Scatter(x=df.index, y=df[col], name=name,
                               line=dict(color=color, width=1.2)),
                    row=r, col=1,
                )
        for yv in (20, 80):
            fig.add_hline(y=yv, line=dict(color="gray", width=0.7, dash="dot"),
                          row=r, col=1)

    # ---- LAYOUT (sạch, giá bên phải, tự khít màn hình như OANDA) ----
    auto_h = height or (420 + 210 * len(sub_panels))
    fig.update_layout(
        # Tên khung đưa vào góc trái trên (không đè vào subplot title)
        title=dict(text=title, font=dict(size=13, color="#c9d1d9"),
                   x=0.01, xanchor="left", y=0.995, yanchor="top"),
        autosize=True,                # TỰ KHỚP chiều rộng theo khung màn hình
        height=auto_h,
        template="plotly_dark",
        paper_bgcolor="#131722",
        plot_bgcolor="#131722",
        xaxis_rangeslider_visible=False,
        dragmode="pan",
        # Legend đặt NGAY DƯỚI tiêu đề, canh phải, nhỏ gọn -> không đè chữ "Giá"
        legend=dict(orientation="h", yanchor="bottom", y=1.005,
                    xanchor="right", x=1, font=dict(size=9),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=8, r=62, t=64, b=28),   # t lớn hơn để chừa chỗ tiêu đề + legend
        hovermode="x unified",
        bargap=0.12,
    )

    # Giá nằm BÊN PHẢI, rõ ràng; lưới mảnh, thoáng
    for i in range(1, n_rows + 1):
        fig.update_yaxes(side="right", showgrid=True,
                         gridcolor="rgba(255,255,255,0.05)",
                         zeroline=False, row=i, col=1)
        fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                         row=i, col=1)

    # Trục THỜI GIAN dễ nhìn: chỉ hiện ở hàng dưới cùng, định dạng giờ:phút,
    # bỏ khoảng trống cuối tuần (rangebreaks) để nến liền mạch.
    # Vàng/forex nghỉ T7+CN -> ẩn 2 ngày này cho biểu đồ liền mạch.
    rb = [dict(bounds=["sat", "mon"])]  # ẩn từ thứ 7 đến hết CN
    for i in range(1, n_rows + 1):
        fig.update_xaxes(rangebreaks=rb, row=i, col=1)
    fig.update_xaxes(
        showticklabels=False, row=1, col=1,  # ẩn nhãn ở hàng giá cho gọn
    )
    fig.update_xaxes(
        tickformat="%H:%M\n%d/%m",   # giờ:phút xuống dòng ngày/tháng
        tickfont=dict(size=11, color="#c9d1d9"),
        nticks=8,                     # vừa đủ mốc, không chen chúc
        row=n_rows, col=1,            # chỉ hàng cuối hiện nhãn thời gian
    )

    # Scale trục giá: kiểu (linear/log) + Auto/Lock
    if opt.scale == "log":
        fig.update_yaxes(type="log", row=1, col=1)
    else:
        fig.update_yaxes(type="linear", row=1, col=1)

    if opt.lock_scale:
        # LOCK: cố định trục giá theo min/max dữ liệu, không auto-fit khi pan/zoom
        lo = float(df["Low"].min())
        hi = float(df["High"].max())
        pad = (hi - lo) * 0.04 if hi > lo else 1.0
        fig.update_yaxes(autorange=False, fixedrange=False,
                         range=[lo - pad, hi + pad], row=1, col=1)
    else:
        # AUTO: trục giá tự co giãn theo vùng đang xem (mặc định)
        fig.update_yaxes(autorange=True, row=1, col=1)

    # Range cố định cho panel có thang chuẩn
    if "pctb" in row_of:
        fig.update_yaxes(range=[-0.1, 1.1], row=row_of["pctb"], col=1)
    if "stoch" in row_of:
        fig.update_yaxes(range=[0, 100], row=row_of["stoch"], col=1)

    # Lưu map panel->row để vẽ mũi tên đúng ô (app dùng)
    fig._row_of = row_of
    return fig


# Cấu hình ModeBar/scroll — pan/zoom mượt, reset, công cụ vẽ đầy đủ
# drawline=trendline/horizontal, drawopenpath=vẽ tự do, drawrect, drawcircle
PLOTLY_CONFIG = {
    "scrollZoom": True,        # cuộn + pinch 2 ngón để zoom (mobile)
    "displaylogo": False,
    "modeBarButtonsToAdd": [
        "drawline",       # vẽ đường thẳng / trendline
        "drawopenpath",   # vẽ tự do
        "drawrect",       # vùng chữ nhật (đánh dấu range)
        "drawcircle",     # vẽ vòng tròn
        "eraseshape",     # XÓA hình đang chọn (bấm hình rồi bấm nút này)
    ],
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "doubleClick": "reset",
    "displayModeBar": True,    # luôn hiện thanh công cụ (có nút xóa)
    "responsive": True,
    "editable": True,          # kéo/sửa/chọn hình đã vẽ
}


def apply_scale(fig: go.Figure, scale: str = "linear") -> go.Figure:
    """Đổi scale trục giá (hàng 1) sau khi đã dựng figure."""
    fig.update_yaxes(type="log" if scale == "log" else "linear", row=1, col=1)
    return fig


# Các mức Fibonacci Retracement chuẩn
FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_COLORS = ["#787b86", "#F23645", "#FF9800", "#4CAF50",
              "#2962FF", "#9C27B0", "#787b86"]


def add_fibonacci(fig: go.Figure, df: pd.DataFrame, lookback: int = 120) -> go.Figure:
    """
    Vẽ Fibonacci Retracement tự động trên hàng giá, dựa trên đỉnh/đáy
    trong `lookback` nến gần nhất.

    Các mức: 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0.
    Hướng fib theo xu hướng: nếu đáy đến trước đỉnh -> uptrend (fib từ thấp lên).
    """
    if df is None or df.empty:
        return fig
    recent = df.tail(lookback)
    hi = float(recent["High"].max())
    lo = float(recent["Low"].min())
    if hi <= lo:
        return fig

    diff = hi - lo
    # Xác định hướng: đỉnh hay đáy xuất hiện sau (gần hiện tại hơn)
    idx_hi = recent["High"].idxmax()
    idx_lo = recent["Low"].idxmin()
    uptrend = idx_lo < idx_hi  # đáy trước, đỉnh sau -> sóng tăng

    for level, color in zip(FIB_LEVELS, FIB_COLORS):
        # Trong uptrend: 0 ở đỉnh, 1 ở đáy (retrace xuống); ngược lại cho downtrend
        if uptrend:
            price = hi - diff * level
        else:
            price = lo + diff * level
        fig.add_hline(
            y=price, line=dict(color=color, width=1, dash="dash"),
            annotation_text=f"Fib {level:.3f} — {price:.2f}",
            annotation_position="right",
            annotation_font=dict(size=9, color=color),
            row=1, col=1,
        )
    return fig


def add_reversal_zones(fig: go.Figure, zones_info: dict) -> go.Figure:
    """
    Vẽ các VÙNG ĐẢO CHIỀU lên hàng giá (đường ngang có nhãn).
    zones_info: kết quả từ analyzer.compute_reversal_zones().
    Vùng đang bị "chạm" -> tô đậm + nhãn nổi bật.
    """
    if not zones_info or not zones_info.get("zones"):
        return fig
    for z in zones_info["zones"]:
        touched = z.get("touched", False)
        is_sup = z["type"] == "support"
        color = "#089981" if is_sup else "#F23645"
        width = 2.5 if touched else 1.2
        dash = "solid" if touched else "dash"
        label = f"{'🟢' if is_sup else '🔴'} {z['name']} {z['level']}"
        if touched:
            label = "🎯 " + label + " (CHẠM)"
        fig.add_hline(
            y=z["level"],
            line=dict(color=color, width=width, dash=dash),
            annotation_text=label,
            annotation_position="left",
            annotation_font=dict(size=10, color=color),
            row=1, col=1,
        )
    return fig


def add_signal_arrows(fig, df, points: dict):
    """
    Vẽ MŨI TÊN NHỎ cho 3 tín hiệu, màu theo hướng (xanh=MUA, đỏ=BÁN):
      - %BB (cốt lõi): mũi tên trên BIỂU ĐỒ GIÁ + trên ô %B
      - Stoch: mũi tên trên ô Stochastic
      - ADX/DI: mũi tên trên ô ADX
    points: kết quả từ analyzer.signal_points(df).
    Vẽ mũi tên ở vị trí gần nhất mỗi loại (tránh rối), nhỏ gọn.
    """
    if not points:
        return fig
    row_of = getattr(fig, "_row_of", {"price": 1})
    GREEN, RED = "#089981", "#F23645"

    def _arrow(ts, y, up, color, row, size=11):
        if ts not in df.index:
            return
        fig.add_annotation(
            x=ts, y=y, text=("▲" if up else "▼"),
            showarrow=False, font=dict(size=size, color=color),
            row=row, col=1, yshift=(8 if up else -8),
        )

    # --- %BB (CỐT LÕI) — vẽ trên giá + ô %B, mũi tên rõ hơn chút ---
    for ts in points.get("bb", {}).get("sell", [])[-5:]:
        _arrow(ts, float(df.loc[ts, "High"]) if ts in df.index else 0, False, RED, 1, 13)
        if "pctb" in row_of:
            _arrow(ts, 1.0, False, RED, row_of["pctb"], 12)
    for ts in points.get("bb", {}).get("buy", [])[-5:]:
        _arrow(ts, float(df.loc[ts, "Low"]) if ts in df.index else 0, True, GREEN, 1, 13)
        if "pctb" in row_of:
            _arrow(ts, 0.0, True, GREEN, row_of["pctb"], 12)

    # --- Stochastic — trên ô stoch + nhắc trên giá ---
    if "stoch" in row_of:
        for ts in points.get("stoch", {}).get("sell", [])[-3:]:
            _arrow(ts, 85, False, RED, row_of["stoch"])
        for ts in points.get("stoch", {}).get("buy", [])[-3:]:
            _arrow(ts, 15, True, GREEN, row_of["stoch"])

    # --- ADX/DI — trên ô adx ---
    if "adx" in row_of:
        for ts in points.get("adx", {}).get("sell", [])[-3:]:
            _arrow(ts, 22, False, RED, row_of["adx"])
        for ts in points.get("adx", {}).get("buy", [])[-3:]:
            _arrow(ts, 22, True, GREEN, row_of["adx"])

    return fig


def add_backtest_markers(fig, df, signals):
    """
    Đánh dấu các ĐIỂM VÀO LỆNH lịch sử (từ backtest_recent_signals) lên biểu đồ giá.
    Mỗi điểm: vòng tròn + nhãn pip (xanh nếu đúng, đỏ nếu sai).
    """
    if not signals:
        return fig
    for s in signals:
        ts = s["idx"]
        if ts not in df.index:
            continue
        win = s["result"] == "ĐÚNG"
        color = "#089981" if win else "#F23645"
        y = float(df.loc[ts, "Low"]) if s["direction"] == "MUA" else float(df.loc[ts, "High"])
        ay = 36 if s["direction"] == "MUA" else -36
        fig.add_annotation(
            x=ts, y=y,
            text=f"{s['direction']}<br>{s['pips']:+.0f}p",
            showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=2,
            arrowcolor=color, font=dict(size=10, color=color),
            bgcolor="rgba(0,0,0,0.6)", bordercolor=color, borderwidth=1,
            ax=0, ay=ay, row=1, col=1,
        )
    return fig
