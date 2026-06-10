"""
Lịch kinh tế (economic calendar) cho XAU/USD và forex.

Nguồn: ForexFactory phát hành công khai file JSON tuần hiện tại (không cần key):
  https://nfs.faireconomy.media/ff_calendar_thisweek.json
Mỗi sự kiện có: title, country, date (UTC ISO), impact (High/Medium/Low/Holiday),
forecast, previous.

Vàng (XAU) nhạy nhất với tin USD (Fed, NFP, CPI, lãi suất). Module ưu tiên lọc
USD + impact cao để nhắc user TRÁNH vào lệnh quanh giờ tin mạnh.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TIMEOUT = 12

# Tiền tệ ảnh hưởng mạnh tới vàng (USD là chính)
GOLD_SENSITIVE = {"USD"}


def fetch_calendar() -> Optional[list]:
    """
    Tải lịch kinh tế tuần hiện tại từ ForexFactory (JSON công khai).
    Trả về list event dicts, hoặc None nếu lỗi.
    """
    try:
        r = requests.get(FF_URL, timeout=TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            logger.warning("Calendar HTTP %s", r.status_code)
            return None
        data = r.json()
        if not isinstance(data, list):
            return None
        return data
    except (requests.RequestException, ValueError) as e:
        logger.warning("Calendar lỗi: %s", e)
        return None


def _parse_dt(s: str) -> Optional[_dt.datetime]:
    """Parse chuỗi thời gian ISO của ForexFactory (có timezone offset)."""
    if not s:
        return None
    try:
        # ví dụ "2026-06-10T08:30:00-04:00"
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def get_high_impact_events(events: list, currencies=None,
                           hours_ahead: int = 24) -> dict:
    """
    Lọc sự kiện ảnh hưởng CAO sắp diễn ra trong 'hours_ahead' giờ tới.

    Trả về:
      {
        'today': [event...],         # tin mạnh hôm nay
        'upcoming_soon': [event...], # tin mạnh trong vài giờ tới (cảnh báo TRÁNH)
        'week_high': [event...],     # tất cả tin mạnh trong tuần
      }
    Mỗi event chuẩn hoá: {time_local, title, country, impact, forecast, previous, dt_utc}
    """
    out = {"today": [], "upcoming_soon": [], "week_high": []}
    if not events:
        return out
    if currencies is None:
        currencies = GOLD_SENSITIVE

    now = _dt.datetime.now(_dt.timezone.utc)
    today = now.date()

    for ev in events:
        impact = (ev.get("impact") or "").lower()
        country = ev.get("country") or ev.get("currency") or ""
        if impact != "high":
            continue
        if currencies and country not in currencies:
            continue
        dt = _parse_dt(ev.get("date", ""))
        if dt is None:
            continue
        dt_utc = dt.astimezone(_dt.timezone.utc)
        norm = {
            "title": ev.get("title", ""),
            "country": country,
            "impact": "High",
            "forecast": ev.get("forecast", ""),
            "previous": ev.get("previous", ""),
            "dt_utc": dt_utc,
        }
        out["week_high"].append(norm)
        if dt_utc.date() == today:
            out["today"].append(norm)
        # Sắp diễn ra trong hours_ahead giờ tới (và chưa qua)
        delta = (dt_utc - now).total_seconds() / 3600.0
        if 0 <= delta <= hours_ahead:
            out["upcoming_soon"].append(norm)

    for k in out:
        out[k].sort(key=lambda e: e["dt_utc"])
    return out


def format_event(ev: dict, tz_offset_hours: int = 7) -> str:
    """Định dạng 1 sự kiện sang giờ địa phương (mặc định VN UTC+7)."""
    local = ev["dt_utc"] + _dt.timedelta(hours=tz_offset_hours)
    t = local.strftime("%d/%m %H:%M")
    fc = f" · DK: {ev['forecast']}" if ev.get("forecast") else ""
    pv = f" · Trước: {ev['previous']}" if ev.get("previous") else ""
    return f"🔴 {t} · {ev['country']} · {ev['title']}{fc}{pv}"
