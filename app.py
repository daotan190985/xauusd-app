"""
app.py
======
XAU/USD Professional Trading Journal & Signal Analyzer
Ứng dụng Streamlit phân tích tín hiệu + nhật ký giao dịch chuyên nghiệp.

Chạy:  streamlit run app.py
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime

import pandas as pd
import streamlit as st

# Auto-refresh: dùng streamlit-autorefresh nếu có (mượt, không reload cả trang)
try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:  # fallback nếu chưa cài thư viện
    _HAS_AUTOREFRESH = False


from core.analyzer import (
    IndicatorParams,
    aggregate_signals,
    analyze_timeframe,
    compute_reversal_zones,
    count_band_tests,
    detect_pullback_zone,
    ema1200_scalp_signal,
    escalation_scan,
    h4_band_context,
    m1_confluence_check,
    target_by_tf,
    three_tier_entry,
    trend_direction,
)
from core.charts import (
    ALL_INDICATORS,
    DEFAULT_INDICATORS,
    PLOTLY_CONFIG,
    ChartOptions,
    add_fibonacci,
    add_reversal_zones,
    add_signal_arrows,
    build_full_chart,
)
from core.data import (
    DEFAULT_PERIOD,
    SYMBOLS,
    clear_all_cache,
    clear_processed_cache,
    clear_raw_cache,
    get_data_until,
    get_processed_data,
)
from core.realtime import apply_live_price, get_realtime_price
from core.journal import (
    ENTRY_METHODS,
    compute_stats,
    delete_trade,
    ensure_dirs,
    filter_by_period,
    load_history,
    method_performance,
    parse_methods,
    save_historical_chart,
    save_trade,
    save_uploaded_image,
)
from core.pdf_export import export_report

# ----------------------------------------------------------------------------
# Cấu hình log + trang
# ----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

st.set_page_config(
    page_title="XAU/USD Trading Journal & Signal Analyzer",
    page_icon="🥇",
    layout="wide",
    initial_sidebar_state="expanded",
)

ensure_dirs()

# CSS nhẹ cho giao diện đẹp hơn
st.markdown(
    """
    <style>
      .big-signal {font-size:30px;font-weight:800;padding:14px 20px;border-radius:12px;
                   text-align:center;margin:8px 0;}
      .buy {background:linear-gradient(135deg,#1b5e20,#43a047);color:#fff;}
      .sell {background:linear-gradient(135deg,#b71c1c,#e53935);color:#fff;}
      .neutral {background:linear-gradient(135deg,#37474f,#607d8b);color:#fff;}
      .metric-card {background:#1e1e2e;padding:10px 14px;border-radius:10px;
                    border:1px solid #333;}
    </style>
    """,
    unsafe_allow_html=True,
)

TIMEFRAMES = ["1m", "5m", "15m", "30m", "60m", "4h", "1d", "1wk"]


def signal_css_class(direction: str) -> str:
    """Trả về class CSS theo hướng tín hiệu."""
    if "MUA" in direction:
        return "buy"
    if "BÁN" in direction:
        return "sell"
    return "neutral"


def indicator_settings_panel(key_prefix: str) -> tuple[IndicatorParams, dict]:
    """
    Panel chỉnh THAM SỐ + MÀU từng indicator (giống TradingView).
    Trả về (IndicatorParams, colors_dict). Đổi -> Streamlit tự rerun -> vẽ lại ngay.
    """
    colors: dict = {}
    with st.sidebar:
        with st.expander("🎨 Tùy chỉnh chỉ báo (tham số + màu)", expanded=False):
            st.caption("Chỉnh giống TradingView. Đổi là biểu đồ cập nhật ngay.")

            st.markdown("**EMA**")
            c1, c2 = st.columns(2)
            ema_fast = c1.number_input("EMA nhanh", 5, 2000, 100, key=f"{key_prefix}_emaf")
            colors["col_ema100"] = c2.color_picker("Màu", "#2962FF", key=f"{key_prefix}_emafc")
            c1, c2 = st.columns(2)
            ema_mid = c1.number_input("EMA vừa", 5, 2000, 200, key=f"{key_prefix}_emam")
            colors["col_ema200"] = c2.color_picker("Màu ", "#FF9800", key=f"{key_prefix}_emamc")
            c1, c2 = st.columns(2)
            ema_slow = c1.number_input("EMA chậm", 5, 5000, 1200, key=f"{key_prefix}_emas")
            colors["col_ema1200"] = c2.color_picker("Màu  ", "#AB47BC", key=f"{key_prefix}_emasc")

            st.markdown("**Bollinger Bands**")
            c1, c2, c3 = st.columns(3)
            bb_period = c1.number_input("Length", 2, 200, 20, key=f"{key_prefix}_bbp")
            bb_std = c2.number_input("StdDev", 0.5, 5.0, 2.0, 0.1, key=f"{key_prefix}_bbs")
            colors["col_bb"] = c3.color_picker("Màu BB", "#7890a0", key=f"{key_prefix}_bbc")

            st.markdown("**MACD**")
            c1, c2, c3 = st.columns(3)
            macd_fast = c1.number_input("Fast", 2, 200, 24, key=f"{key_prefix}_mf")
            macd_slow = c2.number_input("Slow", 2, 400, 52, key=f"{key_prefix}_ms")
            macd_signal = c3.number_input("Signal", 1, 100, 14, key=f"{key_prefix}_msig")
            c1, c2, c3 = st.columns(3)
            colors["col_macd"] = c1.color_picker("MACD", "#2962FF", key=f"{key_prefix}_mc")
            colors["col_macd_sig"] = c2.color_picker("Signal", "#FF6D00", key=f"{key_prefix}_msc")
            colors["col_hist_up"] = c3.color_picker("Hist+", "#089981", key=f"{key_prefix}_hu")
            colors["col_hist_down"] = st.color_picker("Hist-", "#F23645", key=f"{key_prefix}_hd")

            st.markdown("**ADX + DI**")
            c1, c2, c3 = st.columns(3)
            adx_period = c1.number_input("ADX period", 2, 100, 14, key=f"{key_prefix}_adxp")
            colors["col_dip"] = c2.color_picker("DI+", "#26A69A", key=f"{key_prefix}_dipc")
            colors["col_dim"] = c3.color_picker("DI-", "#EF5350", key=f"{key_prefix}_dimc")
            colors["col_adx"] = st.color_picker("ADX line", "#5C6BC0", key=f"{key_prefix}_adxc")

            st.markdown("**Stochastic**")
            c1, c2, c3 = st.columns(3)
            stoch_k = c1.number_input("K", 2, 200, 42, key=f"{key_prefix}_sk")
            stoch_sk = c2.number_input("Smooth K", 1, 50, 5, key=f"{key_prefix}_ssk")
            stoch_sd = c3.number_input("Smooth D", 1, 50, 3, key=f"{key_prefix}_ssd")
            c1, c2 = st.columns(2)
            colors["col_stochk"] = c1.color_picker("%K", "#29B6F6", key=f"{key_prefix}_skc")
            colors["col_stochd"] = c2.color_picker("%D", "#EC407A", key=f"{key_prefix}_sdc")

            st.markdown("**RSI**")
            c1, c2 = st.columns(2)
            rsi_period = c1.number_input("RSI period", 2, 100, 14, key=f"{key_prefix}_rsip")
            colors["col_rsi"] = c2.color_picker("Màu RSI", "#FFB300", key=f"{key_prefix}_rsic")

            st.markdown("**Keltner Channel**")
            c1, c2, c3 = st.columns(3)
            kc_period = c1.number_input("KC Length", 2, 200, 42, key=f"{key_prefix}_kcp")
            kc_mult = c2.number_input("KC Mult", 0.1, 5.0, 1.0, 0.1, key=f"{key_prefix}_kcm")
            colors["col_kc"] = c3.color_picker("Màu KC", "#FFA726", key=f"{key_prefix}_kcc")

    params = IndicatorParams(
        ema_fast=ema_fast, ema_mid=ema_mid, ema_slow=ema_slow,
        bb_period=bb_period, bb_std=bb_std, adx_period=adx_period,
        macd_fast=macd_fast, macd_slow=macd_slow, macd_signal=macd_signal,
        stoch_k=stoch_k, stoch_smooth_k=stoch_sk, stoch_smooth_d=stoch_sd,
        rsi_period=rsi_period, kc_period=kc_period, kc_mult=kc_mult,
    )
    return params, colors


def indicator_controls(key_prefix: str, colors: Optional[dict] = None) -> ChartOptions:
    """
    Hiển thị bộ điều khiển bật/tắt indicator + chọn scale (tái sử dụng).
    Trả về ChartOptions tương ứng. Cập nhật ngay khi đổi (Streamlit rerun nhẹ).
    colors: dict màu tùy chỉnh từ indicator_settings_panel (nếu có).
    """
    with st.expander("🎛️ Hiển thị chỉ báo & Scale", expanded=False):
        selected = st.multiselect(
            "Bật/tắt chỉ báo (mặc định gọn để dễ xem)",
            ALL_INDICATORS, default=DEFAULT_INDICATORS,
            key=f"{key_prefix}_inds",
        )
        scale_label = st.radio(
            "Scale giá", ["Linear", "Log", "Phần trăm"],
            horizontal=True, key=f"{key_prefix}_scale",
        )
        lock = st.toggle(
            "🔒 Lock scale (khóa trục giá, không tự co giãn khi pan/zoom)",
            value=False, key=f"{key_prefix}_lock",
        )
    scale_map = {"Linear": "linear", "Log": "log", "Phần trăm": "percent"}
    return ChartOptions.from_selection(
        selected, scale_map[scale_label], lock, colors=colors)


# ============================================================================
# TAB 1 — PHÂN TÍCH TÍN HIỆU
# ============================================================================
def _render_reversal_section(ticker: str, pkey: tuple, fb: str = None,
                             live_price=None) -> None:
    """
    Hiển thị VÙNG ĐẢO CHIỀU (Fib 161.8 + EMA 200/1200) trên khung lớn,
    và khi giá chạm vùng thì kiểm tra HỘI TỤ M1 (%BB + Stoch + MACD tách histogram).
    """
    st.divider()
    st.subheader("🎯 Vùng đảo chiều & Tín hiệu vào lệnh M1")

    # ===== QUY LUẬT 3 TẦNG (chiến lược thực chiến) =====
    st.markdown("##### 🏆 ĐIỂM VÀO 3 TẦNG (khung lớn + trung + M1)")
    frames3 = {}
    for tf in ["4h", "60m", "30m", "15m", "5m"]:
        d = _fresh_df(ticker, tf, DEFAULT_PERIOD.get(tf), pkey, fb, live_price)
        if not d.empty:
            frames3[tf] = d
    df_m1_3 = _fresh_df(ticker, "1m", DEFAULT_PERIOD.get("1m"), pkey, fb, live_price)
    tt = three_tier_entry(frames3, df_m1_3)

    # 3 ô trạng thái từng tầng
    tcol1, tcol2, tcol3 = st.columns(3)
    for col, key, title in [
        (tcol1, "tier1", "Tầng 1 — Khung lớn\n(kháng cự động H4/H1)"),
        (tcol2, "tier2", "Tầng 2 — Khung trung\n(ngoài band M15/M30/M5)"),
        (tcol3, "tier3", "Tầng 3 — M1\n(then chốt %B+Stoch)"),
    ]:
        ok = tt[key]["ok"]
        bg = "#0d3b2e" if ok else "#2a2e39"
        mark = "✅" if ok else "⬜"
        detail = "<br>".join(tt[key].get("detail", [])) or "chưa thỏa"
        col.markdown(
            f"<div style='background:{bg};padding:10px;border-radius:8px;font-size:12px'>"
            f"<b>{mark} {title.split(chr(10))[0]}</b><br>"
            f"<span style='color:#8b95a5;font-size:11px'>{title.split(chr(10))[1]}</span><br>"
            f"<span style='font-size:11px'>{detail}</span></div>",
            unsafe_allow_html=True,
        )

    if tt["entry_ready"]:
        c = "#089981" if tt["direction"] == "MUA" else "#F23645"
        st.markdown(
            f"<div style='background:{c};color:#fff;padding:16px;border-radius:12px;"
            f"font-size:22px;font-weight:800;text-align:center;margin-top:10px'>"
            f"✅ ĐIỂM VÀO {tt['direction']} — {tt['score']}/3 TẦNG HỘI TỤ</div>",
            unsafe_allow_html=True,
        )
    elif tt["score"] >= 2:
        st.markdown(
            f"<div style='background:#3a2f16;color:#FFD54F;padding:12px;border-radius:10px;"
            f"font-weight:700;text-align:center;margin-top:10px;border:1px solid #D4A017'>"
            f"⏳ {tt['note']}</div>", unsafe_allow_html=True)
    else:
        st.info(f"🎯 {tt['note']}")

    # ===== TÍN HIỆU LEO THANG KHUNG (mô hình %B vượt band nhiều lần) =====
    st.markdown("##### 🎯 Tín hiệu leo thang (mô hình %B vượt band ≥2 lần)")
    esc = escalation_scan(frames3)
    if esc["best"]:
        b = esc["best"]
        dfb = frames3.get(b["tf"])
        tgt = target_by_tf(b["tf"], dfb["Close"].iloc[-1], b["direction"], dfb) if dfb is not None else {}
        if b["safe"]:
            c = "#089981" if b["direction"] == "MUA" else "#F23645"
            st.markdown(
                f"<div style='background:{c};color:#fff;padding:16px;border-radius:12px;"
                f"font-size:19px;font-weight:800;text-align:center'>"
                f"✅ AN TOÀN — {b['direction']} khung {b['tf']} "
                f"(xác nhận bởi {b['confirmed_by']}) · {b['touches']} lần chạm band</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='background:#3a2f16;color:#FFD54F;padding:13px;border-radius:10px;"
                f"font-weight:700;text-align:center;border:1px solid #D4A017'>"
                f"⚠️ {b['direction']} khung {b['tf']} ({b['touches']} lần) — "
                f"chưa có khung lớn xác nhận, theo dõi thêm</div>",
                unsafe_allow_html=True,
            )
        if tgt.get("target"):
            st.caption(f"📍 Entry ~{tgt['entry']} · 🎯 Target {tgt['target']} "
                       f"· 🛑 SL {tgt['sl']} (tối thiểu 2 nến, ~{tgt.get('move_pips','')} giá)")
        # Liệt kê tất cả khung có mô hình
        rows = " · ".join(
            f"{s['tf']}:{s['direction']}({s['touches']})" + ("✅" if s['safe'] else "")
            for s in esc["signals"])
        st.caption(f"Các khung có mô hình: {rows}")

        # Lưu lịch sử tín hiệu
        import datetime as _dt
        hist = st.session_state.setdefault("signal_history", [])
        sig_id = f"{b['tf']}_{b['direction']}_{dfb.index[-1]}"
        if not hist or hist[-1].get("id") != sig_id:
            hist.append({
                "id": sig_id, "time": _dt.datetime.now().strftime("%H:%M:%S"),
                "tf": b["tf"], "direction": b["direction"],
                "safe": b["safe"], "touches": b["touches"],
                "entry": tgt.get("entry"), "target": tgt.get("target"),
            })
            st.session_state["signal_history"] = hist[-30:]  # giữ 30 gần nhất
    else:
        st.info("🎯 Chưa có mô hình %B vượt band ở khung nào.")

    # ===== ĐẾM TEST BAND + BỐI CẢNH H4/H1 (nguyên tắc chi tiết) =====
    st.markdown("##### 🔢 Đếm test band & điều kiện vào lệnh")
    # Điều kiện nền: H4 phải gần/chạm band
    df_h4 = frames3.get("4h")
    df_h1 = frames3.get("60m")
    ctx = h4_band_context(df_h4)
    trend_h4 = trend_direction(df_h4) if df_h4 is not None else "NGANG"
    trend_h1 = trend_direction(df_h1) if df_h1 is not None else "NGANG"

    if ctx["ok"]:
        st.success(f"✅ Điều kiện nền: {ctx['note']} · Xu hướng H4 {trend_h4}, H1 {trend_h1}")
    else:
        st.warning(f"⚠️ {ctx['note']} Chưa đủ điều kiện nền (H4 cần gần/chạm band) — "
                   f"tín hiệu đếm band kém tin cậy.")

    # Đếm test band cho các khung nhỏ
    test_tfs = ["60m", "30m", "15m", "5m", "1m"]
    found_entry = False
    for tf in test_tfs:
        dft = frames3.get(tf) if tf != "1m" else df_m1_3
        if dft is None or dft.empty:
            continue
        # Xác định đỉnh hay đáy theo %B hiện tại
        pb_now = dft["PCT_B"].iloc[-1] if "PCT_B" in dft else 0.5
        side = "top" if pb_now >= 0.5 else "bottom"
        ct = count_band_tests(dft, window=12, side=side)
        if ct["decision"] == "VÀO":
            found_entry = True
            direction = "BÁN" if side == "top" else "MUA"
            # Cảnh báo ngược xu hướng lớn
            warn = ""
            big_trend = trend_h4 if trend_h4 != "NGANG" else trend_h1
            if (direction == "BÁN" and big_trend == "TĂNG") or \
               (direction == "MUA" and big_trend == "GIẢM"):
                warn = " ⚠️ NGƯỢC xu hướng lớn — cẩn thận!"
            c = "#F23645" if direction == "BÁN" else "#089981"
            strong = " 🔥 (đụng band %B không vượt 1)" if ct["touched_not_break"] else ""
            st.markdown(
                f"<div style='background:{c};color:#fff;padding:13px;border-radius:10px;"
                f"font-weight:700;margin-bottom:6px'>"
                f"{direction} khung {tf} — test band {ct['count']} lần{strong}<br>"
                f"🎯 Mục tiêu {ct['pip_target']} pip{warn}</div>",
                unsafe_allow_html=True,
            )
            st.caption(f"  {ct['note']}")
    if not found_entry:
        # Hiện trạng thái đếm gần nhất để theo dõi
        statuses = []
        for tf in test_tfs:
            dft = frames3.get(tf) if tf != "1m" else df_m1_3
            if dft is None or dft.empty:
                continue
            pb_now = dft["PCT_B"].iloc[-1] if "PCT_B" in dft else 0.5
            side = "top" if pb_now >= 0.5 else "bottom"
            ct = count_band_tests(dft, window=12, side=side)
            statuses.append(f"{tf}:{ct['count']}lần")
        st.info(f"🔢 Chưa đủ điều kiện vào (cần test band 2-4 lần). "
                f"Hiện tại: {' · '.join(statuses)}")

    # Lịch sử tín hiệu
    hist = st.session_state.get("signal_history", [])
    if hist:
        with st.expander(f"📜 Lịch sử tín hiệu ({len(hist)})", expanded=False):
            for h in reversed(hist[-15:]):
                mk = "✅" if h["safe"] else "⚠️"
                st.markdown(
                    f"{mk} {h['time']} · **{h['direction']}** {h['tf']} "
                    f"({h['touches']} lần) · Entry {h.get('entry','—')} → "
                    f"Target {h.get('target','—')}"
                )

    st.divider()
    st.markdown("##### ⚡ Scalp theo EMA1200 khung lớn")
    sc1, sc2 = st.columns([1, 2])
    with sc1:
        scalp_tf = st.selectbox(
            "⚡ Khung lớn cho EMA1200 (scalp)",
            ["60m", "4h", "1d"], index=1, key="scalp_tf",
            help="EMA1200 của khung này là điểm đảo chiều để vào M1. "
                 "Khung càng lớn, điểm đảo càng mạnh.",
        )
    df_scalp = _fresh_df(ticker, scalp_tf, DEFAULT_PERIOD.get(scalp_tf), pkey, fb, live_price)
    df_m1 = _fresh_df(ticker, "1m", DEFAULT_PERIOD.get("1m"), pkey, fb, live_price)
    scalp = ema1200_scalp_signal(df_scalp, df_m1)

    if scalp["stage"] == "confirm":
        c = "#089981" if scalp["direction"] == "MUA" else "#F23645"
        st.markdown(
            f"<div style='background:{c};color:#fff;padding:16px;border-radius:12px;"
            f"font-size:20px;font-weight:800;text-align:center'>"
            f"✅ SCALP {scalp['direction']} — Giá chạm EMA1200 ({scalp['level']}) + M1 xác nhận</div>",
            unsafe_allow_html=True,
        )
        st.caption(scalp["note"])
    elif scalp["stage"] == "near":
        st.markdown(
            f"<div style='background:#3a2f16;color:#FFD54F;padding:14px;border-radius:10px;"
            f"font-size:16px;font-weight:700;text-align:center;border:1px solid #D4A017'>"
            f"⏳ GẦN điểm đảo — EMA1200 {scalp['level']} ({scalp['direction']}), "
            f"cách {scalp['dist_pct']}% — theo dõi sát M1</div>",
            unsafe_allow_html=True,
        )
        st.caption(scalp["note"])
    else:
        st.info(f"⚡ {scalp['note']}")

    # ---- PHÁT HIỆN SÓNG HỒI / ĐIỂM ĐẢO TỔNG HỢP ----
    st.markdown("##### 🌀 Phát hiện sóng hồi (đa khung band confluence)")
    frames = {}
    for tf in ["4h", "60m", "15m", "5m"]:
        d = _fresh_df(ticker, tf, DEFAULT_PERIOD.get(tf), pkey, fb, live_price)
        if not d.empty:
            frames[tf] = d
    pb = detect_pullback_zone(frames, df_m1)
    if pb["detected"]:
        c = "#089981" if pb["direction"] == "MUA" else "#F23645"
        st.markdown(
            f"<div style='background:{c};color:#fff;padding:14px;border-radius:10px;"
            f"font-size:17px;font-weight:800;text-align:center'>"
            f"🌀 SÓNG HỒI {pb['direction']} — độ mạnh {pb['strength']}</div>",
            unsafe_allow_html=True,
        )
        st.caption(pb["note"])
        # Liệt kê band đang chạm + khung ngoài band
        if pb["touched_levels"]:
            st.markdown("**Band đang chạm:**")
            for l in pb["touched_levels"]:
                icon = "🟢" if l["type"] == "support" else "🔴"
                st.markdown(f"{icon} {l['name']}: {l['level']} (cách {l['dist_pct']}%)")
        if pb["outside_band"]:
            ob_txt = ", ".join(f"{tf} ({side})" for tf, side in pb["outside_band"].items())
            st.caption(f"⭐ Ưu thế — giá ngoài band: {ob_txt}")
    else:
        st.info(f"🌀 {pb['note']}")

    st.divider()

    c1, c2 = st.columns([1, 1])
    with c1:
        zone_tf = st.selectbox(
            "Khung lớn xác định vùng (Fib 161.8 + EMA động)",
            ["60m", "4h", "1d"], index=0, key="zone_tf",
        )
    df_big = _fresh_df(ticker, zone_tf, DEFAULT_PERIOD.get(zone_tf), pkey, fb, live_price)
    if df_big.empty:
        st.warning("Không tải được dữ liệu khung lớn.")
        return

    zinfo = compute_reversal_zones(df_big)

    # Bảng các vùng
    st.markdown(f"**Giá hiện tại: {zinfo['price']}**")
    for z in zinfo["zones"]:
        icon = "🟢" if z["type"] == "support" else "🔴"
        touched = " &nbsp; 🎯 **ĐANG CHẠM**" if z.get("touched") else ""
        st.markdown(
            f"{icon} {z['name']}: **{z['level']}** "
            f"({'Hỗ trợ' if z['type']=='support' else 'Kháng cự'}) "
            f"— cách {z['dist_pct']}%{touched}",
            unsafe_allow_html=True,
        )

    # Khi giá đang chạm vùng -> kiểm tra M1
    if zinfo["in_zone"]:
        st.warning(f"⚠️ {zinfo['note']}")
        df_m1 = _fresh_df(ticker, "1m", DEFAULT_PERIOD.get("1m"), pkey, fb, live_price)
        m1 = m1_confluence_check(df_m1)
        if m1["ready"]:
            color = "#089981" if m1["direction"] == "MUA" else "#F23645"
            st.markdown(
                f"<div style='background:{color};color:#fff;padding:14px;"
                f"border-radius:10px;font-size:18px;font-weight:800;text-align:center'>"
                f"✅ M1 HỘI TỤ {m1['score']}/3 — CÂN NHẮC {m1['direction']}</div>",
                unsafe_allow_html=True,
            )
            st.caption(m1["note"])
        else:
            st.info(f"⏳ {m1['note']}")
    else:
        if zinfo["nearest"]:
            st.info(f"ℹ️ {zinfo['note']} Giá chưa chạm vùng — chưa cần soi M1.")

    # Biểu đồ khung lớn có đánh dấu vùng
    with st.expander("📈 Xem biểu đồ khung lớn + vùng đảo chiều", expanded=False):
        opt = ChartOptions.from_selection(["EMA 200", "EMA 1200", "MACD"])
        fig = build_full_chart(df_big, title=f"{st.session_state.get('cur_symbol','XAU/USD')} — {zone_tf} + vùng đảo chiều",
                               options=opt, height=560)
        if fig:
            add_reversal_zones(fig, zinfo)
            st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def _safe_secret(key: str) -> str:
    """
    Đọc st.secrets[key] AN TOÀN. Nếu không có file secrets.toml (chạy local
    không cấu hình), st.secrets sẽ ném lỗi -> bọc try/except trả về rỗng.
    """
    try:
        return st.secrets.get(key, "")
    except Exception:
        return ""


def _fresh_df(ticker, tf, period, pkey, fb, live_price=None,
              source=None, td_key=None, symbol_label=None):
    """
    Lấy DataFrame đã có chỉ báo.

    Ưu tiên NẾN TƯƠI:
      - Twelve Data + key: lấy NẾN OHLC real-time -> biểu đồ + chỉ báo khớp TradingView.
      - GoldAPI/yfinance: chỉ lấy được 1 giá tươi -> vá vào nến cuối yfinance
        (biểu đồ vẫn dùng nến yfinance, KHÔNG khớp hoàn toàn TradingView).

    source/td_key/symbol_label: nếu không truyền, đọc từ session_state.
    """
    from core.analyzer import IndicatorParams, add_indicators
    from core.realtime import get_twelvedata_ohlc
    if source is None:
        source = st.session_state.get("_src", "yfinance")
    if td_key is None:
        td_key = st.session_state.get("_tdkey", "")
    if symbol_label is None:
        symbol_label = st.session_state.get("cur_symbol", "")
    # Giá tươi: nếu không truyền, lấy từ session (đã fetch sớm ở đầu phân tích)
    if live_price is None:
        live_price = st.session_state.get("_live_price")
    params = IndicatorParams(*pkey) if pkey else None

    # 1) Twelve Data -> nến tươi thật
    if source == "twelvedata" and td_key and symbol_label:
        raw = get_twelvedata_ohlc(symbol_label, tf, td_key, outputsize=500)
        if raw is not None and not raw.empty and len(raw) > 50:
            st.session_state["_td_ok"] = True
            return add_indicators(raw, params)
        st.session_state["_td_ok"] = False

    # 2) yfinance + vá giá tươi vào nến cuối (GoldAPI/yfinance)
    df = get_processed_data(ticker, tf, period, pkey, fallback=fb)
    if df is None or df.empty:
        return df
    if live_price:
        patched = apply_live_price(
            df[["Open", "High", "Low", "Close", "Volume"]], live_price)
        return add_indicators(patched, params)
    return df


def render_tab_analyzer() -> None:
    st.header("📊 Phân tích Tín hiệu Đa khung")

    # Panel chỉnh tham số + màu (giống TradingView) — nằm trong sidebar
    params, colors = indicator_settings_panel("set")
    pkey = params.cache_key()

    with st.sidebar:
        st.subheader("⚙️ Tham số phân tích")
        symbol_label = st.selectbox(
            "💱 Cặp giao dịch",
            list(SYMBOLS.keys()), index=0, key="symbol_sel",
        )
        sym = SYMBOLS[symbol_label]
        ticker = sym["primary"]
        fb = sym["fallback"]
        st.session_state["cur_symbol"] = symbol_label
        st.caption(f"Ticker: {ticker} (fallback: {sym['fallback']})")
        selected_tfs = st.multiselect(
            "Khung thời gian", TIMEFRAMES, default=["15m", "60m", "1d"]
        )
        analyze_btn = st.button("🚀 Phân tích ngay", type="primary",
                                use_container_width=True)

        # ===== NGUỒN DỮ LIỆU REAL-TIME =====
        st.divider()
        st.subheader("📡 Nguồn dữ liệu")
        data_source = st.radio(
            "Chọn nguồn giá tươi",
            ["yfinance", "twelvedata", "goldapi"],
            format_func=lambda s: {
                "yfinance": "yfinance (free, trễ ~15 phút)",
                "twelvedata": "Twelve Data (real-time, cần key)",
                "goldapi": "GoldAPI (real-time XAU, cần key)",
            }[s],
            key="data_source",
        )
        td_key = gold_key = ""
        if data_source == "twelvedata":
            td_key = st.text_input(
                "Twelve Data API key", type="password",
                value=_safe_secret("TWELVEDATA_KEY"),
                help="Đăng ký free tại twelvedata.com (800 lệnh/ngày)",
                key="td_key",
            )
        elif data_source == "goldapi":
            gold_key = st.text_input(
                "GoldAPI API key", type="password",
                value=_safe_secret("GOLDAPI_KEY"),
                help="Đăng ký free tại goldapi.io",
                key="gold_key",
            )
        # Lưu cấu hình nguồn để _fresh_df dùng nến tươi Twelve Data
        st.session_state["_src"] = data_source
        st.session_state["_tdkey"] = td_key
        if data_source == "twelvedata" and td_key:
            st.caption("📊 Biểu đồ + chỉ báo dùng NẾN TƯƠI Twelve Data (khớp TradingView)")
        elif data_source == "goldapi" and gold_key:
            st.warning("⚠️ GoldAPI chỉ cho giá hiện tại, KHÔNG cho nến lịch sử. "
                       "Biểu đồ vẫn dùng nến yfinance (trễ ~15') — chỉ vá giá tươi vào "
                       "nến cuối. **Muốn biểu đồ khớp TradingView, hãy chọn Twelve Data.**")

        st.divider()
        st.subheader("🔄 Tự động làm mới")
        auto_refresh = st.toggle("Bật auto-refresh", value=False,
                                 key="auto_refresh")
        # Slider chọn khoảng thời gian: 10s - 300s, mặc định 60s
        refresh_sec = st.slider(
            "Khoảng thời gian (giây)", min_value=10, max_value=600,
            value=600, step=10, key="refresh_sec",
        )

        refresh_count = 0
        if auto_refresh:
            if _HAS_AUTOREFRESH:
                # st_autorefresh chỉ rerun script, GIỮ NGUYÊN session_state
                # (khung đang chọn, indicator đang bật...) -> không mất trạng thái
                refresh_count = st_autorefresh(
                    interval=refresh_sec * 1000, key="data_autorefresh",
                )
            else:
                # Fallback: meta refresh (kém mượt hơn, có cài lại trang)
                st.markdown(
                    f'<meta http-equiv="refresh" content="{refresh_sec}">',
                    unsafe_allow_html=True,
                )
            # Mỗi lần auto-refresh -> xóa cache giá để lấy dữ liệu mới
            clear_processed_cache()
            st.caption(f"⏱️ Tự làm mới mỗi {refresh_sec}s "
                       f"· đã refresh {refresh_count} lần")

        if st.button("↻ Làm mới dữ liệu NGAY", use_container_width=True,
                     type="primary"):
            clear_raw_cache()
            clear_processed_cache()
            st.session_state.pop("analysis_results", None)
            st.rerun()

    if not analyze_btn and "analysis_results" not in st.session_state:
        st.info("Chọn khung thời gian ở thanh bên rồi bấm **Phân tích ngay**.")
        return

    # ===== LẤY GIÁ TƯƠI SỚM (trước phân tích) để vá vào dữ liệu =====
    live_price = None
    live_info = None
    if data_source != "yfinance":
        live_info = get_realtime_price(symbol_label, data_source,
                                       twelvedata_key=td_key, goldapi_key=gold_key)
        if live_info and "price" in live_info:
            live_price = live_info["price"]
    # Lưu vào session để MỌI biểu đồ (multi-pane) vá được giá tươi
    st.session_state["_live_price"] = live_price

    # Phân tích lại khi: bấm nút HOẶC auto-refresh đang bật & đã phân tích trước đó
    should_analyze = analyze_btn or (
        auto_refresh and "analysis_results" in st.session_state
    )
    if should_analyze:
        if not selected_tfs:
            st.warning("Vui lòng chọn ít nhất một khung thời gian.")
            return
        # LUÔN xóa cache trước khi phân tích -> lấy dữ liệu MỚI NHẤT, không bị trễ
        clear_processed_cache()
        clear_raw_cache()
        results = {}
        prog = st.progress(0.0, text="Đang tải & phân tích...")
        for i, tf in enumerate(selected_tfs):
            df = _fresh_df(ticker, tf, DEFAULT_PERIOD.get(tf), pkey, fb, live_price)
            if df is None or df.empty:
                st.error(f"Không tải được dữ liệu khung {tf}.")
                continue
            results[tf] = (analyze_timeframe(df, tf), df)
            prog.progress((i + 1) / len(selected_tfs), text=f"Xong {tf}")
        prog.empty()
        st.session_state["analysis_results"] = results
        import datetime as _dt
        st.session_state["last_update"] = _dt.datetime.now().strftime("%H:%M:%S")

    results = st.session_state.get("analysis_results", {})
    if not results:
        return

    # Tổng hợp confluence
    sig_only = {tf: r[0] for tf, r in results.items()}
    agg = aggregate_signals(sig_only)
    css = signal_css_class(agg["direction"])
    st.markdown(
        f"<div class='big-signal {css}'>TÍN HIỆU TỔNG HỢP: {agg['direction']} "
        f"&nbsp;|&nbsp; Confluence: {agg['confluence_score']}</div>",
        unsafe_allow_html=True,
    )
    # Trạng thái cập nhật
    lu = st.session_state.get("last_update", "—")
    status = f"🕒 Cập nhật cuối: **{lu}**"
    # Giờ của cây nến mới nhất (từ khung nhỏ nhất đang phân tích)
    try:
        smallest_tf = list(results.keys())[0]
        last_candle = results[smallest_tf][1].index[-1]
        last_px = float(results[smallest_tf][1]["Close"].iloc[-1])
        status += (f" &nbsp;·&nbsp; 🕯️ Nến cuối {smallest_tf}: "
                   f"**{last_candle:%d/%m %H:%M}** @ giá **{last_px:,.2f}**")
    except (IndexError, KeyError, ValueError):
        pass
    if auto_refresh:
        status += f" &nbsp;·&nbsp; 🔄 Auto-refresh {refresh_sec}s (đã {refresh_count} lần)"
    st.caption(status)

    # ===== GIÁ TƯƠI REAL-TIME (đã lấy sớm + đã vá vào phân tích) =====
    if data_source != "yfinance":
        rt = live_info
        if rt and "price" in rt:
            extra = ""
            if rt.get("bid") and rt.get("ask"):
                extra = f" &nbsp;|&nbsp; Bid {rt['bid']} / Ask {rt['ask']}"
            st.success(
                f"📡 **Giá tươi {rt['symbol']}: {rt['price']}** "
                f"(nguồn: {rt['source']}){extra} &nbsp;✅ đã dùng cho phân tích"
            )
        elif rt and "error" in rt:
            st.warning(f"📡 {rt['error']} — phân tích đang dùng dữ liệu yfinance.")
        else:
            st.warning("📡 Chưa lấy được giá tươi — phân tích đang dùng yfinance.")

    cols = st.columns(len(results))
    for col, (tf, (res, _)) in zip(cols, results.items()):
        with col:
            st.markdown(
                f"<div class='metric-card'><b>{tf}</b><br/>"
                f"{res.direction}<br/>Điểm: {res.score}</div>",
                unsafe_allow_html=True,
            )

    # ---- VÙNG ĐẢO CHIỀU + TÍN HIỆU M1 ----
    _render_reversal_section(ticker, pkey, sym['fallback'], live_price)

    # ---- Chế độ xem: chia màn hình đa khung hoặc tabs ----
    st.divider()
    view_mode = st.radio(
        "Chế độ xem biểu đồ",
        ["1 khung (tabs)", "2 khung", "3 khung"],
        horizontal=True, key="view_mode",
    )
    opt = indicator_controls("analyzer", colors=colors)

    tf_list = list(results.keys())

    if view_mode == "1 khung (tabs)":
        tf_tabs = st.tabs(tf_list)
        for tab, tf in zip(tf_tabs, tf_list):
            res, df = results[tf]
            with tab:
                c1, c2 = st.columns([1, 3])
                with c1:
                    st.metric("Hướng", res.direction, f"{res.score} điểm")
                    st.caption(f"Giá: {res.last_price:.2f}")
                    with st.expander("Lý do tín hiệu", expanded=True):
                        for reason in res.reasons:
                            st.write("•", reason)
                with c2:
                    fig = build_full_chart(df, title=f"{st.session_state.get('cur_symbol','XAU/USD')} — {tf}", options=opt)
                    if fig:
                        st.plotly_chart(fig, use_container_width=True,
                                        config=PLOTLY_CONFIG)
    else:
        n_panes = 2 if view_mode == "2 khung" else 3
        cc1, cc2 = st.columns([3, 1])
        with cc1:
            pane_h = st.slider("Chiều cao biểu đồ (kéo để khớp màn hình)",
                               min_value=380, max_value=900, value=520, step=20,
                               key="pane_height")
        with cc2:
            show_fib = st.toggle("Fibonacci", value=False, key="show_fib")

        # Mỗi pane CÓ SELECTBOX TIMEFRAME ĐỘC LẬP — đổi khung nào chỉ vẽ lại khung đó.
        # Mặc định lấy từ các khung đã phân tích; tự load nếu chọn khung mới.
        defaults = (tf_list + TIMEFRAMES)[:n_panes]
        cols = st.columns(n_panes)
        for i, col in enumerate(cols):
            with col:
                pane_tf = st.selectbox(
                    f"Khung {i + 1}", TIMEFRAMES,
                    index=TIMEFRAMES.index(defaults[i]) if defaults[i] in TIMEFRAMES else 2,
                    key=f"pane_tf_{i}",
                )
                # Load dữ liệu khung này — DÙNG NẾN TƯƠI (Twelve Data) nếu có,
                # để biểu đồ khớp TradingView, không trễ.
                df = _fresh_df(ticker, pane_tf, DEFAULT_PERIOD.get(pane_tf),
                               pkey, fb)
                if df is None or df.empty:
                    st.warning(f"Không có dữ liệu khung {pane_tf}.")
                    continue
                res = analyze_timeframe(df, pane_tf)
                color = ("#089981" if "MUA" in res.direction
                         else "#F23645" if "BÁN" in res.direction else "#8b95a5")
                st.markdown(
                    f"<b style='color:{color}'>{pane_tf} — {res.direction} "
                    f"({res.score} điểm)</b>", unsafe_allow_html=True)
                fig = build_full_chart(
                    df, title=f"{st.session_state.get('cur_symbol','XAU/USD')} — {pane_tf}", options=opt, height=pane_h)
                if fig:
                    if show_fib:
                        add_fibonacci(fig, df)
                    # Vẽ mũi tên 3 tín hiệu (%BB cốt lõi + Stoch + ADX)
                    from core.analyzer import signal_points
                    add_signal_arrows(fig, df, signal_points(df))
                    st.plotly_chart(fig, use_container_width=True,
                                    config=PLOTLY_CONFIG)


# ============================================================================
# TAB 2 — NHẬT KÝ GIAO DỊCH
# ============================================================================
def render_tab_journal() -> None:
    st.header("📓 Nhật ký Giao dịch")

    left, right = st.columns([1, 1])

    # ---- Cột trái: Form log ----
    with left:
        st.subheader("Ghi nhận giao dịch mới")
        ticker = st.text_input("Ticker", value="XAUUSD=X", key="j_ticker")
        interval = st.selectbox("Khung biểu đồ", TIMEFRAMES, index=2, key="j_interval")

        d = st.date_input("Ngày phân tích", value=datetime.now().date(), key="j_date")
        t = st.time_input("Giờ phân tích", value=datetime.now().time(), key="j_time")
        chart_ts = datetime.combine(d, t if isinstance(t, dtime) else dtime())

        direction = st.selectbox(
            "Tín hiệu", ["MUA MẠNH", "MUA", "TRUNG LẬP", "BÁN", "BÁN MẠNH"],
            key="j_dir",
        )
        methods = st.multiselect("Phương pháp đưa vào lệnh", ENTRY_METHODS,
                                 key="j_methods")
        confidence = st.select_slider("Độ chắc chắn",
                                      ["Thấp", "Trung bình", "Cao"],
                                      value="Trung bình", key="j_conf")

        cc1, cc2, cc3 = st.columns(3)
        entry = cc1.number_input("Entry", value=0.0, step=0.1, key="j_entry")
        sl = cc2.number_input("SL", value=0.0, step=0.1, key="j_sl")
        tp = cc3.number_input("TP", value=0.0, step=0.1, key="j_tp")

        outcome = st.selectbox("Kết quả", ["Đang mở", "WIN", "LOSS"], key="j_outcome")
        pnl = st.number_input("PnL (pips)", value=0.0, step=1.0, key="j_pnl")

        uploaded = st.file_uploader("Chèn ảnh biểu đồ (tùy chọn)",
                                    type=["png", "jpg", "jpeg"], key="j_img")
        if uploaded:
            st.image(uploaded, caption="Xem trước", use_container_width=True)

        notes = st.text_area("Ghi chú & chú thích phương pháp", height=120,
                             key="j_notes")

    # ---- Cột phải: Biểu đồ tại thời điểm ----
    with right:
        st.subheader("Biểu đồ tại thời điểm phân tích")
        opt_j = indicator_controls("journal")
        if st.button("📈 Tạo / Cập nhật biểu đồ tại thời điểm này",
                     use_container_width=True):
            with st.spinner("Đang dựng biểu đồ lịch sử chính xác..."):
                hist_df = get_data_until(ticker, interval, pd.Timestamp(chart_ts),
                                         DEFAULT_PERIOD.get(interval))
                if hist_df.empty:
                    st.error("Không có dữ liệu đến thời điểm này.")
                else:
                    fig = build_full_chart(
                        hist_df,
                        title=f"{st.session_state.get('cur_symbol','XAU/USD')} — {interval} @ {chart_ts:%d/%m %H:%M}",
                        options=opt_j,
                    )
                    st.session_state["journal_hist_fig"] = fig
                    st.session_state["journal_hist_ready"] = True

        if st.session_state.get("journal_hist_ready"):
            fig = st.session_state.get("journal_hist_fig")
            if fig:
                st.plotly_chart(fig, use_container_width=True,
                                config=PLOTLY_CONFIG)

    # ---- Nút lưu ----
    st.divider()
    if st.button("💾 Lưu giao dịch", type="primary", use_container_width=True):
        trade = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "chart_timestamp": chart_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "ticker": ticker, "interval": interval,
            "direction": direction, "methods_used": methods,
            "entry_price": entry, "sl": sl, "tp": tp,
            "confidence": confidence,
            "notes": notes,
            "outcome": "" if outcome == "Đang mở" else outcome,
            "pnl_pips": pnl,
        }
        trade_id = save_trade(trade)  # tạo id trước để đặt tên file
        img_path = save_uploaded_image(uploaded, trade_id)
        hist_path = save_historical_chart(
            st.session_state.get("journal_hist_fig"), trade_id
        )
        save_trade({**trade, "id": trade_id,
                    "chart_image_path": img_path, "hist_chart_path": hist_path})
        st.success(f"Đã lưu giao dịch #{trade_id}!")
        st.session_state.pop("journal_hist_ready", None)

    # ---- Lịch sử ----
    st.divider()
    st.subheader("📜 Lịch sử giao dịch")
    hist = load_history()
    if hist.empty:
        st.info("Chưa có giao dịch nào.")
        return

    display = hist[["id", "timestamp", "direction", "confidence",
                    "outcome", "pnl_pips"]].copy()
    st.dataframe(display, use_container_width=True, hide_index=True)

    sel_id = st.selectbox("Xem chi tiết giao dịch",
                          ["—"] + hist["id"].tolist())
    if sel_id and sel_id != "—":
        _render_trade_detail(hist[hist["id"] == sel_id].iloc[0])


def _render_trade_detail(row: pd.Series) -> None:
    """Hiển thị chi tiết + biểu đồ chính xác tại thời điểm log."""
    st.markdown(f"### Chi tiết #{row['id']}")
    c1, c2 = st.columns(2)
    with c1:
        st.write("**Tín hiệu:**", row["direction"])
        st.write("**Phương pháp:**", ", ".join(parse_methods(row["methods_used"])) or "—")
        st.write("**Độ chắc chắn:**", row["confidence"])
        st.write("**Entry/SL/TP:**", f"{row['entry_price']} / {row['sl']} / {row['tp']}")
        st.write("**Kết quả:**", row["outcome"] or "Đang mở",
                 f"({row['pnl_pips']} pips)")
        st.write("**Ghi chú:**", row["notes"] or "—")
    with c2:
        # Tái tạo biểu đồ chính xác tại thời điểm log (không cache figure)
        opt_d = indicator_controls(f"detail_{row['id']}")
        try:
            chart_ts = pd.Timestamp(row["chart_timestamp"])
            hist_df = get_data_until(row["ticker"], row["interval"], chart_ts,
                                     DEFAULT_PERIOD.get(row["interval"]))
            fig = build_full_chart(
                hist_df, title=f"Biểu đồ tại {row['chart_timestamp']}",
                options=opt_d,
            )
            if fig:
                st.plotly_chart(fig, use_container_width=True,
                                config=PLOTLY_CONFIG)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Không tái tạo được biểu đồ: {exc}")

    # Ảnh đã lưu
    for key, cap in [("chart_image_path", "Ảnh upload"),
                     ("hist_chart_path", "Ảnh biểu đồ lịch sử")]:
        p = row.get(key)
        if p and isinstance(p, str) and pd.notna(p):
            try:
                st.image(p, caption=cap, use_container_width=True)
            except Exception:  # noqa: BLE001
                pass

    if st.button("🗑️ Xóa giao dịch này", key=f"del_{row['id']}"):
        delete_trade(row["id"])
        st.success("Đã xóa. Tải lại trang để cập nhật.")


# ============================================================================
# TAB 3 — THỐNG KÊ & TỔNG KẾT
# ============================================================================
def render_tab_stats() -> None:
    st.header("📈 Thống kê & Tổng kết")
    period = st.radio("Khoảng thời gian", ["Tuần", "Tháng", "Tất cả"],
                      horizontal=True)
    period_map = {"Tuần": "week", "Tháng": "month", "Tất cả": "all"}

    hist = load_history()
    df = filter_by_period(hist, period_map[period])
    stats = compute_stats(df)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng lệnh", stats["total"])
    c2.metric("Winrate", f"{stats['winrate']}%")
    c3.metric("Thắng / Thua", f"{stats['wins']} / {stats['losses']}")
    c4.metric("Lệnh chắc thắng", stats["high_conf_wins"])

    st.divider()
    st.subheader("Winrate theo phương pháp")
    perf = method_performance(df)
    if perf.empty:
        st.info("Chưa đủ dữ liệu lệnh đã đóng để thống kê.")
    else:
        st.bar_chart(perf.set_index("method")["winrate"])
        st.subheader("🏆 Top phương pháp hiệu quả nhất")
        st.dataframe(perf, use_container_width=True, hide_index=True)


# ============================================================================
# TAB 4 — CÀI ĐẶT & AI ASSISTANT
# ============================================================================
def render_tab_settings() -> None:
    st.header("⚙️ Cài đặt & AI Assistant")

    st.subheader("📡 Telegram (tùy chọn)")
    st.text_input("Bot Token", type="password", key="tg_token")
    st.text_input("Chat ID", key="tg_chat")
    st.caption("Cấu hình để gửi cảnh báo tín hiệu (mở rộng sau).")

    st.divider()
    st.subheader("🤖 Tạo Prompt AI Phân tích")
    hist = load_history()
    if hist.empty:
        st.info("Chưa có giao dịch để tạo prompt.")
    else:
        sel = st.selectbox("Chọn giao dịch", hist["id"].tolist())
        if st.button("✨ Tạo Prompt AI"):
            row = hist[hist["id"] == sel].iloc[0]
            prompt = _build_ai_prompt(row)
            st.code(prompt, language="markdown")

    st.divider()
    st.subheader("🧹 Quản lý Cache")
    c1, c2, c3 = st.columns(3)
    if c1.button("Xóa cache thô"):
        clear_raw_cache()
        st.success("Đã xóa cache dữ liệu thô.")
    if c2.button("Xóa cache đã xử lý"):
        clear_processed_cache()
        st.success("Đã xóa cache đã xử lý.")
    if c3.button("Xóa toàn bộ cache"):
        clear_all_cache()
        st.success("Đã xóa toàn bộ cache.")

    st.divider()
    st.subheader("📄 Xuất PDF báo cáo")
    period = st.radio("Khoảng", ["Tuần", "Tháng", "Tất cả"], horizontal=True,
                      key="pdf_period")
    period_map = {"Tuần": "week", "Tháng": "month", "Tất cả": "all"}
    if st.button("Xuất PDF"):
        df = filter_by_period(load_history(), period_map[period])
        if df.empty:
            st.warning("Không có dữ liệu để xuất.")
        else:
            path = export_report(df, period_label=period)
            with open(path, "rb") as f:
                st.download_button("⬇️ Tải PDF", f, file_name="bao_cao_xauusd.pdf",
                                   mime="application/pdf")


def _build_ai_prompt(row: pd.Series) -> str:
    """Tạo prompt chi tiết cho AI phân tích sâu từ một trade."""
    methods = ", ".join(parse_methods(row["methods_used"])) or "không ghi"
    return f"""Bạn là chuyên gia phân tích kỹ thuật XAU/USD. Hãy phân tích sâu lệnh sau:

- Thời điểm: {row['chart_timestamp']}
- Khung: {row['interval']}
- Tín hiệu nhận định: {row['direction']}
- Phương pháp vào lệnh: {methods}
- Entry/SL/TP: {row['entry_price']} / {row['sl']} / {row['tp']}
- Độ chắc chắn: {row['confidence']}
- Kết quả: {row['outcome'] or 'đang mở'} ({row['pnl_pips']} pips)
- Ghi chú: {row['notes'] or 'không'}

Yêu cầu:
1. Đánh giá tính hợp lệ của tín hiệu theo quy tắc MACD(24,52,14)/EMA/BB %B then chốt/ADX-DI/Stoch-RSI.
2. Chỉ ra điểm mạnh/yếu của setup.
3. Gợi ý cải thiện quản lý vốn và điểm vào lệnh.
4. Bài học rút ra cho các lệnh tương tự."""


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    st.title("🥇 XAU/USD Professional Trading Journal & Signal Analyzer")
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Phân tích Tín hiệu",
        "📓 Nhật ký Giao dịch",
        "📈 Thống kê & Tổng kết",
        "⚙️ Cài đặt & AI",
    ])
    with tab1:
        render_tab_analyzer()
    with tab2:
        render_tab_journal()
    with tab3:
        render_tab_stats()
    with tab4:
        render_tab_settings()


if __name__ == "__main__":
    main()
