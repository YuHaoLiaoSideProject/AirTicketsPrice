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
import os
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


# ---------------------------------------------------------------- 通知（drop_last，Phase 2 / T10）
# 對照：docs/development/PWA.md §1.3 / §3.1 / §4；docs/test-plans/PWA測試計畫.md SYS-01~15
# 決策邏輯全部為純函式（資料都在手上），stdlib unittest 直接可測（tests/unit/test_pwa_drops.py）
MAX_NOTIFY_DROPS = config.MAX_NOTIFY_DROPS          # D4：摘要最多 3 條
LAST_NOTIFIED_FILENAME = "last_notified.json"       # 週頻率守衛 marker（build_api.py 對非 list 自動跳過）


def _iso_week(dt: datetime) -> str:
    """ISO 週字串（如 2026-08-14 → "2026-W33"）。"""
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _max_scraped_at(records: list[dict]) -> str | None:
    """檔案內最新 scraped_at（ISO 字串字典序即時間序）。"""
    times = [r.get("scraped_at", "") for r in records if isinstance(r, dict)]
    return max(times) if times else None


def load_baseline(data_dir: Path, current_file: Path) -> list[dict] | None:
    """基準 = 上一週 data/*.json 原始檔（scraped_at 小於本次的最大者）；無 → None（E12 首次無基準）。

    ⚠️ 陷阱：不可用「本次 run 已覆寫的 api/latest.json」當基準——workflow 中 build_api.py
    已在本 step 前重產生 latest.json（內容 = 本次資料），誤用會造成「永遠比對自己 = 無下降」。
    基準一律從 data/ 原始檔依 scraped_at 選取（§4.1 流程圖）。
    """
    current = load_existing(current_file)
    current_scraped_at = _max_scraped_at(current)
    if current_scraped_at is None:
        # 本次檔沒有可比較的 scraped_at → 以「現在」為門檻（檔案仍在，保守當基準線）
        current_scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    candidates: list[tuple[str, Path, list[dict]]] = []
    for p in sorted(data_dir.glob("*.json")):
        if p.resolve() == current_file.resolve():
            continue  # 本次檔不能當自己的基準
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 損壞檔跳過
        if not isinstance(data, list):
            continue  # last_notified.json（dict）等非 list 一律跳過
        file_scraped_at = _max_scraped_at(data)
        if file_scraped_at is None or file_scraped_at >= current_scraped_at:
            continue  # 必須早於本次抓取
        candidates.append((p.name, p, data))

    if not candidates:
        return None
    # 檔名日期最大者 = 最近一次抓取（YYYYMMDD.json 字典序即日期序）
    _, _, baseline = max(candidates, key=lambda c: c[0])
    return baseline


def detect_drops(prev: list[dict], curr: list[dict]) -> list[dict]:
    """逐航班（route_id, outbound_date, return_date, outbound_flight_no）比對舊價→新價：
      僅 new_price < old_price 才列入；drop_amount = old - new。
      持平 / 上漲 / 僅低於全域平均 / 僅創近期新低 → 一律不觸發（BDD @edge-case「非 drop_last 一律不觸發」）；
      缺價（None）、差額 0、舊價 0 → 不誤判、不拋例外（SYS-14）。
    @returns [{route, outbound_date, return_date, flight_no, old_price, new_price, drop_amount}]
    """
    def key(r):
        return (r["route_id"], r["outbound_date"], r["return_date"], r["outbound_flight_no"])

    prev_map = {key(r): r for r in prev if isinstance(r, dict)}
    drops: list[dict] = []
    for r in curr:
        if not isinstance(r, dict):
            continue
        old_rec = prev_map.get(key(r))
        if old_rec is None:
            continue  # 新航班（基準無）→ 無比較基準，不觸發
        old_price = old_rec.get("price_total")
        new_price = r.get("price_total")
        # 缺價 / 0 價 / 非數字 → 不誤判、不拋例外（SYS-14）
        if not old_price or not new_price:
            continue
        if not isinstance(old_price, (int, float)) or not isinstance(new_price, (int, float)):
            continue
        old_price = int(old_price)
        new_price = int(new_price)
        if new_price >= old_price:
            continue  # 持平 / 上漲不觸發
        drops.append({
            "route": r["route_id"],
            "outbound_date": r["outbound_date"],
            "return_date": r["return_date"],
            "flight_no": r["outbound_flight_no"],
            "old_price": old_price,
            "new_price": new_price,
            "drop_amount": old_price - new_price,
        })
    return drops


