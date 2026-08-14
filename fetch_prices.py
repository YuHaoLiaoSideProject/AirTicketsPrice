#!/usr/bin/env python3
"""星宇航空機票價格爬蟲

每週抓取指定航線的來回票價（去週六、回下週日），輸出 JSON 歷史檔案。

用法：
    python fetch_prices.py            # 正常執行（今天起未來 NUM_WEEKS 週）
    python fetch_prices.py --date 2026-08-14   # 指定參考日期（測試用）

輸出：data/YYYYMMDD.json
"""
import argparse
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import config

# 航空公司名稱對照（API 回傳代碼 → 中文名）
AIRLINE_NAMES = {
    "JX": "星宇航空",
}

# 艙等優先順序（挑不到 eco 時的 fallback）
CABIN_ORDER = ["eco", "ecoPremium", "business", "first"]

# 星宇 API 錯誤代碼：01003 = 「所選日期或航班不適用」→ 該週無航班，視為正常空結果
# （如 FUK/CTS 等航線部分週沒有航班，不應計為失敗）
NO_FLIGHT_ERROR_CODES = {"01003"}


# ---------------------------------------------------------------- 日期產生
def gen_trip_dates(reference: date, num_weeks: int) -> list[tuple[str, str]]:
    """產生未來 num_weeks 組 (去程日期, 回程日期)。

    去程 = 參考日之後第一個週六；回程 = 去程 + RETURN_AFTER_DAYS 天（下週日）。
    """
    # 週六 = weekday() 5
    days_ahead = (5 - reference.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # 今天已是週六 → 從下週六開始
    first_sat = reference + timedelta(days=days_ahead)

    trips = []
    for i in range(num_weeks):
        dep = first_sat + timedelta(weeks=i)
        ret = dep + timedelta(days=config.RETURN_AFTER_DAYS)
        trips.append((dep.isoformat(), ret.isoformat()))
    return trips


# ---------------------------------------------------------------- API 呼叫
def build_payload(origin: str, dest: str, dep_date: str, ret_date: str) -> dict:
    return {
        "atHome": False,
        "trip": "round-trip",
        "itineraries": [
            {"departure": origin, "arrival": dest, "departureDate": dep_date},
            {"departure": dest, "arrival": origin, "departureDate": ret_date},
        ],
        "firstPickerInfo": {"index": 0, "date": dep_date},
        "travelers": {"adt": 1, "chd": 0, "inf": 0},
        "cabin": config.CABIN,
        "promotion": None,
    }


def _error_code(body: dict) -> str | None:
    """從 API 錯誤 body 抽出錯誤代碼（message 可能是 dict 或 list）。"""
    msg = body.get("message")
    if isinstance(msg, dict):
        return msg.get("code")
    if isinstance(msg, list):
        for m in msg:
            if isinstance(m, dict) and m.get("code"):
                return m["code"]
    return None


def query_flights(origin: str, dest: str, dep_date: str, ret_date: str) -> dict:
    """呼叫星宇 searchFlight API，帶重試。成功回傳 data dict，失敗拋例外。"""
    payload = build_payload(origin, dest, dep_date, ret_date)
    last_err = None
    for attempt in range(config.RETRIES):
        try:
            headers = {**config.API_HEADERS,
                       "User-Agent": random.choice(config.USER_AGENTS)}
            resp = requests.post(
                config.API_URL,
                json=payload,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                if _error_code(body) in NO_FLIGHT_ERROR_CODES:
                    # 該週沒有航班 → 不是錯誤，回傳空航班清單
                    return {"flights": []}
                raise RuntimeError(json.dumps(body.get("message"), ensure_ascii=False))
            return body["data"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < config.RETRIES - 1:
                time.sleep(config.RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError(f"查詢 {origin}->{dest} {dep_date}~{ret_date} 失敗: {last_err}")


# ---------------------------------------------------------------- 解析
def fmt_time(dt_str: str) -> str:
    """"2026-08-22T08:30:00.000+08:00" → "08:30" """
    m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})", dt_str)
    return m.group(1) if m else dt_str


def fmt_flight_no(no: str) -> str:
    """"JX804" → "JX 804"（航空公司代碼 + 空格 + 班號）"""
    m = re.match(r"([A-Z]{2})(\d+)", no)
    return f"{m.group(1)} {m.group(2)}" if m else no


def pick_roundtrip_price(flight: dict) -> tuple[int | None, str]:
    """從 priceInfo 挑出該航班來回總價（優先 eco，否則最低艙等）。

    回傳 (價格, 艙等)。查無價格回傳 (None, '')。
    """
    price_info = flight.get("priceInfo") or []
    best = None
    for pi in price_info:
        cabin = pi.get("cabin")
        total = (pi.get("totalPrices") or {}).get("total") or {}
        amount = total.get("amount")
        if amount is None:
            continue
        rank = CABIN_ORDER.index(cabin) if cabin in CABIN_ORDER else len(CABIN_ORDER)
        if best is None or rank < best[1]:
            best = (amount, rank, cabin)
    return (best[0], best[2]) if best else (None, "")


def parse_flights(data: dict, route: dict, dep_date: str, ret_date: str,
                  scraped_at: str) -> list[dict]:
    """把 API 回傳轉成輸出 schema 的 record 列表（每個航班一筆）。"""
    records = []
    for flight in data.get("flights", []):
        details = flight.get("flightDetails") or []
        if not details:
            continue
        d0 = details[0]
        dep_dt = d0.get("departure", {}).get("dateTime", "")
        arr_dt = d0.get("arrival", {}).get("dateTime", "")
        flight_no = (flight.get("flightNo") or [""])[0]
        airline_code = d0.get("marketingAirlineCode") or "JX"

        price_total, cabin = pick_roundtrip_price(flight)

        records.append({
            "route_id": route["route_id"],
            "outbound_date": dep_date,
            "return_date": ret_date,
            "outbound_flight_no": fmt_flight_no(flight_no),
            "outbound_departure_time": fmt_time(dep_dt),
            "outbound_arrival_time": fmt_time(arr_dt),
            "airline_code": airline_code,
            "airline_name": AIRLINE_NAMES.get(airline_code, airline_code),
            "price_total": price_total,
            "currency": "TWD",
            "status": "Available",
            "data_completeness": "Complete",
            "scraped_at": scraped_at,
            "source": "starlux_official_api",
        })
    return records


# ---------------------------------------------------------------- 儲存
def load_existing(out_path: Path) -> list[dict]:
    if out_path.exists():
        try:
            return json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"⚠️  {out_path} 損壞，重新建立", file=sys.stderr)
    return []


def merge_records(existing: list[dict], new: list[dict]) -> list[dict]:
    """以 (route_id, outbound_date, return_date, flight_no) 去重合併。"""
    seen = {}
    for r in existing:
        key = (r["route_id"], r["outbound_date"], r["return_date"], r["outbound_flight_no"])
        seen[key] = r
    for r in new:
        key = (r["route_id"], r["outbound_date"], r["return_date"], r["outbound_flight_no"])
        seen[key] = r
    return sorted(seen.values(), key=lambda r: (r["route_id"], r["outbound_date"],
                                                r["return_date"], r["outbound_flight_no"]))


def save_records(records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser(description="星宇航空機票價格爬蟲")
    parser.add_argument("--date", help="參考日期 YYYY-MM-DD（預設今天，測試用）")
    args = parser.parse_args()

    ref = date.fromisoformat(args.date) if args.date else date.today()
    taipei = timezone(timedelta(hours=8))
    now_utc = datetime.now(timezone.utc)
    scraped_at = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out_name = (now_utc + timedelta(hours=8)).strftime("%Y%m%d") + ".json"
    out_path = Path(config.OUTPUT_DIR) / out_name

    trips = gen_trip_dates(ref, config.NUM_WEEKS)
    print(f"📅 參考日期 {ref}，未來 {config.NUM_WEEKS} 週，共 {len(trips)} 組日期")
    for dep, ret in trips:
        print(f"   {dep} (六) → {ret} (日)")

    existing = load_existing(out_path)
    new_records: list[dict] = []
    failed = 0
    total = 0

    # 組出所有 (route, 日期) 查詢組合
    jobs = [(route, dep_date, ret_date)
            for route in config.ROUTES
            for dep_date, ret_date in trips]

    for idx, (route, dep_date, ret_date) in enumerate(jobs):
        # 每次請求前隨機延遲，打亂節奏（首筆不延）
        if idx > 0:
            delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
            print(f"⏳ 等待 {delay:.1f}s ...")
            time.sleep(delay)
        total += 1
        try:
            data = query_flights(route["origin"], route["destination"],
                                 dep_date, ret_date)
            recs = parse_flights(data, route, dep_date, ret_date, scraped_at)
            new_records.extend(recs)
            print(f"✅ {route['route_id']} {dep_date}~{ret_date}: "
                  f"{len(recs)} 航班 {[r['outbound_flight_no'] for r in recs]}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"❌ {route['route_id']} {dep_date}~{ret_date}: {e}", file=sys.stderr)

    records = merge_records(existing, new_records)
    save_records(records, out_path)

    print(f"\n📦 寫入 {out_path}（本次 +{len(new_records)} 筆，累積 {len(records)} 筆）")
    if failed:
        print(f"⚠️  {failed}/{total} 個查詢失敗（已跳過，檔案仍為完整 JSON）", file=sys.stderr)
        # 部分失敗但仍有新資料 → 不算致命錯誤，讓 workflow 繼續 build + commit
        if new_records:
            return 0
        # 完全沒抓到任何新資料 → 中止，避免提交空資料
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
