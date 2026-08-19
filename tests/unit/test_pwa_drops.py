#!/usr/bin/env python3
"""T10 爬蟲端票價下降偵測 + 通知觸發 — 單元測試（SYS-01~15）

對照：
  docs/test-plans/PWA測試計畫.md §2（SYS-01~15）
  docs/development/PWA.md §1.3 / §3.1 / §4
  docs/bdds/PWA.feature（P2-F / E6 / E11 / E12 / @edge-case drop_last / 每週頻率）

執行：python -m unittest discover -s tests/unit
"""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

import build_api
import config
import fetch_prices


# ---------------------------------------------------------------- 測試工具
def rec(route="TPE-NRT", dep="2026-08-22", ret="2026-08-30", fno="JX 804",
        price=26008, scraped_at="2026-08-14T00:00:00.000Z"):
    """造一筆爬蟲 record（欄位形狀對齊 data/*.json）。"""
    return {
        "route_id": route,
        "outbound_date": dep,
        "return_date": ret,
        "outbound_flight_no": fno,
        "price_total": price,
        "scraped_at": scraped_at,
    }


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ================================================================ SYS-01~03 / SYS-14 下降偵測
class TestDetectDrops(unittest.TestCase):
    """detect_drops：drop_last 為唯一觸發條件（SYS-01 / SYS-02 / SYS-03 / SYS-14）。"""

    def test_sys01_single_drop_triggers(self):
        """SYS-01：任一航班較上次下降 → 觸發，含 drop_amount。"""
        prev = [rec(route="TPE-NRT", fno="JX 804", price=26008)]
        curr = [rec(route="TPE-NRT", fno="JX 804", price=24120)]
        drops = fetch_prices.detect_drops(prev, curr)
        self.assertEqual(drops, [{
            "route": "TPE-NRT",
            "outbound_date": "2026-08-22",
            "return_date": "2026-08-30",
            "flight_no": "JX 804",
            "old_price": 26008,
            "new_price": 24120,
            "drop_amount": 1888,
        }])

    def test_sys02_flat_up_below_avg_do_not_trigger(self):
        """SYS-02：持平／上漲／僅低於全域平均 → 空清單。"""
        prev = [
            rec(fno="JX 804", price=26008),   # 本次持平
            rec(fno="JX 806", price=30000),   # 本次上漲
            rec(fno="JX 808", price=28000),   # 本次低於全域平均但與上次持平
        ]
        curr = [
            rec(fno="JX 804", price=26008),
            rec(fno="JX 806", price=32000),
            rec(fno="JX 808", price=28000),   # 低於平均（全域約 28669）但非 drop_last
        ]
        self.assertEqual(fetch_prices.detect_drops(prev, curr), [])

    def test_sys03_multiple_drops_all_listed(self):
        """SYS-03：多航班下降 → 全部列出（供合併單則，不逐航班連發）。"""
        prev = [
            rec(fno="JX 804", price=26008),
            rec(fno="JX 806", price=30000),
            rec(fno="JX 808", price=28000),
        ]
        curr = [
            rec(fno="JX 804", price=24120),
            rec(fno="JX 806", price=27000),
            rec(fno="JX 808", price=25500),
        ]
        drops = fetch_prices.detect_drops(prev, curr)
        self.assertEqual(len(drops), 3)
        self.assertEqual([d["flight_no"] for d in drops], ["JX 804", "JX 806", "JX 808"])

    def test_sys03_new_flight_no_baseline_no_trigger(self):
        """新航班（基準無此班號）→ 不觸發（無比較基準）。"""
        prev = [rec(fno="JX 804", price=26008)]
        curr = [rec(fno="JX 804", price=24120), rec(fno="JX 999", price=50000)]
        drops = fetch_prices.detect_drops(prev, curr)
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["flight_no"], "JX 804")

    def test_sys14_zero_or_missing_prices_no_false_positive(self):
        """SYS-14：舊價 0／缺價（None）／差額 0／新價缺失 → 不誤判、不拋例外。"""
        prev = [
            rec(fno="A", price=0),          # 舊價 0
            rec(fno="B", price=None),       # 舊價缺
            rec(fno="C", price=26008),      # 新舊差額 0
            rec(fno="D", price=26008),      # 新價缺失
            rec(fno="E", price=0),          # 舊價 0、新價正常 → 不誤判為大降
        ]
        curr = [
            rec(fno="A", price=24120),
            rec(fno="B", price=24120),
            rec(fno="C", price=26008),
            rec(fno="D", price=None),
            rec(fno="E", price=24120),
        ]
        drops = fetch_prices.detect_drops(prev, curr)  # 不拋例外
        self.assertEqual(drops, [])