def select_top_drops(drops: list[dict], max_n: int = MAX_NOTIFY_DROPS) -> list[dict]:
    """依 drop_amount 遞減排序取前 max_n 筆；≤max_n 全保留（SYS-05）；
      幅度相同 → 依原始順序穩定排序（不 flaky，SYS-12）；空清單 → 空。"""
    if not drops:
        return []
    # Python sorted 為穩定排序：drop_amount 相同時保留原始順序
    return sorted(drops, key=lambda d: d["drop_amount"], reverse=True)[:max_n]


def should_notify(prev: list[dict] | None, curr: list[dict]) -> tuple[bool, list[dict]]:
    """prev 為 None → (False, [])：跳過通知、僅建立基準（E12 / SYS-06）；
      否則 detect_drops + select_top_drops（SYS-01~03）。"""
    if prev is None:
        return False, []
    drops = detect_drops(prev, curr)
    return (True, select_top_drops(drops)) if drops else (False, [])


def within_weekly_window(last_notified_iso: str | None, now: datetime) -> bool:
    """同 ISO 週（now.isocalendar() 相同）→ True（跳過，SYS-08）；
      跨週 / 無記錄 → False（SYS-15：同週 workflow_dispatch 二次觸發被阻擋）。"""
    if not last_notified_iso:
        return False
    return last_notified_iso == _iso_week(now)


def build_notify_payload(drops: list[dict]) -> dict:
    """→ {"drops": [ {route, outbound_date, return_date, flight_no, old_price, new_price} ]}
      （Tech Decision D4 通知承載規格；SYS-09；防禦性截斷至最多 3 筆）。"""
    out = []
    for d in select_top_drops(drops, MAX_NOTIFY_DROPS):
        out.append({
            "route": d["route"],
            "outbound_date": d["outbound_date"],
            "return_date": d["return_date"],
            "flight_no": d["flight_no"],
            "old_price": int(d["old_price"]),
            "new_price": int(d["new_price"]),
        })
    return {"drops": out}


