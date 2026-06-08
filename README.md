# 🥇 XAU/USD Professional Trading Journal & Signal Analyzer

Ứng dụng Streamlit phân tích tín hiệu đa khung thời gian + nhật ký giao dịch
chuyên nghiệp cho vàng (XAU/USD), dữ liệu miễn phí từ yfinance.

## Tính năng

- **Scalp theo EMA1200 khung lớn**: chọn linh hoạt khung lớn (1H/4H/1D), theo dõi EMA1200 làm
  điểm đảo chiều. Báo **2 mức**: "⏳ GẦN" (giá tiến sát, chuẩn bị) và "✅ XÁC NHẬN" (chạm EMA1200
  + M1 hội tụ → vào lệnh scalp ngay).
- **Vùng đảo chiều & tín hiệu M1**: phát hiện vùng hỗ trợ/kháng cự mạnh từ **Fibonacci 161.8%
  extension** + **EMA 200/1200 động** trên khung lớn. Khi giá chạm vùng → đánh dấu trên biểu đồ
  + kiểm tra **hội tụ M1** (%BB then chốt + Stochastic + MACD tách histogram) → báo "CÂN NHẮC MUA/BÁN".
- **Nguồn dữ liệu real-time (giảm trễ)**: chọn linh hoạt trong app giữa yfinance (free, trễ ~15 phút),
  **Twelve Data** (real-time forex+crypto+XAU, free 800 lệnh/ngày) và **GoldAPI** (real-time XAU chính xác).
  Nhập API key trong sidebar hoặc đặt trong `st.secrets`. Tự fallback yfinance khi thiếu key/hết quota.
- **Tùy chỉnh chỉ báo giống TradingView**: chỉnh tham số (EMA length, BB length/std, MACD fast/slow/signal,
  ADX/RSI period, Stochastic, Keltner length/mult) và **đổi màu từng đường** bằng color picker,
  biểu đồ cập nhật ngay.
- **Phân tích tín hiệu đa khung**: **MACD (24,52,14) làm xu hướng chính**, EMA 100/200/1200,
  Bollinger %B "then chốt", **Keltner Channel (42,1)**, ADX + DI (logic riêng sideway <25),
  Stochastic(42,5,3) + RSI(14), tổng hợp confluence → MUA MẠNH / MUA / TRUNG LẬP / BÁN / BÁN MẠNH.
- **Multi-timeframe view**: xem 1 / 2 / 3 khung cùng lúc, **mỗi khung có selectbox độc lập**
  (1m → 1wk, gồm cả 4h), đổi khung nào chỉ vẽ lại khung đó.
- **Công cụ vẽ**: trendline, horizontal line, vùng chữ nhật, và **Fibonacci Retracement tự động**
  (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0).
- **Auto-refresh 15 phút** (bật/tắt) + nút làm mới thủ công.
- **Biểu đồ Plotly động** với cấu hình xem **gần giống OANDA/TradingView**: kéo để pan,
  lăn chuột/pinch để zoom, nhấp đúp reset, chọn scale Linear/Log/Phần trăm, **Auto/Lock scale**,
  giá nằm bên phải, nền tối sạch, MACD histogram nổi bật trên/dưới 0.
- **Bật/tắt từng chỉ báo** (EMA 100/200/1200, Bollinger, %B, ADX+DI, MACD, Stoch+RSI) —
  mặc định gọn (EMA 100/200 + BB + MACD) để dễ xem, bật thêm khi cần. Biểu đồ cập nhật ngay.
- **Chia màn hình đa khung**: xem 1 / 2 / 3 khung cùng lúc cạnh nhau (vd M5 + M15 + H1).
- **Truy cập từ điện thoại**: giao diện responsive + hướng dẫn deploy lên cloud (xem cuối file).
- **Nhật ký giao dịch**: log lệnh, chọn phương pháp (gồm MACD), upload ảnh, ghi chú, độ chắc chắn.
- **Biểu đồ chính xác tại bất kỳ thời điểm nào** (historical-accurate, không leak tương lai).
- **Thống kê tuần/tháng**: winrate, winrate theo phương pháp, top phương pháp hiệu quả.
- **Xuất PDF** báo cáo có bảng tổng kết, chi tiết lệnh và ảnh biểu đồ.
- **AI Prompt generator** + cấu hình Telegram (tùy chọn).

## Chạy nhanh nhất (Windows) — KHÔNG cần gõ lệnh

1. Giải nén `xauusd_app.zip`.
2. Vào thư mục `xauusd_app` (nơi có file `app.py` và `run.bat`).
3. **Nhấp đúp vào `run.bat`**. Xong — app tự cài thư viện rồi mở trình duyệt.

