#!/usr/bin/env python3
"""fetch_schedules.py 離線解析測試（TPE↔NRT，中華航空）

一律只解析 `tests/fixtures/` 的 HTML 快照，**不發任何網路請求**。
fixture 來源：
  https://info.flightmapper.net/route/China_Airlines_CI_TPE_NRT（2026-10-31 抓取，200）
  https://info.flightmapper.net/route/China_Airlines_CI_NRT_TPE（2026-10-31 抓取，200）

對照：docs/research/ci-automation-alternatives.md §A.2
      docs/research/ci-tpe-nrt-schedule.md（冬季 ground truth）

執行：python3 -m unittest discover -s tests/unit
"""
import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import fetch_schedules

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
REF = date(2026, 10, 31)          # 2026/27 冬季班表生效後的第一個週六


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse(route_id: str, fixture: str, ref: date = REF) -> dict[str, dict]:
    html = load_fixture(fixture)
    flights = fetch_schedules.parse_route_html(html, route_id, ref)
    return {f["flight_number"]: f for f in flights}


class TestWinterScheduleTpeNrt(unittest.TestCase):
    """去程 TPE→NRT：ground truth 4 班冬季時刻。"""

    @classmethod
    def setUpClass(cls):
        cls.flights = parse("TPE-NRT", "flightmapper_ci_tpe_nrt.html")

    def test_flight_set(self):
        self.assertEqual(sorted(self.flights), ["CI100", "CI104", "CI106", "CI108"])

    def test_times(self):
        expected = {
            "CI100": ("09:30", "13:30"),
            "CI104": ("12:35", "16:35"),
            "CI108": ("14:25", "18:30"),
            "CI106": ("16:25", "20:30"),
        }
        for fno, (dep, arr) in expected.items():
            with self.subTest(flight=fno):
                self.assertEqual(
                    (self.flights[fno]["departure_time"], self.flights[fno]["arrival_time"]),
                    (dep, arr))

    def test_origin_destination(self):
        for f in self.flights.values():
            with self.subTest(flight=f["flight_number"]):
                self.assertEqual((f["origin"], f["destination"]), ("TPE", "NRT"))

    def test_effective_from(self):
        for f in self.flights.values():
            with self.subTest(flight=f["flight_number"]):
                self.assertEqual(f["effective_from"], "2026-10-25")
                self.assertIsNone(f["effective_to"])   # "Effective from" = open-ended

    def test_aircraft(self):
        self.assertIn("Airbus A330-300 (333)", self.flights["CI100"]["aircraft"])
        self.assertIn("Boeing 777-300ER (77W)", self.flights["CI100"]["aircraft"])
        self.assertEqual(self.flights["CI106"]["aircraft"], ["Airbus A321neo (32Q)"])

    def test_ci100_union_of_split_segments(self):
        """CI100 由 3 段重疊區段組成，聯集必須是每日（不能只取第一段）。"""
        ci100 = self.flights["CI100"]
        self.assertEqual(len(ci100["segments"]), 3)
        self.assertEqual(ci100["days_of_operation"], fetch_schedules.WEEKDAYS)
        self.assertTrue(ci100["is_daily"])
        # 第一段只有 Mon/Tue/Wed/Sun；Thu 來自 10-29 段、Fri/Sat 來自 10-30 段
        self.assertEqual(ci100["segments"][0]["days_of_operation"],
                         ["Mon", "Tue", "Wed", "Sun"])
        self.assertEqual(ci100["segments"][1]["days_of_operation"], ["Thu"])
        self.assertEqual(ci100["segments"][2]["days_of_operation"], ["Fri", "Sat"])

    def test_ci104_days_match_source(self):
        """CI104 在來源頁只有 6 天（Mon/Tue/Wed/Thu/Fri/Sun），週六無區段。

        ⚠️ 這**不是**「來源正確」，而是「來源如此」。已證實來源會缺筆：
        桃園機場官方 FIDS 於 2026-10-31（週六）顯示 CI104 實際有飛
        （OTime 12:35、RTime 12:42），營運日實為每日。
        本測試的用途是**偵測上游資料變動**（若哪天來源補上 Sat，這裡會紅）。
        parser 刻意不自行補值，僅以 `days_unverified` 標記未驗證。
        見 docs/research/ci104-ci105-saturday-check.md。
        """
        self.assertEqual(self.flights["CI104"]["days_of_operation"],
                         ["Mon", "Tue", "Wed", "Thu", "Fri", "Sun"])
        self.assertFalse(self.flights["CI104"]["is_daily"])
        self.assertTrue(self.flights["CI104"]["days_unverified"])

    def test_ci108_union(self):
        ci108 = self.flights["CI108"]
        self.assertEqual(len(ci108["segments"]), 2)
        self.assertTrue(ci108["is_daily"])

    def test_summer_times_excluded(self):
        """夏季時刻（CI100 08:55、CI108 14:40、CI106 16:30）不可混入。"""
        self.assertNotEqual(self.flights["CI100"]["departure_time"], "08:55")
        self.assertNotEqual(self.flights["CI108"]["departure_time"], "14:40")
        self.assertNotEqual(self.flights["CI106"]["departure_time"], "16:30")

    def test_irregular_and_retired_flights_excluded(self):
        """包機 CI2100/CI2108、由他航執飛的 CI9902、夏季 CI192 全部排除。"""
        for noise in ("CI192", "CI2100", "CI2108", "CI9902"):
            self.assertNotIn(noise, self.flights)


