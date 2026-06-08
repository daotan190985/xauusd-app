"""
core/data.py
============
Tải dữ liệu OHLCV từ yfinance với cache tối ưu và xử lý lỗi mạnh mẽ.
Robust yfinance data loader with strong caching & error handling.

- Tự động fallback XAUUSD=X -> GC=F
- Xử lý MultiIndex columns
- Retry 3 lần khi lỗi mạng
- Tách fetch_raw_data() (cache thô) và get_processed_data() (cache sau chỉ báo)
- Hàm xóa cache riêng biệt
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd
import streamlit as st

from core.analyzer import add_indicators

logger = logging.getLogger(__name__)

PRIMARY_TICKER = "XAUUSD=X"
FALLBACK_TICKER = "GC=F"

# ----------------------------------------------------------------------------
# DANH MỤC CẶP GIAO DỊCH / Symbol registry
# Mỗi cặp: ticker chính, ticker fallback, pip_size (1 pip = ? đơn vị giá),
# và số chữ số thập phân hiển thị.
# ----------------------------------------------------------------------------
SYMBOLS = {
    "XAU/USD (Vàng)": {
        "primary": "XAUUSD=X", "fallback": "GC=F",
        "pip": 0.1, "decimals": 2,
    },
    "BTC/USD (Bitcoin)": {
        "primary": "BTC-USD", "fallback": "BTCUSD=X",
        "pip": 1.0, "decimals": 2,
    },
    "GBP/USD": {
        "primary": "GBPUSD=X", "fallback": "GBP=X",
        "pip": 0.0001, "decimals": 5,
    },
    "EUR/USD": {
        "primary": "EURUSD=X", "fallback": "EUR=X",
        "pip": 0.0001, "decimals": 5,
    },
    "USD/JPY": {
        "primary": "USDJPY=X", "fallback": "JPY=X",
        "pip": 0.01, "decimals": 3,
    },
}


def get_symbol_config(label: str) -> dict:
    """Lấy cấu hình của một cặp theo nhãn hiển thị."""
    return SYMBOLS.get(label, SYMBOLS["XAU/USD (Vàng)"])
MAX_RETRIES = 3
RETRY_DELAY = 1.5  # giây

# Period mặc định gợi ý theo interval (đủ nến để tính EMA1200)
DEFAULT_PERIOD = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
    "1h": "730d",
    "90m": "60d",
    "4h": "730d",   # resample từ 1h
    "1d": "5y",
    "1wk": "10y",
}

# Các interval cần resample (yfinance không hỗ trợ trực tiếp): map -> (base, rule)
RESAMPLE_MAP = {
    "4h": ("1h", "4h"),
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Chuẩn hóa cột về Open/High/Low/Close/Volume.
    Xử lý trường hợp yfinance trả về MultiIndex columns.
    """
    if isinstance(df.columns, pd.MultiIndex):
        # Lấy level 0 (tên trường) – bỏ level ticker
        df.columns = df.columns.get_level_values(0)
    # Chuẩn hóa tên cột về dạng Title-case
    rename_map = {c: c.title() for c in df.columns}
    df = df.rename(columns=rename_map)
    # Một số nguồn dùng 'Adj Close'
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep]
    return df


@st.cache_data(ttl=120, max_entries=50, persist="disk", show_spinner=False)
def fetch_raw_data(ticker: str, interval: str, period: str,
                   fallback: Optional[str] = None) -> pd.DataFrame:
    """
    CACHE LỚP 1 — chỉ cache dữ liệu thô từ yfinance.
    Raw OHLCV fetch (cached). Tự fallback ticker + retry.

    fallback: ticker dự phòng (nếu None, dùng FALLBACK_TICKER mặc định cho vàng).
    Cache key = (ticker, interval, period, fallback).
    """
    import yfinance as yf  # import lazy để tránh nặng khi khởi động

    fb = fallback or FALLBACK_TICKER
    tickers_to_try = [ticker]
    if ticker != fb:
        tickers_to_try.append(fb)

    last_err: Optional[Exception] = None
    for tk in tickers_to_try:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info("Tải %s interval=%s period=%s (lần %d)",
                            tk, interval, period, attempt)
                df = yf.download(
                    tickers=tk,
                    interval=interval,
                    period=period,
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                )
                if df is not None and not df.empty:
                    df = _normalize_columns(df)
                    df = df.dropna(how="all")
                    if not df.empty:
                        df.attrs["resolved_ticker"] = tk
                        return df
                logger.warning("Dữ liệu rỗng cho %s (lần %d).", tk, attempt)
            except Exception as exc:  # noqa: BLE001 - cần bắt rộng cho retry
                last_err = exc
                logger.warning("Lỗi tải %s lần %d: %s", tk, attempt, exc)
                time.sleep(RETRY_DELAY * attempt)
        logger.warning("Chuyển sang ticker fallback sau khi thất bại: %s", tk)

    logger.error("Không tải được dữ liệu cho %s. Lỗi cuối: %s", ticker, last_err)
    return pd.DataFrame()


