#!/usr/bin/env python3
"""build_api.py 時段欄位回歸測試（S2：trip JSON flight 缺時間欄位）

背景：航班詳情表格「時間」欄顯示 `— → —`，因為 build_trips() 組裝 flight
物件時沒帶入已抓到的 `outbound_departure_time` / `outbound_arrival_time`。
前端（web/modal.js:208-209）讀取的就是這兩個欄位名。

執行：python3 -m unittest discover -s tests/unit
"""
import glob
import json
import unittest
from pathlib import Path

import build_api

ROOT = Path(build_api.__file__).parent


def rec(fno="JX 804", scraped_at="2026-08-14T00:00:00.000Z",
        dep_time="09:30", arr_time="13:45", price=26008):
    """造一筆爬蟲 record（欄位形狀對齊 data/*.json / fetch_prices.py:160-176）。"""
    r = {
        "route_id": "TPE-NRT",
        "outbound_date": "2026-08-22",
        "return_date": "2026-08-30",
        "outbound_flight_no": fno,
        "airline_code": "JX",
        "airline_name": "星宇航空",
        "price_total": price,
        "currency": "TWD",
        "status": "Available",
        "scraped_at": scraped_at,
    }
    if dep_time is not None:
        r["outbound_departure_time"] = dep_time
    if arr_time is not None:
        r["outbound_arrival_time"] = arr_time
    return r


class TestBuildTripsTimeFields(unittest.TestCase):
    """build_trips() 產出的 flight 物件必須含時段欄位（且非空）。"""

    def test_flight_has_departure_and_arrival_time(self):
        trips = build_api.build_trips([rec()])
        self.assertEqual(len(trips), 1)
        flights = trips[0]["flights"]
        self.assertEqual(len(flights), 1)
        self.assertEqual(flights[0]["outbound_departure_time"], "09:30")
        self.assertEqual(flights[0]["outbound_arrival_time"], "13:45")

    def test_time_fields_present_and_non_empty(self):
        """明確斷言：build 後 flight 物件含時段欄位且值非空。"""
        flights = build_api.build_trips(
            [rec(fno="JX 800", dep_time="08:10", arr_time="12:20"),
             rec(fno="JX 802", dep_time="12:10", arr_time="16:20")]
        )[0]["flights"]
        for f in flights:
            self.assertIn("outbound_departure_time", f)
            self.assertIn("outbound_arrival_time", f)
            self.assertTrue(f["outbound_departure_time"],
                            f"{f['outbound_flight_no']} 去程時間為空")
            self.assertTrue(f["outbound_arrival_time"],
                            f"{f['outbound_flight_no']} 回程時間為空")

    def test_falls_back_to_older_record_with_time(self):
        """最新一筆若缺時段欄位，仍應回退到較舊但有值的紀錄（勿輸出 null）。"""
        old = rec(scraped_at="2026-08-14T00:00:00.000Z",
                  dep_time="09:30", arr_time="13:45")
        new = rec(scraped_at="2026-08-21T00:00:00.000Z", dep_time=None, arr_time=None)
        flight = build_api.build_trips([old, new])[0]["flights"][0]
        self.assertEqual(flight["outbound_departure_time"], "09:30")
        self.assertEqual(flight["outbound_arrival_time"], "13:45")

    def test_missing_time_is_none(self):
        """完全無時段資料時回傳 None（前端顯示 '—'），不可拋錯。"""
        flight = build_api.build_trips(
            [rec(dep_time=None, arr_time=None)])[0]["flights"][0]
        self.assertIsNone(flight["outbound_departure_time"])
        self.assertIsNone(flight["outbound_arrival_time"])

    def test_prefers_newest_non_empty_time(self):
        old = rec(scraped_at="2026-08-14T00:00:00.000Z",
                  dep_time="09:30", arr_time="13:45")
        new = rec(scraped_at="2026-08-21T00:00:00.000Z",
                  dep_time="10:05", arr_time="15:10")
        flight = build_api.build_trips([old, new])[0]["flights"][0]
        self.assertEqual(flight["outbound_departure_time"], "10:05")
        self.assertEqual(flight["outbound_arrival_time"], "15:10")


class TestGeneratedTripJson(unittest.TestCase):
    """已重產的 api/trips/**.json 應全部帶有非空時段欄位。

    api/ 為 gitignore 的建置產物；未 build 時自動 skip（不讓測試依賴產物存在）。
    """

    def setUp(self):
        self.files = sorted(glob.glob(str(ROOT / "api" / "trips" / "*" / "*.json")))
        if not self.files:
            self.skipTest("api/trips/ 尚未產出（先執行 python3 build_api.py）")

    def test_all_generated_flights_have_times(self):
        missing = []
        total = 0
        for path in self.files:
            trip = json.loads(Path(path).read_text(encoding="utf-8"))
            for f in trip.get("flights", []):
                total += 1
                if not f.get("outbound_departure_time") or \
                        not f.get("outbound_arrival_time"):
                    missing.append(f"{Path(path).name}:{f.get('outbound_flight_no')}")
        self.assertGreater(total, 0, "api/trips 內沒有任何航班")
        self.assertEqual(missing, [], f"{len(missing)} 個航班缺時段欄位（共 {total}）")


if __name__ == "__main__":
    unittest.main()
