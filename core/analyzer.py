"""
core/analyzer.py
================
Module phân tích tín hiệu đa khung thời gian cho XAU/USD.
Signal analysis module for XAU/USD multi-timeframe trading.

Triển khai các quy tắc nghiêm ngặt:
- EMA 100 / 200 / 1200 (bias)
- Bollinger Bands %B "then chốt" (band-hold reversal)  <-- quan trọng nhất
- ADX + DI+/DI- với logic đặc biệt cho sideway (<25) và trending (>=25)
- Stochastic custom (42, 5, 3) + RSI(14) confirmation
- Tổng hợp confluence -> MUA MẠNH / MUA / TRUNG LẬP / BÁN / BÁN MẠNH
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Cấu hình tham số chỉ báo / Indicator configuration constants
# ----------------------------------------------------------------------------
EMA_FAST = 100
EMA_MID = 200
EMA_SLOW = 1200

BB_PERIOD = 20
BB_STD = 2.0

ADX_PERIOD = 14
ADX_THRESHOLD = 25.0  # ngưỡng phân biệt sideway / trending

STOCH_K = 42
STOCH_SMOOTH_K = 5
STOCH_SMOOTH_D = 3

RSI_PERIOD = 14

# MACD (24, 52, 14) — chỉ báo XU HƯỚNG CHÍNH theo yêu cầu
MACD_FAST = 24
MACD_SLOW = 52
MACD_SIGNAL = 14

# Keltner Channel (42, 1) — kênh biến động dựa trên ATR
KC_PERIOD = 42
KC_MULT = 1.0

# Cửa sổ kiểm tra "then chốt" (số nến gần nhất xét band-hold)
BAND_HOLD_WINDOW = 20
BAND_HOLD_MIN_TOUCHES = 2  # >=2 lần test band


# ----------------------------------------------------------------------------
# Cấu trúc dữ liệu kết quả / Result dataclasses
# ----------------------------------------------------------------------------
@dataclass
class IndicatorParams:
    """
    Tham số TÙY CHỈNH cho toàn bộ chỉ báo (giống chỉnh trên TradingView).
    User-tunable parameters for every indicator. Mặc định = giá trị chuẩn.
    """
    # EMA lengths
    ema_fast: int = EMA_FAST       # 100
    ema_mid: int = EMA_MID         # 200
    ema_slow: int = EMA_SLOW       # 1200
    # Bollinger
    bb_period: int = BB_PERIOD     # 20
    bb_std: float = BB_STD         # 2.0
    # ADX
    adx_period: int = ADX_PERIOD   # 14
    # MACD
    macd_fast: int = MACD_FAST     # 24
    macd_slow: int = MACD_SLOW     # 52
    macd_signal: int = MACD_SIGNAL # 14
    # Stochastic
    stoch_k: int = STOCH_K         # 42
    stoch_smooth_k: int = STOCH_SMOOTH_K  # 5
    stoch_smooth_d: int = STOCH_SMOOTH_D  # 3
    # RSI
    rsi_period: int = RSI_PERIOD   # 14
    # Keltner
    kc_period: int = KC_PERIOD     # 42
    kc_mult: float = KC_MULT       # 1.0

    def cache_key(self) -> tuple:
        """Tuple để dùng làm khóa cache (hashable)."""
        return (self.ema_fast, self.ema_mid, self.ema_slow,
                self.bb_period, self.bb_std, self.adx_period,
                self.macd_fast, self.macd_slow, self.macd_signal,
                self.stoch_k, self.stoch_smooth_k, self.stoch_smooth_d,
                self.rsi_period, self.kc_period, self.kc_mult)


@dataclass
class SignalResult:
    """Kết quả phân tích cho MỘT khung thời gian / Single-timeframe signal."""

    interval: str
    direction: str = "TRUNG LẬP"          # MUA MẠNH / MUA / TRUNG LẬP / BÁN / BÁN MẠNH
    score: float = 0.0                     # điểm confluence (-100..+100)
    reasons: list[str] = field(default_factory=list)
    last_price: float = 0.0
    last_time: Optional[pd.Timestamp] = None
    # Snapshot các chỉ báo tại nến cuối (dùng cho hiển thị / AI prompt)
    metrics: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# CÁC HÀM TÍNH CHỈ BÁO / Indicator computation helpers
# ----------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False, min_periods=1).mean()


def bollinger_bands(
    close: pd.Series, period: int = BB_PERIOD, n_std: float = BB_STD
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Tính Bollinger Bands và %B.
    Trả về: (middle, upper, lower, percent_b)
    %B = (close - lower) / (upper - lower)
    """
    mid = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + n_std * std
    lower = mid - n_std * std
    width = (upper - lower).replace(0, np.nan)
    percent_b = (close - lower) / width
    return mid, upper, lower, percent_b


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Relative Strength Index theo phương pháp Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    close: pd.Series,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD (24, 52, 14) — chỉ báo xu hướng chính.
    Trả về (macd_line, signal_line, histogram).

    macd_line   = EMA(fast) - EMA(slow)
    signal_line = EMA(macd_line, signal)
    histogram   = macd_line - signal_line
    """
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=1).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=1).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=1).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def keltner_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = KC_PERIOD,
    mult: float = KC_MULT,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Keltner Channel (42, 1).
    Trả về (middle, upper, lower).

    middle = EMA(close, period)
    ATR    = EMA(True Range, period)
    upper  = middle + mult * ATR
    lower  = middle - mult * ATR
    """
    mid = close.ewm(span=period, adjust=False, min_periods=1).mean()
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False, min_periods=1).mean()
    upper = mid + mult * atr
    lower = mid - mult * atr
    return mid, upper, lower


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = STOCH_K,
    smooth_k: int = STOCH_SMOOTH_K,
    smooth_d: int = STOCH_SMOOTH_D,
) -> tuple[pd.Series, pd.Series]:
    """
    Stochastic custom (42, 5, 3).
    Trả về (%K đã làm mượt, %D).
    """
    lowest = low.rolling(window=k_period, min_periods=k_period).min()
    highest = high.rolling(window=k_period, min_periods=k_period).max()
    rng = (highest - lowest).replace(0, np.nan)
    raw_k = 100 * (close - lowest) / rng
    k = raw_k.rolling(window=smooth_k, min_periods=1).mean()
    d = k.rolling(window=smooth_d, min_periods=1).mean()
    return k, d


def adx_dmi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = ADX_PERIOD,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Tính ADX, DI+, DI- theo Wilder.
    Trả về (adx, di_plus, di_minus).
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder smoothing qua EMA alpha = 1/period
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_dm_s = pd.Series(plus_dm, index=high.index).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    minus_dm_s = pd.Series(minus_dm, index=high.index).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()

    di_plus = 100 * plus_dm_s / atr.replace(0, np.nan)
    di_minus = 100 * minus_dm_s / atr.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx, di_plus, di_minus