class TestWinterScheduleNrtTpe(unittest.TestCase):
    """回程 NRT→TPE：ground truth 4 班冬季時刻。"""

    @classmethod
    def setUpClass(cls):
        cls.flights = parse("NRT-TPE", "flightmapper_ci_nrt_tpe.html")

    def test_flight_set(self):
        self.assertEqual(sorted(self.flights), ["CI101", "CI105", "CI107", "CI109"])

    def test_times(self):
        expected = {
            "CI107": ("09:20", "12:30"),
            "CI101": ("14:35", "17:45"),
            "CI105": ("17:45", "20:55"),
            "CI109": ("19:30", "22:45"),
        }
        for fno, (dep, arr) in expected.items():
            with self.subTest(flight=fno):
                self.assertEqual(
                    (self.flights[fno]["departure_time"], self.flights[fno]["arrival_time"]),
                    (dep, arr))

    def test_origin_destination(self):
        for f in self.flights.values():
            with self.subTest(flight=f["flight_number"]):
                self.assertEqual((f["origin"], f["destination"]), ("NRT", "TPE"))

    def test_daily_flags(self):
        for fno in ("CI101", "CI107", "CI109"):
            with self.subTest(flight=fno):
                self.assertTrue(self.flights[fno]["is_daily"])
        # CI105 同 CI104：來源只有 6 天（無週六）
        self.assertEqual(self.flights["CI105"]["days_of_operation"],
                         ["Mon", "Tue", "Wed", "Thu", "Fri", "Sun"])

    def test_summer_times_and_retired_excluded(self):
        self.assertEqual(self.flights["CI107"]["departure_time"], "09:20")  # 夏季 09:35
        self.assertEqual(self.flights["CI109"]["departure_time"], "19:30")  # 夏季 20:00
        for noise in ("CI193", "CI2101", "CI2109", "CI9909"):
            self.assertNotIn(noise, self.flights)


class TestEffectivePeriod(unittest.TestCase):
    """生效期間四種寫法 + 邊界。"""

    def test_parse_period_formats(self):
        cases = {
            "Effective from 2026-10-25": ("2026-10-25", None),
            "Effective 2026-03-29 through 2026-10-24": ("2026-03-29", "2026-10-24"),
            "Valid until 2026-03-28": (None, "2026-03-28"),
            "Operates only on 2026-03-04": ("2026-03-04", "2026-03-04"),
            " This is a Non-scheduled Passenger flight.": None,
        }
        for text, expected in cases.items():
            with self.subTest(info=text):
                self.assertEqual(fetch_schedules.parse_period(text), expected)

    def test_is_active_boundaries(self):
        ref = date(2026, 10, 25)
        self.assertTrue(fetch_schedules.is_active("2026-10-25", None, ref))   # 含頭
        self.assertFalse(fetch_schedules.is_active("2026-10-26", None, ref))
        self.assertTrue(fetch_schedules.is_active(None, "2026-10-25", ref))   # 含尾
        self.assertFalse(fetch_schedules.is_active(None, "2026-10-24", ref))

    def test_summer_reference_yields_summer_schedule(self):
        """參考日落在夏季時，應回傳夏季時刻（證明期間篩選真的有效）。"""
        flights = parse("TPE-NRT", "flightmapper_ci_tpe_nrt.html", date(2026, 10, 20))
        self.assertEqual(flights["CI100"]["departure_time"], "08:55")
        self.assertEqual(flights["CI106"]["departure_time"], "16:30")
        self.assertEqual(flights["CI104"]["departure_time"], "12:40")
        self.assertEqual(flights["CI192"]["days_of_operation"], ["Thu", "Sat"])

    def test_no_active_flights_raises_instead_of_empty(self):
        """抓不到就明確報錯，不可靜默回空陣列。"""
        html = ("<div class=\"m-box\">FLIGHT SCHEDULE</div>"
                "<div class=\"m-coll\">"
                "<a href=\"/flight/China_Airlines_CI_100\">CI100</a>"
                "<div class=\"m-time\">09:30</div>"
                "<div class=\"m-time\">13:30</div>"
                "<a href=\"/airport/TPE\">TPE</a>"
                "<a href=\"/airport/NRT\">NRT</a>"
                "<div class=\"m-infobox\">Effective 2026-03-29 through 2026-10-24</div>"
                "<div class=\"m-weekday\">Mon<br><i class=\"icon-ok\"></i></div>"
                "</div>")
        with self.assertRaises(RuntimeError):
            fetch_schedules.parse_route_html(html, "TPE-NRT", date(2027, 6, 1))