# ================================================================ SYS-06 / SYS-07 基準載入
class TestLoadBaseline(unittest.TestCase):
    """load_baseline：基準 = 上一週 data/*.json 原始檔（scraped_at < 本次的最大者）。"""

    def _make_data_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _write(self, data_dir, name, records, scraped_at=None):
        if scraped_at:
            records = [dict(r, scraped_at=scraped_at) for r in records]
        write_json(data_dir / name, records)

    def test_sys07_baseline_is_previous_data_file(self):
        """SYS-07：基準 = scraped_at 小於本次的最大檔（data/ 原始檔，非 api/latest.json）。"""
        data_dir = self._make_data_dir()
        prev_records = [rec(fno="JX 804", price=26008)]
        curr_records = [rec(fno="JX 804", price=24120)]
        self._write(data_dir, "20260807.json", prev_records, scraped_at="2026-08-07T00:00:00.000Z")
        self._write(data_dir, "20260814.json", curr_records, scraped_at="2026-08-14T00:00:00.000Z")

        baseline = fetch_prices.load_baseline(data_dir, data_dir / "20260814.json")
        self.assertIsNotNone(baseline)
        self.assertEqual([r["price_total"] for r in baseline], [26008])

    def test_baseline_skips_current_file_and_newer_files(self):
        """基準檔必須 scraped_at < 本次；本次檔與更新檔案不得當基準。"""
        data_dir = self._make_data_dir()
        self._write(data_dir, "20260807.json", [rec(fno="A", price=100)], scraped_at="2026-08-07T00:00:00.000Z")
        curr_path = data_dir / "20260814.json"
        self._write(data_dir, "20260814.json", [rec(fno="A", price=200)], scraped_at="2026-08-14T00:00:00.000Z")

        baseline = fetch_prices.load_baseline(data_dir, curr_path)
        self.assertEqual([r["price_total"] for r in baseline], [100])

    def test_baseline_picks_latest_by_scraped_at(self):
        """多個候選檔 → 取檔名日期（scraped_at 最大）者。"""
        data_dir = self._make_data_dir()
        self._write(data_dir, "20260731.json", [rec(fno="A", price=100)], scraped_at="2026-07-31T00:00:00.000Z")
        self._write(data_dir, "20260807.json", [rec(fno="A", price=150)], scraped_at="2026-08-07T00:00:00.000Z")
        curr_path = data_dir / "20260814.json"
        self._write(data_dir, "20260814.json", [rec(fno="A", price=200)], scraped_at="2026-08-14T00:00:00.000Z")

        baseline = fetch_prices.load_baseline(data_dir, curr_path)
        self.assertEqual([r["price_total"] for r in baseline], [150])

    def test_sys06_first_run_no_baseline_returns_none(self):
        """SYS-06：首次執行（無任何先前檔）→ None。"""
        data_dir = self._make_data_dir()
        curr_path = data_dir / "20260814.json"
        self._write(data_dir, "20260814.json", [rec(price=24120)], scraped_at="2026-08-14T00:00:00.000Z")
        self.assertIsNone(fetch_prices.load_baseline(data_dir, curr_path))

    def test_baseline_skips_non_list_marker_and_corrupt(self):
        """last_notified.json（dict）與損壞檔一律跳過（build_api 相容語意）。"""
        data_dir = self._make_data_dir()
        write_json(data_dir / "last_notified.json",
                   {"iso_week": "2026-W33", "notified_at": "2026-08-14T01:00:00.000Z"})
        (data_dir / "corrupt.json").write_text("{ not valid json", encoding="utf-8")
        self._write(data_dir, "20260807.json", [rec(fno="A", price=100)], scraped_at="2026-08-07T00:00:00.000Z")
        curr_path = data_dir / "20260814.json"
        self._write(data_dir, "20260814.json", [rec(fno="A", price=200)], scraped_at="2026-08-14T00:00:00.000Z")

        baseline = fetch_prices.load_baseline(data_dir, curr_path)
        self.assertEqual([r["price_total"] for r in baseline], [100])