@st.cache_data(ttl=120, max_entries=50, persist="disk", show_spinner=False)
def get_processed_data(
    ticker: str, interval: str, period: Optional[str] = None,
    params_key: Optional[tuple] = None, fallback: Optional[str] = None,
) -> pd.DataFrame:
    """
    CACHE LỚP 2 — cache DataFrame ĐÃ tính đầy đủ chỉ báo.
    Processed (indicator-attached) data, cached separately from raw.
    Hỗ trợ interval cần resample (vd 4h từ 1h) và tham số chỉ báo tùy chỉnh.

    params_key: tuple tham số chỉ báo (từ IndicatorParams.cache_key()).
                Khác params -> cache entry khác -> tự tính lại.
    fallback: ticker dự phòng theo cặp đang chọn.
    """
    from core.analyzer import IndicatorParams, add_indicators as _add

    # Tái tạo IndicatorParams từ tuple (nếu có)
    params = None
    if params_key is not None:
        try:
            params = IndicatorParams(*params_key)
        except TypeError:
            params = None

    # Nếu interval cần resample (vd 4h), lấy base rồi gộp nến
    if interval in RESAMPLE_MAP:
        base_itv, rule = RESAMPLE_MAP[interval]
        base_period = period or DEFAULT_PERIOD.get(interval, "730d")
        raw = fetch_raw_data(ticker, base_itv, base_period, fallback)
        if raw.empty:
            return raw
        raw = _resample_ohlc(raw, rule)
    else:
        period = period or DEFAULT_PERIOD.get(interval, "60d")
        raw = fetch_raw_data(ticker, interval, period, fallback)
        if raw.empty:
            return raw

    processed = _add(raw, params)
    processed.attrs["resolved_ticker"] = raw.attrs.get("resolved_ticker", ticker)
    return processed


def _resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Gộp nến OHLCV lên khung lớn hơn (vd 1h -> 4h)."""
    agg = {"Open": "first", "High": "max", "Low": "min",
           "Close": "last", "Volume": "sum"}
    cols = {k: v for k, v in agg.items() if k in df.columns}
    out = df.resample(rule).agg(cols).dropna(how="any")
    out.attrs["resolved_ticker"] = df.attrs.get("resolved_ticker", "")
    return out


def get_data_until(
    ticker: str,
    interval: str,
    timestamp: pd.Timestamp,
    period: Optional[str] = None,
) -> pd.DataFrame:
    """
    Lấy dữ liệu ĐẾN ĐÚNG một timestamp (cho biểu đồ lịch sử chính xác).
    Historical-accurate slice up to a given timestamp.

    Dùng lại cache get_processed_data rồi cắt theo timestamp,
    sau đó tính LẠI chỉ báo trên đoạn đã cắt để đảm bảo đúng giá trị
    "tại thời điểm đó" (không nhìn thấy tương lai).
    """
    period = period or DEFAULT_PERIOD.get(interval, "60d")
    full = fetch_raw_data(ticker, interval, period)  # dùng cache thô
    if full.empty:
        return full

    ts = pd.Timestamp(timestamp)
    # Đồng bộ timezone giữa index và timestamp
    if full.index.tz is not None and ts.tzinfo is None:
        ts = ts.tz_localize(full.index.tz)
    elif full.index.tz is None and ts.tzinfo is not None:
        ts = ts.tz_localize(None)

    sliced = full[full.index <= ts]
    if sliced.empty:
        return sliced
    # Tính lại chỉ báo CHỈ trên đoạn đã cắt -> không leak dữ liệu tương lai
    return add_indicators(sliced)


# ----------------------------------------------------------------------------
# QUẢN LÝ CACHE / Cache management (xóa riêng biệt, không xóa hết)
# ----------------------------------------------------------------------------
def clear_raw_cache() -> None:
    """Chỉ xóa cache dữ liệu thô."""
    fetch_raw_data.clear()
    logger.info("Đã xóa cache dữ liệu thô.")


def clear_processed_cache() -> None:
    """Chỉ xóa cache dữ liệu đã xử lý chỉ báo."""
    get_processed_data.clear()
    logger.info("Đã xóa cache dữ liệu đã xử lý.")


def clear_all_cache() -> None:
    """Xóa toàn bộ cache dữ liệu (dùng thận trọng)."""
    clear_raw_cache()
    clear_processed_cache()
    logger.info("Đã xóa toàn bộ cache.")
