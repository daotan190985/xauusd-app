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
    Lấy giá real-time từ Twelve Data (endpoint /price).
    Trả về {'price','source','symbol'} hoặc {'error': '...'}.
    """
    sym = TWELVEDATA_SYMBOLS.get(symbol_label)
    if not sym:
        return {"error": f"Twelve Data chưa map cặp {symbol_label}."}
    if not api_key or not api_key.strip():
        return {"error": "Chưa nhập Twelve Data key."}
    try:
        url = "https://api.twelvedata.com/price"
        r = requests.get(url, params={"symbol": sym, "apikey": api_key.strip()},
                         timeout=REQUEST_TIMEOUT)
        data = r.json()
        if "price" in data:
            return {"price": float(data["price"]), "source": "Twelve Data",
                    "symbol": sym}
        # Twelve Data báo lỗi qua field code/message
        msg = data.get("message", str(data))
        if "run out of API credits" in msg or data.get("code") == 429:
            return {"error": "Hết quota Twelve Data hôm nay (800 lệnh/ngày)."}
        if data.get("code") == 401 or "apikey" in msg.lower():
            return {"error": "Key Twelve Data sai. Kiểm tra lại trên twelvedata.com."}
        if "not available" in msg.lower() or "grow" in msg.lower():
            return {"error": f"Gói free không hỗ trợ cặp {sym}. Thử nguồn GoldAPI cho vàng."}
        return {"error": f"Twelve Data: {msg[:120]}"}
    except requests.Timeout:
        return {"error": "Twelve Data quá thời gian chờ (mạng chậm/chặn)."}
    except requests.RequestException as e:
        return {"error": f"Lỗi kết nối Twelve Data: {str(e)[:100]}"}
    except (ValueError, KeyError) as e:
        return {"error": f"Lỗi đọc dữ liệu Twelve Data: {str(e)[:100]}"}


def get_goldapi_price(symbol_label: str, api_key: str) -> Optional[dict]:
    """
    Lấy giá real-time XAU từ GoldAPI.io. Chỉ áp dụng cho vàng.
    Trả về {'price','bid','ask','source','symbol'} hoặc
            {'error': 'mô tả lỗi'} để app hiển thị nguyên nhân.
    """
    if "XAU" not in symbol_label:
        return {"error": "GoldAPI chỉ hỗ trợ vàng (XAU). Chọn nguồn khác cho cặp này."}
    if not api_key or not api_key.strip():
        return {"error": "Chưa nhập GoldAPI key."}
    try:
        url = "https://www.goldapi.io/api/XAU/USD"
        r = requests.get(url, headers={"x-access-token": api_key.strip()},
                         timeout=REQUEST_TIMEOUT)
        # Bắt lỗi HTTP rõ ràng
        if r.status_code == 401 or r.status_code == 403:
            return {"error": "Key sai hoặc chưa kích hoạt (HTTP "
                             f"{r.status_code}). Kiểm tra lại key trên goldapi.io."}
        if r.status_code == 429:
            return {"error": "Hết quota GoldAPI (HTTP 429). Chờ reset hoặc nâng gói."}
        data = r.json()
        if "price" in data:
            return {
                "price": float(data["price"]),
                "bid": data.get("bid"), "ask": data.get("ask"),
                "source": f"GoldAPI ({data.get('exchange', 'FOREX')})",
                "symbol": "XAU/USD",
            }
        return {"error": f"GoldAPI trả về bất thường: {str(data)[:120]}"}
    except requests.Timeout:
        return {"error": "GoldAPI quá thời gian chờ (mạng chậm/chặn)."}
    except requests.RequestException as e:
        return {"error": f"Lỗi kết nối GoldAPI: {str(e)[:100]}"}
    except (ValueError, KeyError) as e:
        return {"error": f"Lỗi đọc dữ liệu GoldAPI: {str(e)[:100]}"}


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


def apply_live_price(df, live_price: float):
    """
    VÁ GIÁ TƯƠI vào cây nến cuối cùng của DataFrame, để phân tích phản ánh
    đúng giá hiện tại thay vì giá nến cũ (yfinance trễ ~15 phút).

    - Close nến cuối = live_price
    - High = max(High cũ, live_price); Low = min(Low cũ, live_price)
    Trả về DataFrame mới (copy). Chỉ báo cần được tính LẠI sau khi gọi hàm này.
    """
    if df is None or df.empty or live_price is None or live_price <= 0:
        return df
    out = df.copy()
    i = out.index[-1]
    out.loc[i, "Close"] = live_price
    if live_price > out.loc[i, "High"]:
        out.loc[i, "High"] = live_price
    if live_price < out.loc[i, "Low"]:
        out.loc[i, "Low"] = live_price
    return out