# ================================================================ SYS-04 / SYS-05 / SYS-12 節流
class TestSelectTopDrops(unittest.TestCase):
    """select_top_drops：依下降幅度取前 max_n 筆。"""

    def _drop(self, fno, amount):
        return {
            "route": "TPE-NRT", "outbound_date": "2026-08-22", "return_date": "2026-08-30",
            "flight_no": fno, "old_price": 30000, "new_price": 30000 - amount,
            "drop_amount": amount,
        }

    def test_sys04_over_3_keep_top3_by_drop_amount(self):
        """SYS-04：超過 3 條 → 取下降幅度最大 3 條。"""
        drops = [self._drop(f"JX {i}", a) for i, a in enumerate([100, 500, 200, 900, 300], start=1)]
        top = fetch_prices.select_top_drops(drops, max_n=3)
        self.assertEqual([d["drop_amount"] for d in top], [900, 500, 300])
        self.assertEqual([d["flight_no"] for d in top], ["JX 4", "JX 2", "JX 5"])

    def test_sys05_under_or_equal_3_keep_all_desc(self):
        """SYS-05：≤3 條 → 全保留，依下降幅度遞減。"""
        drops = [self._drop("JX 1", 100), self._drop("JX 2", 500), self._drop("JX 3", 300)]
        top = fetch_prices.select_top_drops(drops, max_n=3)
        self.assertEqual([d["flight_no"] for d in top], ["JX 2", "JX 3", "JX 1"])

    def test_sys12_empty_and_stable_ties(self):
        """SYS-12：空清單 → 空；幅度相同 → 依原始順序穩定排序。"""
        self.assertEqual(fetch_prices.select_top_drops([], max_n=3), [])
        drops = [self._drop("JX 1", 100), self._drop("JX 2", 100), self._drop("JX 3", 50)]
        top = fetch_prices.select_top_drops(drops, max_n=3)
        self.assertEqual([d["flight_no"] for d in top], ["JX 1", "JX 2", "JX 3"])


# ================================================================ SYS-06 should_notify
class TestShouldNotify(unittest.TestCase):
    """should_notify：prev None → 跳過僅建立基準；否則 detect + select。"""

    def test_sys06_no_baseline_skips(self):
        """SYS-06：prev=None → (False, [])。"""
        curr = [rec(price=24120)]
        self.assertEqual(fetch_prices.should_notify(None, curr), (False, []))

    def test_with_drop_returns_selected_drops(self):
        prev = [rec(fno="JX 804", price=26008)]
        curr = [rec(fno="JX 804", price=24120)]
        ok, drops = fetch_prices.should_notify(prev, curr)
        self.assertTrue(ok)
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["drop_amount"], 1888)

    def test_no_drop_returns_false(self):
        prev = [rec(fno="JX 804", price=26008)]
        curr = [rec(fno="JX 804", price=26008)]
        self.assertEqual(fetch_prices.should_notify(prev, curr), (False, []))


# ================================================================ SYS-08 / SYS-15 週頻率守衛
class TestWithinWeeklyWindow(unittest.TestCase):
    """within_weekly_window：同 ISO 週 → True（跳過）；跨週／無記錄 → False。"""

    def test_sys08_same_week_blocks(self):
        """SYS-08：同週已發送 → True（不重複發送）。"""
        now = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)   # 2026-W33 週五
        self.assertTrue(fetch_prices.within_weekly_window("2026-W33", now))

    def test_sys08_cross_week_allows(self):
        """SYS-08：跨週（下週五）→ False（恢復可發送）。"""
        now = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)   # 2026-W34 週五
        self.assertFalse(fetch_prices.within_weekly_window("2026-W33", now))

    def test_sys15_no_record_allows(self):
        """SYS-15：無記錄（首次）→ False（可發送）。"""
        now = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)
        self.assertFalse(fetch_prices.within_weekly_window(None, now))

    def test_sys15_same_week_second_trigger_blocked(self):
        """SYS-15：同週 workflow_dispatch 二次觸發 → 被週守衛阻擋。"""
        now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)   # 同 W33 週六
        self.assertTrue(fetch_prices.within_weekly_window("2026-W33", now))