# ----------------------------------------------------------------------------
# TÍNH TOÀN BỘ CHỈ BÁO / Compute all indicators onto the DataFrame
# ----------------------------------------------------------------------------
def add_indicators(df: pd.DataFrame,
                   params: Optional["IndicatorParams"] = None) -> pd.DataFrame:
    """
    Nhận DataFrame OHLCV chuẩn (cột: Open, High, Low, Close, Volume),
    trả về DataFrame mới đã gắn tất cả các cột chỉ báo.

    params: tham số tùy chỉnh cho từng chỉ báo (None = dùng mặc định).
    """
    if df is None or df.empty:
        return df

    p = params or IndicatorParams()
    out = df.copy()
    close, high, low = out["Close"], out["High"], out["Low"]

    # EMA bias (length tùy chỉnh)
    out["EMA_100"] = ema(close, p.ema_fast)
    out["EMA_200"] = ema(close, p.ema_mid)
    out["EMA_1200"] = ema(close, p.ema_slow)

    # Bollinger + %B
    out["BB_MID"], out["BB_UP"], out["BB_LOW"], out["PCT_B"] = bollinger_bands(
        close, p.bb_period, p.bb_std)

    # ADX / DI
    out["ADX"], out["DI_PLUS"], out["DI_MINUS"] = adx_dmi(
        high, low, close, p.adx_period)

    # Stochastic + RSI
    out["STOCH_K"], out["STOCH_D"] = stochastic(
        high, low, close, p.stoch_k, p.stoch_smooth_k, p.stoch_smooth_d)
    out["RSI"] = rsi(close, p.rsi_period)

    # MACD (xu hướng chính)
    out["MACD"], out["MACD_SIGNAL"], out["MACD_HIST"] = macd(
        close, p.macd_fast, p.macd_slow, p.macd_signal)

    # Keltner Channel
    out["KC_MID"], out["KC_UP"], out["KC_LOW"] = keltner_channel(
        high, low, close, p.kc_period, p.kc_mult)

    return out


# ----------------------------------------------------------------------------
# LOGIC TÍN HIỆU "THEN CHỐT" %B / Band-hold reversal detection
# ----------------------------------------------------------------------------
def detect_band_hold(
    df: pd.DataFrame,
    window: int = BAND_HOLD_WINDOW,
    min_touches: int = BAND_HOLD_MIN_TOUCHES,
) -> dict:
    """
    Phát hiện tín hiệu "then chốt" – quy tắc QUAN TRỌNG NHẤT.

    Định nghĩa:
      - Giá test Lower/Upper band liên tục (>= min_touches trong ~window nến gần nhất)
      - %B KHÔNG chạm 0 (lower) hoặc 1 (upper) -> band "giữ" được
      - Có dấu hiệu đảo chiều ở các nến cuối

    Trả về dict:
      {
        'lower_hold': bool,   # giữ lower band -> tín hiệu MUA
        'upper_hold': bool,   # giữ upper band -> tín hiệu BÁN
        'lower_touches': int,
        'upper_touches': int,
        'note': str
      }
    """
    result = {
        "lower_hold": False,
        "upper_hold": False,
        "lower_touches": 0,
        "upper_touches": 0,
        "note": "",
    }
    if df is None or len(df) < window:
        return result

    recent = df.tail(window)
    pct_b = recent["PCT_B"].dropna()
    if pct_b.empty:
        return result

    # "Test" lower: %B đi xuống vùng <= 0.15 nhưng KHÔNG <= 0 (không phá band)
    lower_touch = (pct_b <= 0.15) & (pct_b > 0.0)
    # "Test" upper: %B đi lên vùng >= 0.85 nhưng KHÔNG >= 1 (không phá band)
    upper_touch = (pct_b >= 0.85) & (pct_b < 1.0)

    result["lower_touches"] = int(lower_touch.sum())
    result["upper_touches"] = int(upper_touch.sum())

    # Dấu hiệu đảo chiều: %B của 1-2 nến cuối bật lên (lower) hoặc rơi xuống (upper)
    last_vals = pct_b.tail(3).tolist()
    reversing_up = len(last_vals) >= 2 and last_vals[-1] > last_vals[0]
    reversing_down = len(last_vals) >= 2 and last_vals[-1] < last_vals[0]

    # Không có nến nào phá hẳn band trong cửa sổ (then chốt giữ vững)
    no_break_low = (pct_b <= 0.0).sum() == 0
    no_break_up = (pct_b >= 1.0).sum() == 0

    if (
        result["lower_touches"] >= min_touches
        and no_break_low
        and reversing_up
    ):
        result["lower_hold"] = True
        result["note"] = (
            f"BB Lower Hold: {result['lower_touches']} lần test lower band, "
            f"%B chưa phá 0, đang đảo chiều lên -> tín hiệu MUA mạnh."
        )

    if (
        result["upper_touches"] >= min_touches
        and no_break_up
        and reversing_down
    ):
        result["upper_hold"] = True
        result["note"] = (
            f"BB Upper Hold: {result['upper_touches']} lần test upper band, "
            f"%B chưa phá 1, đang đảo chiều xuống -> tín hiệu BÁN mạnh."
        )

    return result