> ⚠️ QUAN TRỌNG: `run.bat` phải nằm CÙNG thư mục với `app.py`. Nếu sau khi giải nén
> bạn thấy thư mục lồng nhau (vd `xauusd_app/xauusd_app/`), hãy đi vào thư mục
> trong cùng — nơi chứa `app.py` — rồi mới nhấp đúp `run.bat`.

Máy cần cài sẵn Python (python.org, nhớ tick "Add Python to PATH").

## Cấu trúc

```
xauusd_app/
├── app.py                  # Giao diện 4 tab
├── core/
│   ├── analyzer.py         # Chỉ báo (gồm MACD 24,52,14) + logic tín hiệu
│   ├── data.py             # yfinance + cache + slice lịch sử
│   ├── charts.py           # Biểu đồ Plotly động (bật/tắt indicator, scale)
│   ├── journal.py          # Lưu lịch sử, ảnh, thống kê
│   └── pdf_export.py       # Xuất PDF
├── .streamlit/config.toml  # Theme tối + tối ưu mobile
├── Procfile                # Lệnh deploy Render/Railway
├── run.bat / run.sh        # Chạy nhanh 1 cú nhấp
├── data/                   # CSV + ảnh (tự tạo)
├── requirements.txt
└── README.md
```

## Cài đặt & chạy

```bash
pip install -r requirements.txt
streamlit run app.py
```

> `kaleido` cần thiết để lưu ảnh biểu đồ lịch sử ra PNG cho PDF. Nếu không cài,
> app vẫn chạy bình thường, chỉ bỏ qua bước lưu ảnh biểu đồ tự động.

## Lưu ý

- Ticker mặc định `XAUUSD=X`, tự động fallback sang `GC=F` khi lỗi.
- Dữ liệu intraday của yfinance bị giới hạn lịch sử (vd 1m chỉ ~7 ngày) — đây là
  giới hạn của nguồn miễn phí, không phải lỗi app.

---

## 📱 Truy cập từ điện thoại

### Cách 1 — Cùng mạng WiFi (nhanh, không cần đăng ký)

Chạy app trên máy tính bằng lệnh sau (cho phép máy khác trong mạng truy cập):

```bash
streamlit run app.py --server.address=0.0.0.0
```

Sau đó trên điện thoại (cùng WiFi với máy tính), mở trình duyệt và gõ:
`http://<IP_máy_tính>:8501` — ví dụ `http://192.168.1.10:8501`.

> Xem IP máy tính: Windows gõ `ipconfig` (dòng IPv4), Mac/Linux gõ `ifconfig` hoặc `ip a`.

### Cách 2 — Streamlit Community Cloud (MIỄN PHÍ, truy cập mọi nơi) ⭐ Khuyên dùng

1. Đẩy code lên một repo **GitHub** (public hoặc private).
2. Vào https://share.streamlit.io → đăng nhập bằng GitHub.
3. Bấm **New app** → chọn repo, nhánh, và file chính là `app.py`.
4. Bấm **Deploy**. Sau vài phút sẽ có link dạng `https://<tên>.streamlit.app`
   — mở được trên điện thoại ở bất kỳ đâu, kể cả khác WiFi.

> Lưu ý: dữ liệu nhật ký (`data/trade_history.csv`) trên Streamlit Cloud có thể
> bị reset khi app ngủ/khởi động lại. Nếu cần lưu lâu dài, nên kết nối Google Sheets
> hoặc một database (có thể mở rộng sau).

### Cách 3 — Render / Railway (chạy bền hơn, có thể gắn ổ đĩa lưu trữ)

Repo đã kèm sẵn `Procfile`:

```
web: streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
```

- **Render**: tạo **New Web Service** → trỏ tới repo → Render tự đọc `Procfile`.
  Build command: `pip install -r requirements.txt`.
- **Railway**: **New Project → Deploy from GitHub** → Railway tự nhận `Procfile`.

Cả hai đều cho link công khai mở được trên điện thoại mọi nơi.

### Cách 4 — ngrok (chia sẻ nhanh máy đang chạy ra Internet)

Khi app đang chạy local (port 8501), mở thêm một cửa sổ và chạy:

```bash
ngrok http 8501
```

ngrok cho một link công khai dạng `https://xxxx.ngrok-free.app` mở được trên điện thoại
mọi nơi. Phù hợp để test nhanh, không cần đẩy code lên GitHub. Cần cài ngrok và đăng ký
tài khoản miễn phí tại ngrok.com.

### File cấu hình kèm theo

- `.streamlit/config.toml` — theme tối, tối ưu mobile, `maxUploadSize`, headless.
- `Procfile` — lệnh chạy cho Render/Railway/Heroku.
- `requirements.txt` — danh sách thư viện để cloud tự cài.
