#!/usr/bin/env python3
"""農曆過年旺季區間計算（曆法自動帶出，取代每年手動更新）

來源決策：docs/tech-decisions/農曆過年旺季-2026-08-15.md（D1/D2）
  - 農曆為陰陽合曆，正月初一 = 天文朔日，可純本地計算（lunardate）
  - 區間定義：from = 除夕前最後一個週六，to = 初一後第一個週六（含邊界，以去程日判斷）
  - 2027 驗算：除夕 2027-02-05(五) → from=2027-01-30；初一 2027-02-06(六) → to=2027-02-06
    （與 web/aggregate.js 原 CONFIG.PEAKS 手動設定一致）

對照來源（測試用）：香港天文台年度文字檔
  https://www.hko.gov.hk/tc/gts/time/calendar/text/files/T{year}c.txt
"""
from datetime import date, timedelta

try:
    from lunardate import LunarDate
except ImportError:  # pragma: no cover - CI 裝完依賴後不會走到
    raise SystemExit("缺少 lunardate：請先 pip install -r requirements.txt")

# 週六 = 5（date.weekday(): Mon=0 ... Sun=6）
SATURDAY = 5

# 輸出 label（與前端 CONFIG.PEAKS 語意一致）
LABEL_CNY = "農曆過年"


def _lunar_new_year(year: int) -> date:
    """回傳陽曆 date：year 年正月初一。"""
    # lunardate 新舊版 API 相容（新版 to_solar_date / 舊版 toSolarDate）
    d = LunarDate(year, 1, 1)
    fn = getattr(d, "to_solar_date", None) or d.toSolarDate
    return fn()


def peak_range(year: int) -> tuple[date, date]:
    """回傳 (from, to)：year 年過年旺季區間（含邊界）。

    from = 除夕前最後一個週六（該週出發的旅程涵蓋除夕/初一）
    to   = 初一後第一個週六（含初一當日若為週六）
    """
    ny = _lunar_new_year(year)
    eve = ny - timedelta(days=1)          # 除夕
    f = eve
    while f.weekday() != SATURDAY:
        f -= timedelta(days=1)
    t = ny
    while t.weekday() != SATURDAY:
        t += timedelta(days=1)
    return f, t


def _as_date(v: date | str) -> date:
    """相容 'YYYY-MM-DD' 字串與 date 物件（build_api 傳入的是字串）。"""
    if isinstance(v, date):
        return v
    return date.fromisoformat(v)


def peaks_in_range(min_date: date | str, max_date: date | str) -> list[dict]:
    """回傳視窗 [min_date, max_date] 內的過年旺季（與視窗有交集即納入）。

    @param min_date/max_date: 資料視窗（通常為 records 的 outbound_date 最小/最大值，'YYYY-MM-DD'）
    @returns [{label, from, to}] — from/to 為 'YYYY-MM-DD'
    """
    mn, mx = _as_date(min_date), _as_date(max_date)
    if mn > mx:
        return []
    peaks: list[dict] = []
    # 跨年視窗保守多掃前後各 1 年
    for y in range(mn.year - 1, mx.year + 2):
        f, t = peak_range(y)
        if t >= mn and f <= mx:
            peaks.append({
                "label": LABEL_CNY,
                "from": f.isoformat(),
                "to": t.isoformat(),
            })
    peaks.sort(key=lambda p: p["from"])
    return peaks