def call_notify(payload: dict, token: str, url: str, timeout: int = 15) -> dict:
    """POST url 附 Authorization: Bearer <token>（SYS-10）；body = payload JSON。
      任何失敗（401 / 網路錯誤 / timeout）→ 回傳 {ok: False, status}，**不 raise**——
      資料已於本 step 前 commit，通知失敗不得中斷或污染流程（E6 / SYS-11）。"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}
        if resp.ok:
            return {"ok": True, "status": resp.status_code, **body}
        return {"ok": False, "status": resp.status_code,
                "error": body.get("error") or f"HTTP {resp.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": str(e)}


def _last_notified_path() -> Path:
    return Path(config.OUTPUT_DIR) / LAST_NOTIFIED_FILENAME


def _read_last_notified() -> str | None:
    """讀週頻率守衛 marker，回傳 iso_week（無 marker / 缺欄位 → None）。"""
    p = _last_notified_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    iso = data.get("iso_week") if isinstance(data, dict) else None
    if iso:
        return iso
    # 舊格式相容：僅有時間戳 → 由 notified_at 推導 ISO 週
    notified = data.get("notified_at") if isinstance(data, dict) else None
    if notified:
        try:
            return _iso_week(datetime.fromisoformat(notified.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _write_last_notified(now: datetime) -> None:
    """寫週頻率守衛 marker {iso_week, notified_at, scraped_at}（隨 data/ commit）。"""
    p = _last_notified_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "iso_week": _iso_week(now),
        "notified_at": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "scraped_at": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _latest_data_file(data_dir: Path) -> Path | None:
    """本次 data 檔 = 最近寫入者（mtime 最大；同秒以檔名排序補齊），限 list JSON。"""
    files = []
    for p in data_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, list):
            files.append(p)
    if not files:
        return None
    return max(files, key=lambda p: (p.stat().st_mtime, p.name))


def main_notify(dry_run: bool = False) -> int:
    """fetch_prices.py --notify 入口（workflow「Detect drops & notify」step 呼叫）：
      1. 找本次 data 檔（最近寫入者）+ load_baseline（上一週）
      2. should_notify → (False, []) → 印「無下降（或首次），跳過通知」→ 0
      3. 讀 LAST_NOTIFIED_FILE → within_weekly_window → True → 印「同週已發送，跳過」→ 0
      4. build_notify_payload → call_notify（env PUSH_API_TOKEN / PUSH_NOTIFY_URL）
      5. 成功（ok）→ 寫 LAST_NOTIFIED_FILE（{iso_week, notified_at}）→ 0；失敗 → 印錯誤 → 1
        （workflow 該 step 標記失敗；資料已 commit，E6）"""
    data_dir = Path(config.OUTPUT_DIR)
    current_file = _latest_data_file(data_dir)
    if current_file is None:
        print("❌ data/ 沒有任何 JSON 檔，無法進行下降偵測", file=sys.stderr)
        return 1
    curr = load_existing(current_file)
    prev = load_baseline(data_dir, current_file)

    send, drops = should_notify(prev, curr)
    if not send:
        reason = "首次執行無基準，跳過通知（僅建立基準）" if prev is None else "無票價下降，跳過通知"
        print(f"ℹ️  {reason}")
        return 0

    if within_weekly_window(_read_last_notified(), datetime.now(timezone.utc)):
        print("ℹ️  同週已發送過通知，跳過（週頻率守衛）")
        return 0

    payload = build_notify_payload(drops)
    print(f"📉 偵測到 {len(drops)} 條票價下降（已節流）：")
    for d in payload["drops"]:
        print(f"   {d['route']} {d['flight_no']} {d['outbound_date']}~{d['return_date']} "
              f"NT${d['old_price']} → NT${d['new_price']}")
    if dry_run:
        print("🔍 dry-run：不實際發送，payload 如下")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    token = os.environ.get("PUSH_API_TOKEN")
    if not token:
        print("❌ 缺少環境變數 PUSH_API_TOKEN（T11 設定 GitHub secret）", file=sys.stderr)
        return 1
    url = os.environ.get("PUSH_NOTIFY_URL") or config.PUSH_NOTIFY_URL

    result = call_notify(payload, token, url)
    if result.get("ok"):
        _write_last_notified(datetime.now(timezone.utc))
        print(f"✅ 通知已發送（status {result.get('status')}）：{result.get('sent', '?')} 位訂閱者")
        return 0
    print(f"❌ 通知發送失敗：{result}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser(description="星宇航空機票價格爬蟲")
    parser.add_argument("--date", help="參考日期 YYYY-MM-DD（預設今天，測試用）")
    parser.add_argument("--notify", action="store_true",
                        help="爬蟲後執行票價下降通知（Phase 2；讀 data/ 比對 → 呼叫 Worker /notify）")
    parser.add_argument("--dry-run", action="store_true",
                        help="（搭配 --notify）不實際發送，只輸出將送出的 payload")
    parser.add_argument("--region", choices=["jp", "other", "all"], default="all",
                        help="只爬特定區域航線：jp=日本4條, other=非日本4條, all=全部8條")
    args = parser.parse_args()

    if args.notify:
        sys.exit(main_notify(dry_run=args.dry_run))   # 不重新爬蟲

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

    # 依 --region 選擇航線
    if args.region == "jp":
        routes = config.ROUTES_JP
        print(f"🌏 區域：日本 (JP) - {len(routes)} 條航線")
    elif args.region == "other":
        routes = config.ROUTES_OTHER
        print(f"🌏 區域：非日本 (OTHER) - {len(routes)} 條航線")
    else:
        routes = config.ROUTES
        print(f"🌏 區域：全部 (ALL) - {len(routes)} 條航線")

    # 組出所有 (route, 日期) 查詢組合
    jobs = [(route, dep_date, ret_date)
            for route in routes
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