# ================================================================ SYS-09 payload
class TestBuildNotifyPayload(unittest.TestCase):
    """build_notify_payload：D4 通知承載格式。"""

    def test_sys09_payload_shape_and_types(self):
        """SYS-09：{"drops": [{route, outbound_date, return_date, flight_no, old_price, new_price}]}。"""
        drops = [{
            "route": "TPE-NRT", "outbound_date": "2026-08-22", "return_date": "2026-08-30",
            "flight_no": "JX 804", "old_price": 26008, "new_price": 24120, "drop_amount": 1888,
        }]
        payload = fetch_prices.build_notify_payload(drops)
        self.assertEqual(list(payload.keys()), ["drops"])
        d = payload["drops"][0]
        self.assertEqual(d, {
            "route": "TPE-NRT", "outbound_date": "2026-08-22", "return_date": "2026-08-30",
            "flight_no": "JX 804", "old_price": 26008, "new_price": 24120,
        })
        self.assertNotIn("drop_amount", d)
        for key in ("old_price", "new_price"):
            self.assertIsInstance(d[key], int)

    def test_sys09_at_most_3(self):
        """最多 3 筆（防禦性截斷）。"""
        drops = [{
            "route": "TPE-NRT", "outbound_date": "2026-08-22", "return_date": "2026-08-30",
            "flight_no": f"JX {i}", "old_price": 30000, "new_price": 20000,
            "drop_amount": 10000,
        } for i in range(1, 6)]
        payload = fetch_prices.build_notify_payload(drops)
        self.assertEqual(len(payload["drops"]), 3)

    def test_empty_drops_empty_payload(self):
        self.assertEqual(fetch_prices.build_notify_payload([]), {"drops": []})


# ================================================================ SYS-10 / SYS-11 呼叫
class TestCallNotify(unittest.TestCase):
    """call_notify：Bearer token POST /notify；失敗不 raise。"""

    def test_sys10_sends_bearer_token_and_payload(self):
        """SYS-10：以 Authorization: Bearer <token> POST /notify，body = payload。"""
        payload = {"drops": [{"route": "TPE-NRT"}]}
        with mock.patch("fetch_prices.requests.post") as post:
            post.return_value = mock.Mock(
                status_code=200, ok=True, content=b'{"ok": true, "sent": 0, "failed": 0}',
                json=lambda: {"ok": True, "sent": 0, "failed": 0},
            )
            result = fetch_prices.call_notify(payload, "secret-token", "https://worker.example/notify")
        post.assert_called_once_with(
            "https://worker.example/notify", json=payload, timeout=15,
            headers=mock.ANY,
        )
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret-token")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)

    def test_sys11_401_returns_failure_no_raise(self):
        """SYS-11：401（token 失效）→ {ok: False, status: 401}，不 raise。"""
        with mock.patch("fetch_prices.requests.post") as post:
            post.return_value = mock.Mock(
                status_code=401, ok=False, content=b'{"error": "unauthorized"}',
                json=lambda: {"error": "unauthorized"},
            )
            result = fetch_prices.call_notify({"drops": []}, "bad", "https://worker.example/notify")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 401)

    def test_sys11_network_error_returns_failure_no_raise(self):
        """SYS-11：連線失敗 → {ok: False, status: None}，不 raise。"""
        import requests
        with mock.patch("fetch_prices.requests.post",
                        side_effect=requests.exceptions.ConnectionError("boom")):
            result = fetch_prices.call_notify({"drops": []}, "tok", "https://worker.example/notify")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["status"])


# ================================================================ SYS-13 既有爬蟲回歸
class TestExistingRegression(unittest.TestCase):
    """SYS-13：既有純函式行為不變。"""

    def test_gen_trip_dates_unchanged(self):
        from datetime import date
        trips = fetch_prices.gen_trip_dates(date(2026, 8, 14), 2)
        self.assertEqual(trips, [("2026-08-15", "2026-08-23"),
                                 ("2026-08-22", "2026-08-30")])

    def test_build_payload_unchanged(self):
        payload = fetch_prices.build_payload("TPE", "NRT", "2026-08-22", "2026-08-30")
        self.assertEqual(payload["trip"], "round-trip")
        self.assertEqual(payload["cabin"], config.CABIN)
        self.assertEqual(payload["itineraries"][0],
                         {"departure": "TPE", "arrival": "NRT", "departureDate": "2026-08-22"})

    def test_error_code_unchanged(self):
        self.assertEqual(fetch_prices._error_code({"message": {"code": "01003"}}), "01003")
        self.assertEqual(fetch_prices._error_code({"message": [{"code": "X"}]}), "X")
        self.assertIsNone(fetch_prices._error_code({"message": "plain"}))