def detect_macd_signal(df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    Phân tích MACD (24,52,14) — chỉ báo XU HƯỚNG CHÍNH.

    Quy tắc theo yêu cầu:
      - Histogram > 0 -> xu hướng tăng (bullish bias)
      - Histogram < 0 -> xu hướng giảm (bearish bias)
      - Histogram chưa vượt 0 -> vẫn giữ xu hướng theo histogram hiện tại
      - Histogram TÁCH RA khỏi signal (độ lớn tăng dần) -> dấu hiệu đảo chiều mạnh

    Trả về dict:
      {
        'bias': 'bullish'/'bearish'/'neutral',
        'hist': float,                # giá trị histogram hiện tại
        'expanding': bool,            # histogram đang tách xa (momentum mạnh)
        'cross_up': bool,             # MACD cắt lên signal
        'cross_down': bool,           # MACD cắt xuống signal
        'note': str
      }
    """
    res = {
        "bias": "neutral", "hist": 0.0, "expanding": False,
        "cross_up": False, "cross_down": False, "note": "",
    }
    if df is None or len(df) < lookback + 2 or "MACD_HIST" not in df:
        return res

    hist = df["MACD_HIST"].dropna()
    macd_line = df["MACD"].dropna()
    signal_line = df["MACD_SIGNAL"].dropna()
    if hist.empty:
        return res

    cur_hist = float(hist.iloc[-1])
    res["hist"] = round(cur_hist, 4)

    # Bias theo dấu histogram
    if cur_hist > 0:
        res["bias"] = "bullish"
    elif cur_hist < 0:
        res["bias"] = "bearish"

    # Histogram "tách ra khỏi signal": |histogram| tăng dần qua các nến gần nhất
    recent = hist.tail(3).abs().tolist()
    if len(recent) == 3 and recent[2] > recent[1] > recent[0]:
        res["expanding"] = True

    # MACD cắt signal (đảo chiều)
    diff = (macd_line - signal_line).tail(lookback + 1).dropna()
    if len(diff) >= 2:
        signs = np.sign(diff.values)
        for i in range(1, len(signs)):
            if signs[i - 1] < 0 <= signs[i]:
                res["cross_up"] = True
            elif signs[i - 1] > 0 >= signs[i]:
                res["cross_down"] = True

    # Ghi chú
    if res["bias"] == "bullish":
        note = "MACD Histogram > 0 (xu hướng tăng)"
    elif res["bias"] == "bearish":
        note = "MACD Histogram < 0 (xu hướng giảm)"
    else:
        note = "MACD Histogram quanh 0 (trung lập)"
    if res["expanding"]:
        note += " + Histogram tách khỏi signal (momentum mạnh)"
    res["note"] = note
    return res


def detect_di_cross(df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    Phát hiện DI+ và DI- cắt nhau gần đây.
    Trả về {'cross_up': bool, 'cross_down': bool} (bullish / bearish cross).
    """
    res = {"cross_up": False, "cross_down": False}
    if df is None or len(df) < lookback + 1:
        return res
    di_p = df["DI_PLUS"]
    di_m = df["DI_MINUS"]
    diff = (di_p - di_m).tail(lookback + 1).dropna()
    if len(diff) < 2:
        return res
    signs = np.sign(diff.values)
    for i in range(1, len(signs)):
        if signs[i - 1] < 0 <= signs[i]:
            res["cross_up"] = True       # DI+ vượt lên DI- -> bullish
        elif signs[i - 1] > 0 >= signs[i]:
            res["cross_down"] = True     # DI- vượt lên DI+ -> bearish
    return res


# ----------------------------------------------------------------------------
# CHẤM ĐIỂM TÍN HIỆU 1 KHUNG / Score a single timeframe
# ----------------------------------------------------------------------------
def analyze_timeframe(df: pd.DataFrame, interval: str) -> SignalResult:
    """
    Phân tích một khung thời gian -> SignalResult.
    Điểm dương = thiên hướng MUA, điểm âm = thiên hướng BÁN.
    """
    res = SignalResult(interval=interval)
    if df is None or df.empty:
        res.reasons.append("Không có dữ liệu.")
        return res

    last = df.iloc[-1]
    res.last_price = float(last["Close"])
    res.last_time = df.index[-1]

    score = 0.0

    # --- 1) EMA Alignment (bias) ---
    ema100, ema200, ema1200 = last["EMA_100"], last["EMA_200"], last["EMA_1200"]
    price = last["Close"]
    if pd.notna(ema100) and pd.notna(ema200) and pd.notna(ema1200):
        if price > ema100 > ema200 > ema1200:
            score += 25
            res.reasons.append("EMA Alignment tăng mạnh (Giá>EMA100>EMA200>EMA1200).")
        elif price < ema100 < ema200 < ema1200:
            score -= 25
            res.reasons.append("EMA Alignment giảm mạnh (Giá<EMA100<EMA200<EMA1200).")
        elif price > ema200:
            score += 8
            res.reasons.append("Giá trên EMA200 (bias tăng nhẹ).")
        elif price < ema200:
            score -= 8
            res.reasons.append("Giá dưới EMA200 (bias giảm nhẹ).")

    # --- 2) BB %B "then chốt" (quan trọng nhất, trọng số cao) ---
    band = detect_band_hold(df)
    res.metrics["band_hold"] = band
    if band["lower_hold"]:
        score += 35
        res.reasons.append(band["note"])
    if band["upper_hold"]:
        score -= 35
        res.reasons.append(band["note"])

    # --- 2b) MACD (24,52,14) — XU HƯỚNG CHÍNH (trọng số cao) ---
    macd_sig = detect_macd_signal(df)
    res.metrics["macd"] = macd_sig
    if macd_sig["bias"] == "bullish":
        score += 22
        res.reasons.append(macd_sig["note"])
        if macd_sig["expanding"]:
            score += 8  # momentum tăng mạnh
        if macd_sig["cross_up"]:
            score += 6
    elif macd_sig["bias"] == "bearish":
        score -= 22
        res.reasons.append(macd_sig["note"])
        if macd_sig["expanding"]:
            score -= 8
        if macd_sig["cross_down"]:
            score -= 6

    # --- 3) ADX đặc biệt ---
    adx = last["ADX"]
    di_cross = detect_di_cross(df)
    res.metrics["di_cross"] = di_cross
    if pd.notna(adx):
        if adx < ADX_THRESHOLD:
            # Sideway: CHỈ lấy tín hiệu khi DI cross
            if di_cross["cross_up"]:
                score += 18
                res.reasons.append(f"ADX<25 (sideway) + DI+ cắt lên DI- -> MUA.")
            elif di_cross["cross_down"]:
                score -= 18
                res.reasons.append(f"ADX<25 (sideway) + DI- cắt lên DI+ -> BÁN.")
            else:
                res.reasons.append(f"ADX={adx:.1f}<25 sideway, không có DI cross -> bỏ qua.")
        else:
            # Trending: ưu tiên alignment + DI cross gần đây
            if last["DI_PLUS"] > last["DI_MINUS"]:
                score += 15
                res.reasons.append(f"ADX={adx:.1f}>=25 trending, DI+>DI- -> ưu tiên MUA.")
                if di_cross["cross_up"]:
                    score += 7
            else:
                score -= 15
                res.reasons.append(f"ADX={adx:.1f}>=25 trending, DI->DI+ -> ưu tiên BÁN.")
                if di_cross["cross_down"]:
                    score -= 7

    # --- 4) Stochastic + RSI confirmation ---
    stoch_k, stoch_d, rsi_v = last["STOCH_K"], last["STOCH_D"], last["RSI"]
    if pd.notna(stoch_k) and pd.notna(stoch_d):
        if stoch_k < 20 and stoch_k > stoch_d:
            score += 10
            res.reasons.append("Stochastic vùng quá bán & %K cắt lên %D -> xác nhận MUA.")
        elif stoch_k > 80 and stoch_k < stoch_d:
            score -= 10
            res.reasons.append("Stochastic vùng quá mua & %K cắt xuống %D -> xác nhận BÁN.")
    if pd.notna(rsi_v):
        if rsi_v < 30:
            score += 7
            res.reasons.append(f"RSI={rsi_v:.0f} quá bán -> hỗ trợ MUA.")
        elif rsi_v > 70:
            score -= 7
            res.reasons.append(f"RSI={rsi_v:.0f} quá mua -> hỗ trợ BÁN.")

    # --- Tổng hợp -> hướng ---
    score = float(np.clip(score, -100, 100))
    res.score = round(score, 1)
    res.direction = _score_to_direction(score)

    # Lưu snapshot metrics cho hiển thị / prompt
    res.metrics.update(
        {
            "price": round(float(price), 2),
            "ema_100": _safe(ema100),
            "ema_200": _safe(ema200),
            "ema_1200": _safe(ema1200),
            "pct_b": _safe(last["PCT_B"], 3),
            "adx": _safe(adx),
            "di_plus": _safe(last["DI_PLUS"]),
            "di_minus": _safe(last["DI_MINUS"]),
            "stoch_k": _safe(stoch_k),
            "stoch_d": _safe(stoch_d),
            "rsi": _safe(rsi_v),
            "macd": _safe(last["MACD"], 4),
            "macd_signal": _safe(last["MACD_SIGNAL"], 4),
            "macd_hist": _safe(last["MACD_HIST"], 4),
        }
    )
    return res


def _score_to_direction(score: float) -> str:
    """Ánh xạ điểm -> nhãn tín hiệu tiếng Việt."""
    if score >= 45:
        return "MUA MẠNH"
    if score >= 15:
        return "MUA"
    if score <= -45:
        return "BÁN MẠNH"
    if score <= -15:
        return "BÁN"
    return "TRUNG LẬP"


def _safe(val, ndigits: int = 2):
    """Làm tròn an toàn, trả None nếu NaN."""
    try:
        if pd.isna(val):
            return None
        return round(float(val), ndigits)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# TỔNG HỢP CONFLUENCE ĐA KHUNG / Multi-timeframe confluence
# ----------------------------------------------------------------------------
# Trọng số khung thời gian: khung lớn quan trọng hơn (M15 trở lên)
TF_WEIGHTS = {
    "1m": 0.4,
    "5m": 0.7,
    "15m": 1.0,
    "30m": 1.2,
    "60m": 1.5,
    "1h": 1.5,
    "90m": 1.6,
    "1d": 2.0,
    "1wk": 2.2,
}


def aggregate_signals(results: dict[str, SignalResult]) -> dict:
    """
    Tổng hợp các SignalResult của nhiều khung -> tín hiệu cuối.
    Tín hiệu vào lệnh M1 dựa trên confluence từ M15 trở lên (trọng số cao hơn).

    Trả về:
      {
        'direction': str, 'confluence_score': float,
        'weighted_score': float, 'detail': {interval: score}
      }
    """
    if not results:
        return {"direction": "TRUNG LẬP", "confluence_score": 0.0,
                "weighted_score": 0.0, "detail": {}}

    total_w = 0.0
    weighted = 0.0
    detail = {}
    for itv, r in results.items():
        w = TF_WEIGHTS.get(itv, 1.0)
        weighted += r.score * w
        total_w += w
        detail[itv] = r.score

    weighted_score = weighted / total_w if total_w else 0.0
    weighted_score = float(np.clip(weighted_score, -100, 100))

    return {
        "direction": _score_to_direction(weighted_score),
        "confluence_score": round(weighted_score, 1),
        "weighted_score": round(weighted_score, 1),
        "detail": detail,
    }


# ============================================================================
# PHÁT HIỆN VÙNG ĐẢO CHIỀU / Reversal-zone detection
# ============================================================================
def compute_reversal_zones(
    df: pd.DataFrame,
    fib_lookback: int = 120,
    touch_tolerance: float = 0.003,  # 0.3% coi là "chạm" vùng
) -> dict:
    """
    Tính các VÙNG ĐẢO CHIỀU tiềm năng dựa trên:
      1. Fibonacci 161.8% extension (giá xuống/lên "quá đà" tới mức này -> dễ đảo)
      2. EMA 200 và EMA 1200 (hỗ trợ/kháng cự ĐỘNG)

    Trả về dict:
      {
        'price': giá hiện tại,
        'zones': [ {name, level, type('support'/'resistance'), touched(bool), dist_pct} ... ],
        'in_zone': bool,            # giá ĐANG chạm ít nhất 1 vùng
        'nearest': zone gần nhất,
        'note': mô tả
      }
    """
    res = {"price": 0.0, "zones": [], "in_zone": False,
           "nearest": None, "note": ""}
    if df is None or df.empty:
        return res

    price = float(df["Close"].iloc[-1])
    res["price"] = round(price, 2)
    zones = []

    # --- 1) Fibonacci 161.8% extension ---
    recent = df.tail(fib_lookback)
    hi = float(recent["High"].max())
    lo = float(recent["Low"].min())
    if hi > lo:
        diff = hi - lo
        idx_hi = recent["High"].idxmax()
        idx_lo = recent["Low"].idxmin()
        uptrend = idx_lo < idx_hi  # đáy trước đỉnh -> sóng tăng
        if uptrend:
            # Extension lên trên đỉnh -> kháng cự (giá tăng quá đà)
            fib_1618 = lo + diff * 1.618
            ztype = "resistance"
        else:
            # Extension xuống dưới đáy -> hỗ trợ (giá giảm quá đà)
            fib_1618 = hi - diff * 1.618
            ztype = "support"
        zones.append({"name": "Fib 161.8% ext", "level": round(fib_1618, 2),
                      "type": ztype})

    # --- 2) EMA 200 / EMA 1200 động ---
    for col, label in [("EMA_200", "EMA 200"), ("EMA_1200", "EMA 1200")]:
        if col in df and pd.notna(df[col].iloc[-1]):
            lvl = float(df[col].iloc[-1])
            ztype = "support" if price >= lvl else "resistance"
            zones.append({"name": label, "level": round(lvl, 2), "type": ztype})

    # --- Đánh dấu vùng nào đang bị "chạm" ---
    nearest = None
    nearest_dist = 1e9
    for z in zones:
        dist_pct = abs(price - z["level"]) / price if price else 1.0
        z["dist_pct"] = round(dist_pct * 100, 2)
        z["touched"] = dist_pct <= touch_tolerance
        if z["touched"]:
            res["in_zone"] = True
        if dist_pct < nearest_dist:
            nearest_dist = dist_pct
            nearest = z

    res["zones"] = zones
    res["nearest"] = nearest
    if res["in_zone"]:
        touched = [z["name"] for z in zones if z["touched"]]
        res["note"] = f"Giá đang CHẠM vùng: {', '.join(touched)} -> theo dõi sát M1."
    elif nearest:
        res["note"] = (f"Vùng gần nhất: {nearest['name']} @ {nearest['level']} "
                       f"(cách {nearest['dist_pct']}%).")
    return res


def m1_confluence_check(df_m1: pd.DataFrame) -> dict:
    """
    Kiểm tra HỘI TỤ TÍN HIỆU trên khung M1 khi giá đã chạm vùng đảo chiều.
    Theo đúng nguyên tắc: %BB then chốt + Stochastic + MACD tách histogram.

    Trả về dict:
      {
        'score': int (0-3, số tín hiệu hội tụ),
        'bb': bool, 'stoch': bool, 'macd': bool,
        'direction': 'MUA'/'BÁN'/None,
        'ready': bool,   # >=2/3 tín hiệu cùng hướng -> sẵn sàng vào
        'note': str
      }
    """
    out = {"score": 0, "bb": False, "stoch": False, "macd": False,
           "direction": None, "ready": False, "note": ""}
    if df_m1 is None or len(df_m1) < 20:
        out["note"] = "Chưa đủ dữ liệu M1."
        return out

    last = df_m1.iloc[-1]
    buy_signals = 0
    sell_signals = 0

    # 1) %BB then chốt
    band = detect_band_hold(df_m1)
    if band["lower_hold"]:
        out["bb"] = True; buy_signals += 1
    elif band["upper_hold"]:
        out["bb"] = True; sell_signals += 1

    # 2) Stochastic: quá bán + %K cắt lên (mua) / quá mua + cắt xuống (bán)
    sk, sd = last.get("STOCH_K"), last.get("STOCH_D")
    if pd.notna(sk) and pd.notna(sd):
        if sk < 20 and sk > sd:
            out["stoch"] = True; buy_signals += 1
        elif sk > 80 and sk < sd:
            out["stoch"] = True; sell_signals += 1

    # 3) MACD tách histogram (momentum mạnh theo hướng)
    macd_sig = detect_macd_signal(df_m1)
    if macd_sig["expanding"]:
        if macd_sig["bias"] == "bullish":
            out["macd"] = True; buy_signals += 1
        elif macd_sig["bias"] == "bearish":
            out["macd"] = True; sell_signals += 1

    if buy_signals > sell_signals:
        out["direction"] = "MUA"; out["score"] = buy_signals
    elif sell_signals > buy_signals:
        out["direction"] = "BÁN"; out["score"] = sell_signals

    out["ready"] = out["score"] >= 2
    parts = []
    if out["bb"]: parts.append("%BB then chốt ✓")
    if out["stoch"]: parts.append("Stochastic ✓")
    if out["macd"]: parts.append("MACD tách histogram ✓")
    if out["ready"]:
        out["note"] = (f"M1 HỘI TỤ {out['score']}/3 ({out['direction']}): "
                       f"{', '.join(parts)} -> cân nhắc vào lệnh.")
    elif parts:
        out["note"] = f"M1 mới có {out['score']}/3: {', '.join(parts)} -> chờ thêm."
    else:
        out["note"] = "M1 chưa có tín hiệu hội tụ."
    return out


# ============================================================================
# SCALPING THEO EMA 1200 KHUNG LỚN / EMA1200 reversal scalping
# ============================================================================
def ema1200_scalp_signal(
    df_big: pd.DataFrame,
    df_m1: pd.DataFrame,
    near_pct: float = 0.0025,   # <=0.25% coi là "GẦN" EMA1200
    touch_pct: float = 0.0008,  # <=0.08% coi là "CHẠM" (xác nhận)
) -> dict:
    """
    Tín hiệu scalping: giá khung lớn tiến sát / chạm EMA1200 -> điểm đảo chiều.
    Báo 2 MỨC:
      - 'near'    : giá tiến gần EMA1200 (cảnh báo sớm, chuẩn bị)
      - 'confirm' : giá chạm EMA1200 + M1 hội tụ (%BB+Stoch+MACD) -> vào lệnh

    Trả về dict:
      {
        'level': giá EMA1200 khung lớn,
        'price': giá hiện tại,
        'dist_pct': khoảng cách %,
        'stage': 'none' / 'near' / 'confirm',
        'direction': 'MUA'/'BÁN'/None,   # đảo lên (hỗ trợ) hay xuống (kháng cự)
        'm1': dict kết quả m1_confluence_check (nếu có),
        'note': str
      }
    """
    out = {"level": None, "price": None, "dist_pct": None,
           "stage": "none", "direction": None, "m1": None, "note": ""}
    if df_big is None or df_big.empty or "EMA_1200" not in df_big:
        out["note"] = "Chưa đủ dữ liệu khung lớn cho EMA1200."
        return out

    ema1200 = df_big["EMA_1200"].iloc[-1]
    price = float(df_big["Close"].iloc[-1])
    if pd.isna(ema1200):
        out["note"] = "EMA1200 chưa đủ nến để tính (cần nhiều dữ liệu)."
        return out

    ema1200 = float(ema1200)
    out["level"] = round(ema1200, 2)
    out["price"] = round(price, 2)
    dist = abs(price - ema1200) / price if price else 1.0
    out["dist_pct"] = round(dist * 100, 3)

    # Hướng đảo kỳ vọng: giá trên EMA1200 -> EMA1200 là hỗ trợ -> đảo LÊN (MUA)
    #                    giá dưới EMA1200 -> EMA1200 là kháng cự -> đảo XUỐNG (BÁN)
    expected_dir = "MUA" if price >= ema1200 else "BÁN"
    out["direction"] = expected_dir

    if dist <= touch_pct:
        # CHẠM -> kiểm tra M1 để xác nhận
        m1 = m1_confluence_check(df_m1) if df_m1 is not None else None
        out["m1"] = m1
        if m1 and m1["ready"] and m1["direction"] == expected_dir:
            out["stage"] = "confirm"
            out["note"] = (f"✅ XÁC NHẬN scalp {expected_dir}: giá CHẠM EMA1200 "
                           f"({out['level']}) + M1 hội tụ {m1['score']}/3.")
        else:
            out["stage"] = "near"
            m1_txt = f" (M1: {m1['note']})" if m1 else ""
            out["note"] = (f"⚠️ Giá CHẠM EMA1200 ({out['level']}) nhưng M1 chưa "
                           f"hội tụ {expected_dir}.{m1_txt}")
    elif dist <= near_pct:
        out["stage"] = "near"
        out["note"] = (f"⏳ GẦN EMA1200 ({out['level']}), cách {out['dist_pct']}% "
                       f"-> chuẩn bị scalp {expected_dir}, theo dõi M1.")
    else:
        out["note"] = (f"Giá cách EMA1200 ({out['level']}) {out['dist_pct']}% "
                       f"-> chưa tới điểm đảo.")
    return out


# ============================================================================
# PHÁT HIỆN SÓNG HỒI / ĐIỂM ĐẢO TỔNG HỢP (cho scalp 50 pip)
# Pullback / reversal detection from multi-TF band confluence
# ============================================================================
def detect_outside_band(df: pd.DataFrame) -> dict:
    """
    Phát hiện giá ĐÓNG NGOÀI Bollinger Band (tín hiệu quá đà -> dễ hồi).
    Trả về {'outside': bool, 'side': 'above'/'below'/None, 'pct_b': float}.
    """
    out = {"outside": False, "side": None, "pct_b": None}
    if df is None or df.empty or "PCT_B" not in df:
        return out
    pb = df["PCT_B"].iloc[-1]
    if pd.isna(pb):
        return out
    out["pct_b"] = round(float(pb), 3)
    if pb > 1.0:
        out["outside"] = True; out["side"] = "above"   # ngoài band trên -> dễ hồi XUỐNG
    elif pb < 0.0:
        out["outside"] = True; out["side"] = "below"   # ngoài band dưới -> dễ hồi LÊN
    return out


def collect_resistance_levels(
    frames: dict,
    touch_pct: float = 0.0015,  # 0.15% coi là "chạm" band
) -> list[dict]:
    """
    Thu thập TẤT CẢ các band/đường khung lớn làm kháng cự/hỗ trợ tiềm năng.

    frames: dict {tf: df_đã_có_chỉ_báo}, ví dụ {'4h':..,'60m':..,'15m':..}
    Theo mô tả của anh, quét:
      - H4: BB giữa (mid), BB trên (up), BB dưới (low)
      - H1: BB trên, BB dưới, EMA 200
      - M15: EMA 1200
    Giá tham chiếu lấy từ nến cuối của khung NHỎ NHẤT có trong frames.

    Trả về list các level: [{tf, name, level, type, dist_pct, touched}, ...]
    """
    # Bản đồ band cần lấy theo từng khung
    spec = {
        "4h":  [("BB_MID", "H4 BB giữa"), ("BB_UP", "H4 BB trên"),
                ("BB_LOW", "H4 BB dưới")],
        "60m": [("BB_UP", "H1 BB trên"), ("BB_LOW", "H1 BB dưới"),
                ("EMA_200", "H1 EMA200")],
        "15m": [("EMA_1200", "M15 EMA1200")],
    }
    # Giá hiện tại: ưu tiên khung nhỏ nhất
    price = None
    for tf in ["15m", "60m", "4h"]:
        if tf in frames and not frames[tf].empty:
            price = float(frames[tf]["Close"].iloc[-1])
            break
    if price is None:
        return []

    levels = []
    for tf, items in spec.items():
        df = frames.get(tf)
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        for col, label in items:
            if col not in df or pd.isna(last[col]):
                continue
            lvl = float(last[col])
            dist = abs(price - lvl) / price if price else 1.0
            levels.append({
                "tf": tf, "name": label, "level": round(lvl, 2),
                "type": "support" if price >= lvl else "resistance",
                "dist_pct": round(dist * 100, 3),
                "touched": dist <= touch_pct,
            })
    return levels


def detect_pullback_zone(
    frames: dict,
    df_m1: pd.DataFrame,
    cluster_pct: float = 0.0020,   # các band cách nhau <=0.2% coi là 1 cụm
    adx_narrow: float = 22.0,      # ADX M1 dưới mức này = hẹp (sideway/hồi)
) -> dict:
    """
    PHÁT HIỆN SÓNG HỒI / ĐIỂM ĐẢO TỔNG HỢP cho scalp.

    Logic (theo mô tả của anh):
      1. Thu thập band khung lớn (H4/H1/M15) -> tìm band giá đang chạm.
      2. CHỈ CẦN chạm 1 band lớn là đủ báo; nhiều band trùng (cụm) -> mạnh hơn.
      3. ƯU THẾ khi giá đang ĐÓNG NGOÀI band BB (M5/M15/H1) -> quá đà, dễ hồi.
      4. Xác nhận bằng M1 ADX HẸP (sideway = giá khựng để đảo) + DI co lại.

    Trả về dict:
      {
        'detected': bool,            # có điểm hồi/đảo đáng chú ý
        'strength': int,             # độ mạnh (số yếu tố hội tụ)
        'direction': 'MUA'/'BÁN'/None,
        'touched_levels': [...],     # band đang bị chạm
        'outside_band': {tf: side},  # khung nào giá ngoài band
        'm1_adx_narrow': bool,
        'm1_adx': float,
        'note': str,
      }
    """
    out = {"detected": False, "strength": 0, "direction": None,
           "touched_levels": [], "outside_band": {}, "m1_adx_narrow": False,
           "m1_adx": None, "note": ""}

    # --- 1+2) Band khung lớn đang bị chạm ---
    levels = collect_resistance_levels(frames)
    touched = [l for l in levels if l["touched"]]
    out["touched_levels"] = touched

    # --- 3) Giá ngoài band ở M5/M15/H1 (ưu thế) ---
    outside_count = 0
    outside_dir_votes = {"MUA": 0, "BÁN": 0}
    for tf in ["5m", "15m", "60m"]:
        df = frames.get(tf)
        if df is None or df.empty:
            continue
        ob = detect_outside_band(df)
        if ob["outside"]:
            out["outside_band"][tf] = ob["side"]
            outside_count += 1
            # ngoài band trên -> kỳ vọng hồi XUỐNG (BÁN); ngoài band dưới -> MUA
            if ob["side"] == "above":
                outside_dir_votes["BÁN"] += 1
            else:
                outside_dir_votes["MUA"] += 1

    # --- 4) M1 ADX hẹp (sideway/hồi) ---
    if df_m1 is not None and not df_m1.empty and "ADX" in df_m1:
        adx_v = df_m1["ADX"].iloc[-1]
        if pd.notna(adx_v):
            out["m1_adx"] = round(float(adx_v), 1)
            out["m1_adx_narrow"] = float(adx_v) < adx_narrow

    # --- Tổng hợp độ mạnh + hướng ---
    strength = 0
    # Hướng từ band bị chạm: chạm kháng cự -> BÁN, chạm hỗ trợ -> MUA
    res_votes = {"MUA": 0, "BÁN": 0}
    for l in touched:
        strength += 1
        if l["type"] == "support":
            res_votes["MUA"] += 1
        else:
            res_votes["BÁN"] += 1

    # Cộng phiếu ngoài band (ưu thế -> +2 mỗi khung)
    for d, v in outside_dir_votes.items():
        res_votes[d] += v * 2
        strength += v * 2

    if out["m1_adx_narrow"]:
        strength += 1  # sideway M1 xác nhận sóng hồi

    # Quyết định hướng
    if res_votes["MUA"] > res_votes["BÁN"]:
        out["direction"] = "MUA"
    elif res_votes["BÁN"] > res_votes["MUA"]:
        out["direction"] = "BÁN"

    out["strength"] = strength
    # "Phát hiện" khi: chạm >=1 band lớn HOẶC giá ngoài band, VÀ có hướng rõ
    out["detected"] = (len(touched) >= 1 or outside_count >= 1) and out["direction"] is not None

    # Ghi chú
    parts = []
    if touched:
        parts.append("chạm " + ", ".join(f"{l['name']}" for l in touched))
    if out["outside_band"]:
        parts.append("ngoài band " + "/".join(out["outside_band"].keys()) + " (ưu thế)")
    if out["m1_adx_narrow"]:
        parts.append(f"M1 ADX hẹp {out['m1_adx']} (sideway/hồi)")
    if out["detected"]:
        out["note"] = (f"🌀 SÓNG HỒI {out['direction']} (độ mạnh {out['strength']}): "
                       + "; ".join(parts) + ".")
    elif parts:
        out["note"] = "Theo dõi: " + "; ".join(parts) + "."
    else:
        out["note"] = "Chưa có dấu hiệu sóng hồi rõ ràng."
    return out


# ============================================================================
# QUY LUẬT 3 TẦNG (theo phân tích thực chiến XAU/USD của user)
# 3-tier reversal: khung lớn = kháng cự động | khung trung = ngoài band | M1 = then chốt
# ============================================================================
def big_tf_resistance(df: pd.DataFrame, touch_pct: float = 0.0015) -> dict:
    """
    TẦNG 1 — Khung lớn (H4, H1): kháng cự/hỗ trợ ĐỘNG.
    Kiểm tra giá có chạm BB band ngoài / EMA200 / EMA1200 không.
    Trả về {'hit': bool, 'levels': [tên các mốc bị chạm], 'type': 'support'/'resistance'}
    """
    out = {"hit": False, "levels": [], "type": None}
    if df is None or df.empty:
        return out
    last = df.iloc[-1]
    price = float(last["Close"])
    # Các mốc kháng cự động trên khung lớn
    candidates = [
        ("BB trên", last.get("BB_UP"), "resistance"),
        ("BB dưới", last.get("BB_LOW"), "support"),
        ("EMA200", last.get("EMA_200"), None),
        ("EMA1200", last.get("EMA_1200"), None),
    ]
    for name, lvl, fixed_type in candidates:
        if lvl is None or pd.isna(lvl):
            continue
        lvl = float(lvl)
        if abs(price - lvl) / price <= touch_pct:
            out["hit"] = True
            out["levels"].append(name)
            # BB trên/dưới có type cố định; EMA tùy vị trí giá
            t = fixed_type or ("support" if price >= lvl else "resistance")
            out["type"] = t
    return out


def mid_tf_outside_band(df: pd.DataFrame) -> dict:
    """
    TẦNG 2 — Khung trung (M15, M30, M5): QUÁ ĐÀ.
    Giá đóng RA NGOÀI Bollinger band -> căng, dễ hồi.
    Trả về {'outside': bool, 'side': 'above'/'below', 'pct_b': float}
    """
    return detect_outside_band(df)  # tái dùng hàm đã có


def m1_trigger(df_m1: pd.DataFrame) -> dict:
    """
    TẦNG 3 — M1: THEN CHỐT kích hoạt vào lệnh.
    %B đã cắt band >=1 lần + test lại liên tục không phá + Stoch quá mua/bán.
    Trả về {'ready': bool, 'direction': 'MUA'/'BÁN'/None, 'reasons': [...]}
    """
    out = {"ready": False, "direction": None, "reasons": []}
    if df_m1 is None or len(df_m1) < 20:
        return out

    band = detect_band_hold(df_m1)
    last = df_m1.iloc[-1]
    sk = last.get("STOCH_K")

    if band["lower_hold"]:
        out["direction"] = "MUA"
        out["reasons"].append("%B test band dưới liên tục, then chốt đảo lên")
        if pd.notna(sk) and sk < 25:
            out["reasons"].append(f"Stoch quá bán ({sk:.0f})")
        out["ready"] = True
    elif band["upper_hold"]:
        out["direction"] = "BÁN"
        out["reasons"].append("%B test band trên liên tục, then chốt đảo xuống")
        if pd.notna(sk) and sk > 75:
            out["reasons"].append(f"Stoch quá mua ({sk:.0f})")
        out["ready"] = True
    return out


def three_tier_entry(frames: dict, df_m1: pd.DataFrame) -> dict:
    """
    QUY LUẬT 3 TẦNG TỔNG HỢP — phát hiện điểm đảo để scalp.

    frames: {'4h':df, '60m':df, '30m':df, '15m':df, '5m':df} (đã có chỉ báo)
    df_m1: dữ liệu M1 đã có chỉ báo.

    Logic:
      Tầng 1 (H4/H1): giá chạm kháng cự động (BB ngoài / EMA200 / EMA1200)
      Tầng 2 (M15/M30/M5): giá ra ngoài band (quá đà)
      Tầng 3 (M1): then chốt %B + Stoch -> kích hoạt

    Trả về dict đầy đủ trạng thái + mức sẵn sàng vào lệnh.
    """
    out = {
        "tier1": {"ok": False, "detail": []},
        "tier2": {"ok": False, "detail": []},
        "tier3": {"ok": False, "direction": None, "detail": []},
        "score": 0, "direction": None, "entry_ready": False, "note": "",
    }

    # --- TẦNG 1: khung lớn H4 + H1 ---
    big_dir_votes = {"MUA": 0, "BÁN": 0}
    for tf, label in [("4h", "H4"), ("60m", "H1")]:
        df = frames.get(tf)
        r = big_tf_resistance(df) if df is not None else {"hit": False}
        if r.get("hit"):
            out["tier1"]["ok"] = True
            out["tier1"]["detail"].append(f"{label}: chạm {', '.join(r['levels'])}")
            # chạm kháng cự -> kỳ vọng BÁN; chạm hỗ trợ -> MUA
            if r["type"] == "resistance":
                big_dir_votes["BÁN"] += 1
            elif r["type"] == "support":
                big_dir_votes["MUA"] += 1

    # --- TẦNG 2: khung trung M15/M30/M5 ngoài band ---
    mid_dir_votes = {"MUA": 0, "BÁN": 0}
    for tf, label in [("15m", "M15"), ("30m", "M30"), ("5m", "M5")]:
        df = frames.get(tf)
        if df is None:
            continue
        ob = mid_tf_outside_band(df)
        if ob["outside"]:
            out["tier2"]["ok"] = True
            side_txt = "trên" if ob["side"] == "above" else "dưới"
            out["tier2"]["detail"].append(f"{label}: ngoài band {side_txt}")
            if ob["side"] == "above":
                mid_dir_votes["BÁN"] += 1   # ngoài band trên -> hồi xuống
            else:
                mid_dir_votes["MUA"] += 1

    # --- TẦNG 3: M1 then chốt ---
    trig = m1_trigger(df_m1)
    out["tier3"]["ok"] = trig["ready"]
    out["tier3"]["direction"] = trig["direction"]
    out["tier3"]["detail"] = trig["reasons"]

    # --- Tổng hợp hướng + điểm ---
    votes = {"MUA": big_dir_votes["MUA"] + mid_dir_votes["MUA"],
             "BÁN": big_dir_votes["BÁN"] + mid_dir_votes["BÁN"]}
    if trig["direction"]:
        votes[trig["direction"]] += 2  # M1 then chốt có trọng số cao

    if votes["MUA"] > votes["BÁN"]:
        out["direction"] = "MUA"
    elif votes["BÁN"] > votes["MUA"]:
        out["direction"] = "BÁN"

    # Điểm = số tầng thỏa mãn (0-3)
    out["score"] = sum([out["tier1"]["ok"], out["tier2"]["ok"], out["tier3"]["ok"]])

    # SẴN SÀNG VÀO khi: đủ cả 3 tầng cùng hướng (lý tưởng) hoặc tầng 3 + ít nhất 1 tầng lớn
    tier3_dir = out["tier3"]["direction"]
    aligned = (tier3_dir is not None and tier3_dir == out["direction"])
    out["entry_ready"] = (out["score"] >= 3 and aligned) or \
                         (out["tier3"]["ok"] and out["tier1"]["ok"] and aligned)

    # Ghi chú
    if out["entry_ready"]:
        out["note"] = (f"✅ ĐIỂM VÀO {out['direction']} — đủ {out['score']}/3 tầng hội tụ.")
    elif out["score"] >= 2:
        miss = []
        if not out["tier1"]["ok"]: miss.append("khung lớn chưa chạm kháng cự")
        if not out["tier2"]["ok"]: miss.append("khung trung chưa ngoài band")
        if not out["tier3"]["ok"]: miss.append("M1 chưa then chốt")
        out["note"] = f"⏳ Gần đủ ({out['score']}/3) — còn thiếu: {', '.join(miss)}."
    else:
        out["note"] = f"Chưa hội tụ (mới {out['score']}/3 tầng)."
    return out


# ============================================================================
# TÍN HIỆU LEO THANG KHUNG (escalation) — nguyên tắc thực chiến của user
# %B vượt band >=2 lần trong vùng gần nhau -> mô hình đỉnh/đáy sideway.
# Báo khung nhỏ + xác nhận khung lớn hơn 1 bậc = AN TOÀN.
# ============================================================================

# Thứ tự khung từ nhỏ -> lớn, để biết "khung lớn hơn 1 bậc"
TF_LADDER = ["1m", "5m", "15m", "30m", "60m", "4h", "1d"]


def detect_band_touches(df: pd.DataFrame, window: int = 12,
                        min_touches: int = 2) -> dict:
    """
    Phát hiện mô hình %B VƯỢT BAND >=min_touches lần trong 'window' nến gần nhất
    (các lần vượt nằm gần nhau = đỉnh/đáy sideway).

    Trả về:
      {
        'pattern': bool,           # có mô hình
        'side': 'top'/'bottom'/None,  # đỉnh (band trên) hay đáy (band dưới)
        'touches': int,            # số lần vượt
        'direction': 'BÁN'/'MUA'/None, # đỉnh->BÁN, đáy->MUA
        'touch_idx': [vị trí các lần vượt]  # để vẽ mũi tên
      }
    """
    out = {"pattern": False, "side": None, "touches": 0,
           "direction": None, "touch_idx": []}
    if df is None or len(df) < window or "PCT_B" not in df:
        return out

    recent = df["PCT_B"].tail(window)
    vals = recent.values
    idxs = recent.index

    # Đếm lần vượt band trên (%B >= 1) và band dưới (%B <= 0)
    top_idx = [idxs[i] for i, v in enumerate(vals) if pd.notna(v) and v >= 0.98]
    bot_idx = [idxs[i] for i, v in enumerate(vals) if pd.notna(v) and v <= 0.02]

    if len(top_idx) >= min_touches:
        out.update(pattern=True, side="top", touches=len(top_idx),
                   direction="BÁN", touch_idx=top_idx)
    elif len(bot_idx) >= min_touches:
        out.update(pattern=True, side="bottom", touches=len(bot_idx),
                   direction="MUA", touch_idx=bot_idx)
    return out


def escalation_scan(frames: dict) -> dict:
    """
    QUÉT MÔ HÌNH LEO THANG trên tất cả khung trong frames.

    frames: {tf: df_đã_có_chỉ_báo}.
    Tìm các khung có mô hình %B vượt band >=2 lần. Với mỗi khung có mô hình,
    kiểm tra khung LỚN HƠN 1 BẬC có cùng hướng không -> nếu có = AN TOÀN.

    Trả về:
      {
        'signals': [
          {tf, direction, touches, confirmed_by, safe(bool), touch_idx}
        ],
        'best': tín hiệu an toàn nhất (nếu có),
      }
    """
    out = {"signals": [], "best": None}
    # Phát hiện mô hình từng khung
    patterns = {}
    for tf, df in frames.items():
        p = detect_band_touches(df)
        if p["pattern"]:
            patterns[tf] = p

    for tf, p in patterns.items():
        # Tìm khung lớn hơn 1 bậc
        confirmed_by = None
        safe = False
        if tf in TF_LADDER:
            pos = TF_LADDER.index(tf)
            # duyệt các khung lớn hơn để tìm xác nhận cùng hướng
            for bigger in TF_LADDER[pos + 1:]:
                if bigger in patterns and patterns[bigger]["direction"] == p["direction"]:
                    confirmed_by = bigger
                    safe = True
                    break
        sig = {
            "tf": tf, "direction": p["direction"], "touches": p["touches"],
            "confirmed_by": confirmed_by, "safe": safe, "touch_idx": p["touch_idx"],
        }
        out["signals"].append(sig)

    # Tín hiệu tốt nhất: ưu tiên safe, rồi nhiều lần chạm
    safe_sigs = [s for s in out["signals"] if s["safe"]]
    if safe_sigs:
        out["best"] = max(safe_sigs, key=lambda s: s["touches"])
    elif out["signals"]:
        out["best"] = max(out["signals"], key=lambda s: s["touches"])
    return out


def target_by_tf(tf: str, entry_price: float, direction: str,
                 df: pd.DataFrame) -> dict:
    """
    Tính target tối thiểu 2 nến theo khung. Dùng ATR đơn giản (biên độ nến TB)
    để ước lượng quãng đường ~2 nến.
    """
    out = {"entry": round(entry_price, 2), "target": None, "sl": None}
    if df is None or len(df) < 14:
        return out
    # Biên độ trung bình 14 nến gần nhất (range High-Low)
    rng = (df["High"] - df["Low"]).tail(14).mean()
    if pd.isna(rng) or rng <= 0:
        return out
    move = rng * 2  # tối thiểu 2 nến
    if direction == "MUA":
        out["target"] = round(entry_price + move, 2)
        out["sl"] = round(entry_price - rng, 2)
    else:
        out["target"] = round(entry_price - move, 2)
        out["sl"] = round(entry_price + rng, 2)
    out["move_pips"] = round(move, 2)
    return out


# ============================================================================
# ĐIỂM TÍN HIỆU CHO MŨI TÊN (%BB cốt lõi + Stoch + ADX/DI)
# Trả về các vị trí (index) + hướng để vẽ mũi tên nhỏ trên chart.
# ============================================================================
def signal_points(df: pd.DataFrame, window: int = 30) -> dict:
    """
    Quét 'window' nến gần nhất, tìm điểm phát tín hiệu của 3 chỉ báo:
      - bb    : %B vượt band trên (BÁN) / dưới (MUA)  [CỐT LÕI]
      - stoch : %K cắt %D ở vùng quá mua (BÁN) / quá bán (MUA)
      - adx   : DI+ cắt lên DI- (MUA) / DI- cắt lên DI+ (BÁN)

    Trả về dict mỗi loại: {'buy': [idx...], 'sell': [idx...]}
    """
    out = {"bb": {"buy": [], "sell": []},
           "stoch": {"buy": [], "sell": []},
           "adx": {"buy": [], "sell": []}}
    if df is None or len(df) < 3:
        return out

    sub = df.tail(window)
    idxs = sub.index

    for i in range(1, len(sub)):
        ts = idxs[i]
        prev = sub.iloc[i - 1]
        cur = sub.iloc[i]

        # --- %BB (cốt lõi) ---
        pb = cur.get("PCT_B")
        if pd.notna(pb):
            if pb >= 0.98:
                out["bb"]["sell"].append(ts)   # vượt band trên -> đảo xuống
            elif pb <= 0.02:
                out["bb"]["buy"].append(ts)     # vượt band dưới -> đảo lên

        # --- Stochastic: %K cắt %D ---
        k0, d0 = prev.get("STOCH_K"), prev.get("STOCH_D")
        k1, d1 = cur.get("STOCH_K"), cur.get("STOCH_D")
        if all(pd.notna(x) for x in [k0, d0, k1, d1]):
            # cắt xuống ở vùng quá mua -> BÁN
            if k0 >= d0 and k1 < d1 and k1 > 70:
                out["stoch"]["sell"].append(ts)
            # cắt lên ở vùng quá bán -> MUA
            elif k0 <= d0 and k1 > d1 and k1 < 30:
                out["stoch"]["buy"].append(ts)

        # --- ADX/DI: DI cross ---
        dip0, dim0 = prev.get("DI_PLUS"), prev.get("DI_MINUS")
        dip1, dim1 = cur.get("DI_PLUS"), cur.get("DI_MINUS")
        if all(pd.notna(x) for x in [dip0, dim0, dip1, dim1]):
            if dip0 <= dim0 and dip1 > dim1:   # DI+ cắt lên -> MUA
                out["adx"]["buy"].append(ts)
            elif dim0 <= dip0 and dim1 > dip1:  # DI- cắt lên -> BÁN
                out["adx"]["sell"].append(ts)

    return out
