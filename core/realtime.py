"""
core/realtime.py — Nguồn giá REAL-TIME (giảm độ trễ so với yfinance ~15 phút).

Hỗ trợ 3 nguồn, cho chọn linh hoạt trong app:
  - "yfinance"   : mặc định, miễn phí, KHÔNG cần key, nhưng trễ ~15 phút
  - "twelvedata" : real-time forex + crypto + XAU, free 800 lệnh/ngày (cần API key)
  - "goldapi"    : real-time XAU/kim loại chính xác (cần API key), chỉ cho vàng

Triết lý: lấy GIÁ TƯƠI (last price/quote) từ nguồn real-time để hiển thị và
đối chiếu, còn dữ liệu nến lịch sử (để vẽ + tính chỉ báo) vẫn từ yfinance.
Nếu nguồn real-time lỗi/hết quota -> tự fallback yfinance.

LƯU Ý: API key do người dùng tự đăng ký (miễn phí), nhập trong app hoặc
đặt trong st.secrets. KHÔNG hardcode key vào source.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Map nhãn cặp -> ký hiệu của từng nhà cung cấp
TWELVEDATA_SYMBOLS = {
    "XAU/USD (Vàng)": "XAU/USD",
    "BTC/USD (Bitcoin)": "BTC/USD",
    "GBP/USD": "GBP/USD",
    "EUR/USD": "EUR/USD",
    "USD/JPY": "USD/JPY",
}

REQUEST_TIMEOUT = 8  # giây


def get_twelvedata_price(symbol_label: str, api_key: str) -> Optional[dict]:
    """
    Lấy giá real-time từ Twelve Data (endpoint /price + /quote).
    Trả về {'price', 'source', 'symbol'} hoặc None nếu lỗi.
    """
    sym = TWELVEDATA_SYMBOLS.get(symbol_label)
    if not sym or not api_key:
        return None
    try:
        url = "https://api.twelvedata.com/price"
        r = requests.get(url, params={"symbol": sym, "apikey": api_key},
                         timeout=REQUEST_TIMEOUT)
        data = r.json()
        # Twelve Data trả {"price": "4665.82"} hoặc {"code":..,"message":..}
        if "price" in data:
            return {"price": float(data["price"]), "source": "Twelve Data",
                    "symbol": sym}
        logger.warning("Twelve Data lỗi: %s", data.get("message", data))
        return None
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("Twelve Data request lỗi: %s", e)
        return None


def get_goldapi_price(symbol_label: str, api_key: str) -> Optional[dict]:
    """
    Lấy giá real-time XAU từ GoldAPI.io. Chỉ áp dụng cho vàng.
    Trả về {'price','bid','ask','source','symbol'} hoặc None.
    """
    if "XAU" not in symbol_label or not api_key:
        return None
    try:
        # GoldAPI: GET https://www.goldapi.io/api/XAU/USD , header x-access-token
        url = "https://www.goldapi.io/api/XAU/USD"
        r = requests.get(url, headers={"x-access-token": api_key},
                         timeout=REQUEST_TIMEOUT)
        data = r.json()
        if "price" in data:
            return {
                "price": float(data["price"]),
                "bid": data.get("bid"), "ask": data.get("ask"),
                "source": f"GoldAPI ({data.get('exchange', 'FOREX')})",
                "symbol": "XAU/USD",
            }
        logger.warning("GoldAPI lỗi: %s", data)
        return None
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("GoldAPI request lỗi: %s", e)
        return None


def get_realtime_price(
    symbol_label: str, source: str,
    twelvedata_key: str = "", goldapi_key: str = "",
) -> Optional[dict]:
    """
    Hàm tổng: lấy giá tươi theo nguồn người dùng chọn.
    source: 'twelvedata' / 'goldapi' / 'yfinance'(=None real-time).
    Trả về dict giá hoặc None (None -> app dùng giá từ nến yfinance).
    """
    if source == "twelvedata":
        return get_twelvedata_price(symbol_label, twelvedata_key)
    if source == "goldapi":
        return get_goldapi_price(symbol_label, goldapi_key)
    return None  # yfinance: không có "giá tươi" riêng, dùng close nến cuối