# ================================================================ build_api 相容性（非 list JSON 跳過）
class TestBuildApiCompat(unittest.TestCase):
    """data/last_notified.json（dict）不應讓 build_api.py 炸掉（§1.2 註記語意）。"""

    def test_build_api_skips_non_list_marker(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data_dir = Path(tmp.name)
        write_json(data_dir / "20260814.json",
                   [rec(fno="JX 804", price=24120, scraped_at="2026-08-14T00:00:00.000Z")])
        write_json(data_dir / "last_notified.json",
                   {"iso_week": "2026-W33", "notified_at": "2026-08-14T01:00:00.000Z"})

        with mock.patch.object(build_api, "DATA_DIR", data_dir):
            records, files_meta = build_api.load_all_records()

        self.assertEqual(len(records), 1)
        self.assertEqual([f["file"] for f in files_meta], ["20260814.json"])


# ================================================================ main_notify 流程（SYS-15 整合）
class TestMainNotifyFlow(unittest.TestCase):
    """main_notify：成功寫 marker；同週二次觸發被阻擋。"""

    def _setup_data(self, prices_prev, prices_curr):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data_dir = Path(tmp.name)
        write_json(data_dir / "20260807.json",
                   [rec(fno="JX 804", price=p, scraped_at="2026-08-07T00:00:00.000Z")
                    for p in prices_prev])
        curr_path = data_dir / "20260814.json"
        write_json(data_dir / "20260814.json",
                   [rec(fno="JX 804", price=p, scraped_at="2026-08-14T00:00:00.000Z")
                    for p in prices_curr])
        return data_dir, curr_path

    def test_main_notify_success_writes_marker(self):
        """成功發送 → 寫 data/last_notified.json（{iso_week, notified_at}）→ 0。"""
        data_dir, _ = self._setup_data([26008], [24120])
        now_iso = fetch_prices._iso_week(datetime.now(timezone.utc))
        with mock.patch.object(config, "OUTPUT_DIR", str(data_dir)), \
             mock.patch.object(fetch_prices, "call_notify",
                               return_value={"ok": True, "status": 200}) as call, \
             mock.patch.dict(os.environ, {"PUSH_API_TOKEN": "t", "PUSH_NOTIFY_URL": "https://w/n"}):
            with redirect_stdout(StringIO()):
                code = fetch_prices.main_notify()
        self.assertEqual(code, 0)
        call.assert_called_once()
        marker = json.loads((data_dir / "last_notified.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["iso_week"], now_iso)
        self.assertIn("notified_at", marker)

    def test_main_notify_same_week_second_trigger_blocked(self):
        """SYS-15：同週二次觸發 → 不呼叫 /notify、不覆寫 marker。"""
        data_dir, _ = self._setup_data([26008], [24120])
        now_iso = fetch_prices._iso_week(datetime.now(timezone.utc))
        write_json(data_dir / "last_notified.json",
                   {"iso_week": now_iso, "notified_at": "2026-08-14T01:00:00.000Z"})
        with mock.patch.object(config, "OUTPUT_DIR", str(data_dir)), \
             mock.patch.object(fetch_prices, "call_notify") as call, \
             mock.patch.dict(os.environ, {"PUSH_API_TOKEN": "t", "PUSH_NOTIFY_URL": "https://w/n"}):
            with redirect_stdout(StringIO()):
                code = fetch_prices.main_notify()
        self.assertEqual(code, 0)
        call.assert_not_called()
        marker = json.loads((data_dir / "last_notified.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["notified_at"], "2026-08-14T01:00:00.000Z")  # 未覆寫

    def test_main_notify_first_run_skips_and_writes_no_marker(self):
        """首次（無基準）→ 跳過通知、不寫 marker。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data_dir = Path(tmp.name)
        write_json(data_dir / "20260814.json",
                   [rec(fno="JX 804", price=24120, scraped_at="2026-08-14T00:00:00.000Z")])
        with mock.patch.object(config, "OUTPUT_DIR", str(data_dir)), \
             mock.patch.object(fetch_prices, "call_notify") as call, \
             mock.patch.dict(os.environ, {"PUSH_API_TOKEN": "t", "PUSH_NOTIFY_URL": "https://w/n"}):
            with redirect_stdout(StringIO()):
                code = fetch_prices.main_notify()
        self.assertEqual(code, 0)
        call.assert_not_called()
        self.assertFalse((data_dir / "last_notified.json").exists())

    def test_main_notify_call_failure_returns_1(self):
        """call_notify 失敗 → 回傳 1（workflow 察覺），不寫 marker。"""
        data_dir, _ = self._setup_data([26008], [24120])
        with mock.patch.object(config, "OUTPUT_DIR", str(data_dir)), \
             mock.patch.object(fetch_prices, "call_notify",
                               return_value={"ok": False, "status": 401}) as call, \
             mock.patch.dict(os.environ, {"PUSH_API_TOKEN": "t", "PUSH_NOTIFY_URL": "https://w/n"}):
            with redirect_stdout(StringIO()):
                code = fetch_prices.main_notify()
        self.assertEqual(code, 1)
        call.assert_called_once()
        self.assertFalse((data_dir / "last_notified.json").exists())


if __name__ == "__main__":
    unittest.main()