class TestDaysUnverifiedMarker(unittest.TestCase):
    """誠實標記：營運日未經第二來源交叉驗證（不改動任何營運日數值）。"""

    FIXTURES = (
        ("TPE-NRT", "flightmapper_ci_tpe_nrt.html"),
        ("NRT-TPE", "flightmapper_ci_nrt_tpe.html"),
    )

    def test_every_flight_is_marked_unverified(self):
        """全部航班都要標，不是只標已知缺筆的 CI104/CI105。

        理由：缺筆在來源內部自洽，無法判斷哪些班沒問題；只標已知個案會讓
        其他航班被誤認為「已驗證」。
        """
        for route_id, fixture in self.FIXTURES:
            flights = parse(route_id, fixture)
            self.assertTrue(flights)
            for fno, f in flights.items():
                with self.subTest(route=route_id, flight=fno):
                    self.assertIn("days_unverified", f)
                    self.assertIs(f["days_unverified"], True)
                    self.assertIsInstance(f["unverified_reason"], str)
                    self.assertTrue(f["unverified_reason"].strip())

    def test_marker_does_not_alter_days(self):
        """標記不得改動營運日數值：CI104/CI105 仍是 6 天、不含 Sat。"""
        dep = parse("TPE-NRT", "flightmapper_ci_tpe_nrt.html")
        arr = parse("NRT-TPE", "flightmapper_ci_nrt_tpe.html")
        self.assertEqual(dep["CI104"]["days_of_operation"],
                         ["Mon", "Tue", "Wed", "Thu", "Fri", "Sun"])
        self.assertEqual(arr["CI105"]["days_of_operation"],
                         ["Mon", "Tue", "Wed", "Thu", "Fri", "Sun"])
        self.assertNotIn("Sat", dep["CI104"]["days_of_operation"])
        self.assertNotIn("Sat", arr["CI105"]["days_of_operation"])

    def test_payload_top_level_marker(self):
        """檔案頂層也要有標記（只讀頂層的消費者也看得到）。"""
        html = load_fixture("flightmapper_ci_tpe_nrt.html")
        buf = io.StringIO()
        argv = ["fetch_schedules.py", "--json", "--dry-run", "--routes", "TPE-NRT"]
        with mock.patch.object(fetch_schedules, "fetch_html", return_value=html), \
             mock.patch.object(sys, "argv", argv), \
             contextlib.redirect_stdout(buf), \
             contextlib.redirect_stderr(io.StringIO()):
            rc = fetch_schedules.main()
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIs(payload["days_unverified"], True)
        self.assertTrue(payload["unverified_reason"].strip())
        for route in payload["routes"]:
            for f in route["flights"]:
                self.assertIs(f["days_unverified"], True)
                self.assertTrue(f["unverified_reason"].strip())


class TestOfflineGuarantee(unittest.TestCase):
    """解析路徑不得觸發任何網路請求。"""

    def test_parse_does_not_use_network(self):
        html = load_fixture("flightmapper_ci_tpe_nrt.html")
        with mock.patch.object(fetch_schedules.requests, "get",
                               side_effect=AssertionError("測試不應發網路請求")), \
             mock.patch.object(fetch_schedules.requests.Session, "get",
                               side_effect=AssertionError("測試不應發網路請求")):
            flights = fetch_schedules.parse_route_html(html, "TPE-NRT", REF)
        self.assertEqual(len(flights), 4)


class TestCliHelpers(unittest.TestCase):
    def test_routes_arg(self):
        self.assertEqual(fetch_schedules.parse_routes_arg("TPE-NRT"),
                         ["TPE-NRT"])
        self.assertEqual(fetch_schedules.parse_routes_arg("TPE-NRT, nrt-tpe"),
                         ["TPE-NRT", "NRT-TPE"])

    def test_unsupported_route_rejected(self):
        with self.assertRaises(SystemExit):
            fetch_schedules.parse_routes_arg("TPE-KIX")

    def test_source_url(self):
        self.assertEqual(
            fetch_schedules.source_url("TPE-NRT"),
            "https://info.flightmapper.net/route/China_Airlines_CI_TPE_NRT")
        self.assertEqual(
            fetch_schedules.source_url("NRT-TPE"),
            "https://info.flightmapper.net/route/China_Airlines_CI_NRT_TPE")


if __name__ == "__main__":
    unittest.main()
