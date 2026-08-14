#!/usr/bin/env python3
"""把 data/*.json 原始檔編譯成靜態 API 成品（api/）

在每次爬蟲執行後呼叫，產出：

  api/index.json                       # 目錄與總覽（消費端從這裡開始）
  api/latest.json                      # 最新快照（每組合保留最新一筆）
  api/trips/<route>_<dep>_<ret>.json   # 每趟旅程的價格歷史（畫趨勢圖用）

用法：
    python build_api.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
API_DIR = ROOT / "api"
TRIPS_DIR = API_DIR / "trips"


def load_all_records() -> tuple[list[dict], list[dict]]:
    """讀取所有 data/*.json，回傳 (全部紀錄, 檔案清單 meta)。"""
    records: list[dict] = []
    files_meta: list[dict] = []
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"⚠️  跳過損壞檔案 {p.name}")
            continue
        if not isinstance(data, list):
            continue
        records.extend(data)
        files_meta.append({
            "file": p.name,
            "url": f"data/{p.name}",
            "records": len(data),
            "scraped_at": max((r.get("scraped_at", "") for r in data), default=""),
        })
    return records, files_meta


def latest_snapshot(records: list[dict]) -> list[dict]:
    """每 (航線, 去程, 回程, 班號) 保留 scraped_at 最新的一筆。"""
    latest: dict[tuple, dict] = {}
    for r in records:
        key = (r["route_id"], r["outbound_date"], r["return_date"],
               r["outbound_flight_no"])
        if key not in latest or r["scraped_at"] > latest[key]["scraped_at"]:
            latest[key] = r
    return sorted(latest.values(),
                  key=lambda r: (r["route_id"], r["outbound_date"],
                                 r["return_date"], r["outbound_flight_no"]))


def build_trips(records: list[dict]) -> list[dict]:
    """把紀錄依 (航線, 去程, 回程) 分組，每個航班一條價格歷史。"""
    trips: dict[tuple, dict] = {}
    for r in records:
        key = (r["route_id"], r["outbound_date"], r["return_date"])
        flight = r["outbound_flight_no"]
        trip = trips.setdefault(key, {"flights": {}})
        trip["flights"].setdefault(flight, []).append(r)

    out = []
    for (route_id, dep, ret), trip in sorted(trips.items()):
        flights = []
        for flight_no in sorted(trip["flights"]):
            history = sorted(trip["flights"][flight_no],
                             key=lambda r: r["scraped_at"])
            flights.append({
                "outbound_flight_no": flight_no,
                "airline_code": history[-1]["airline_code"],
                "airline_name": history[-1]["airline_name"],
                "history": [{
                    "scraped_at": h["scraped_at"],
                    "price_total": h["price_total"],
                    "currency": h["currency"],
                    "status": h["status"],
                } for h in history],
            })
        out.append({
            "route_id": route_id,
            "outbound_date": dep,
            "return_date": ret,
            "url": f"api/trips/{route_id}_{dep}_{ret}.json",
            "flights": flights,
        })
    return out


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> int:
    records, files_meta = load_all_records()
    if not records:
        print("❌ data/ 沒有任何 JSON 檔")
        return 1

    latest = latest_snapshot(records)
    trips = build_trips(records)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    index = {
        "generated_at": generated_at,
        "source": "starlux_official_api",
        "description": "星宇航空來回票價歷史（去程週六/回程下週日）",
        "total_records": len(records),
        "latest_snapshot_records": len(latest),
        "routes": sorted({r["route_id"] for r in records}),
        "trip_count": len(trips),
        "files": files_meta,
        "latest_file": files_meta[-1]["file"] if files_meta else None,
        "latest": "api/latest.json",
        "trips": [t["url"] for t in trips],
    }

    write_json(API_DIR / "index.json", index)
    write_json(API_DIR / "latest.json", latest)
    for t in trips:
        write_json(TRIPS_DIR / f"{t['route_id']}_{t['outbound_date']}_{t['return_date']}.json", t)

    print(f"✅ api/index.json  （{len(files_meta)} 個來源檔，{len(records)} 筆）")
    print(f"✅ api/latest.json （快照 {len(latest)} 筆）")
    print(f"✅ api/trips/     （{len(trips)} 趟旅程）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
