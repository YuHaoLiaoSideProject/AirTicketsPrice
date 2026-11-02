"""`fetch_ci_fares.py` 的離線單元測試（不發任何網路請求）。

證明方式：整個檔案在 import 後就把 `requests.Session.post`、`requests.post`、
`requests.Session.get`、`requests.get` 換成「一呼叫就 AssertionError」的假函式，
因此任何意外連網都會直接讓測試爆掉（fixture 走的是純解析函式 + 注入的假 transport）。
"""
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fetch_ci_fares as ci  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "ci_air_bounds_tpe_nrt_2026-11-20.json"


def _no_network(*args, **kwargs):  # pragma: no cover - 只在被誤用時觸發
    raise AssertionError("測試不可發出網路請求")


class NetworkGuardMixin:
    """把所有可能連網的 requests 入口都封死。"""

    def setUp(self):
        super().setUp()
        self._patches = [
            mock.patch.object(ci.requests.Session, "post", _no_network),
            mock.patch.object(ci.requests.Session, "get", _no_network),
            mock.patch.object(ci.requests, "post", _no_network),
            mock.patch.object(ci.requests, "get", _no_network),
            # 預設把 time.sleep 也 mock 掉：請求間隔（P1-3）不該讓全套件變慢。
            # 要驗證 sleep 的測試會自己再 patch 一次並斷言呼叫內容。
            mock.patch.object(ci.time, "sleep"),
        ]
        for p in self._patches:
            self.addCleanup(p.stop)
            p.start()


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeTransport:
    """記錄請求並回傳預先準備好的回應（完全不碰 socket）。"""

    def __init__(self, responses, repeat_last=False):
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "data": data, "headers": dict(headers or {})})
        if not self.responses:
            raise AssertionError(f"多餘的請求：{url}")
        if self.repeat_last and len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


def _session_ctx():
    return {
        "saved_at": "2026-11-01T04:00:00.000Z",
        "cookies": [
            {"name": "datadome", "value": "DD", "domain": ".china-airlines.com", "path": "/"},
            {"name": "visid_incap_2933525", "value": "VIS", "domain": ".china-airlines.com"},
            {"name": "incap_ses_724_2933525", "value": "SES", "domain": ".china-airlines.com"},
            {"name": "hostonly", "value": "H", "domain": "api-des.china-airlines.com"},
            {"name": "other", "value": "O", "domain": ".example.com"},
        ],
        "x_d_token": "3:AAA==:BBB",
        "private_facts": "eyJhbGciOiJSU0EtT0FFUC0yNTYi... jwe",
        "public_facts_jwt": 'eyJhbGciOiJub25lIn0.eyJzdWIiOiJmYWN0In0.',
        "ama_client_ref": "081c1460-b2cc-434b-a4a5-7f891b7037e3:0",
        "user_agent": "UA/1.0",
        "fact_template": {"keyValuePairs": [
            {"key": "originLocationCode1", "value": "TPE"},
            {"key": "pointOfSale", "value": "SEA"},
            {"key": "officeId", "value": "TPECI08AB"},
            {"key": "ip", "value": "IP-1.2.3.4"},
        ]},
    }


# ---------------------------------------------------------------- fixture 解析
class TestParseAirBounds(NetworkGuardMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.body = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_parses_all_flights_and_fares(self):
        flights = ci.parse_air_bounds(self.body, "TPE-NRT")
        self.assertEqual([f["flight_no"] for f in flights],
                         ["CI 100", "CI 104", "CI 108", "CI 106"])
        self.assertEqual(sum(f["fare_count"] for f in flights), 20)
        for flight in flights:
            self.assertTrue(flight["flight_id"].startswith("SEG-CI"))
            self.assertEqual(flight["origin"], "TPE")
            self.assertEqual(flight["destination"], "NRT")
            self.assertEqual(flight["departure_date"], "2026-11-20")
            self.assertEqual(flight["route_id"], "TPE-NRT")

    def test_matches_verified_baseline_prices(self):
        """規格書 §〇 的實測基準：CI100 經濟艙 17,107、CI104 經濟艙 12,451。"""
        flights = {f["flight_no"]: f for f in ci.parse_air_bounds(self.body, "TPE-NRT")}
        self.assertEqual(flights["CI 100"]["cheapest_price"], 17107)
        self.assertEqual(flights["CI 104"]["cheapest_price"], 12451)
        # 商務艙對照（同一份實測樣本）
        ci104 = flights["CI 104"]
        biz = [f for f in ci104["fares"] if f["cabin"] == "business"]
        self.assertIn(18777, [f["price_total"] for f in biz])

    def test_fare_fields_include_class_quota_and_currency(self):
        flights = ci.parse_air_bounds(self.body, "TPE-NRT")
        fare = flights[0]["fares"][0]
        self.assertEqual(fare["currency"], "TWD")
        self.assertIsNotNone(fare["booking_class"])
        self.assertIsInstance(fare["quota"], int)
        self.assertIsNotNone(fare["base"])
        self.assertIsNotNone(fare["total_taxes"])
        # 含稅總價 = base + totalTaxes（同一幣別）
        self.assertEqual(fare["price_total"], fare["base"] + fare["total_taxes"])

    def test_fares_sorted_by_price_and_cheapest_marked(self):
        flights = ci.parse_air_bounds(self.body, "TPE-NRT")
        for flight in flights:
            prices = [f["price_total"] for f in flight["fares"]]
            self.assertEqual(prices, sorted(prices))
            self.assertEqual(flight["cheapest_fare"]["price_total"],
                             min(prices))
        summary = ci.summarize_prices(flights)
        self.assertEqual(summary["flight_count"], 4)
        # 每班最低價的 min / max（CI108 7,085 … CI100 17,107）
        self.assertEqual(summary["price_min"], 7085)
        self.assertEqual(summary["price_max"], 17107)
        self.assertEqual(summary["fare_count"], 20)
        self.assertEqual(summary["currency"], "TWD")

    def test_empty_payload_is_not_silently_zero(self):
        """200 但沒有資料 → parse 回空清單（由 query_leg 標成 no_data，不是 0 元）。"""
        self.assertEqual(ci.parse_air_bounds({"data": {"airBoundGroups": []}}, "TPE-NRT"), [])
        self.assertEqual(ci.parse_air_bounds({}, "TPE-NRT"), [])
        self.assertEqual(ci.summarize_prices([]), {"flight_count": 0, "fare_count": 0})


class TestParseFlightId(NetworkGuardMixin, unittest.TestCase):
    def test_parse_ok(self):
        meta = ci.parse_flight_id("SEG-CI104-TPENRT-2026-11-20-1235")
        self.assertEqual(meta["flight_no"], "CI 104")
        self.assertEqual(meta["departure_time"], "12:35")
        self.assertEqual(meta["departure_date"], "2026-11-20")

    def test_parse_garbage(self):
        self.assertEqual(ci.parse_flight_id("nonsense"), {})
        self.assertEqual(ci.parse_flight_id(""), {})


# ---------------------------------------------------------------- 請求形狀
class TestRequestShape(NetworkGuardMixin, unittest.TestCase):
    def test_build_fact_replaces_dates_and_keeps_pos(self):
        fact = ci.build_fact(_session_ctx()["fact_template"], "TPE", "NRT",
                             date(2026, 12, 1), date(2026, 12, 2))
        pairs = {p["key"]: p["value"] for p in fact["keyValuePairs"]}
        self.assertEqual(pairs["originLocationCode1"], "TPE")
        self.assertEqual(pairs["destinationLocationCode1"], "NRT")
        self.assertEqual(pairs["departureDateTime1"], "2026-12-01")
        self.assertEqual(pairs["originLocationCode2"], "NRT")
        self.assertEqual(pairs["departureDateTime2"], "2026-12-02")
        self.assertEqual(pairs["pointOfSale"], "SEA")
        self.assertEqual(pairs["officeId"], "TPECI08AB")
        self.assertEqual(pairs["ip"], "IP-1.2.3.4")

    def test_build_air_bounds_payload_requests_only_outbound_leg(self):
        payload = ci.build_air_bounds_payload("TPE", "NRT", date(2026, 11, 20),
                                             date(2026, 11, 21))
        self.assertEqual(payload["commercialFareFamilies"],
                         ["ECONEW", "PREMIUMECO", "BZCLASS"])
        outbound, inbound = payload["itineraries"]
        self.assertTrue(outbound["isRequestedBound"])
        self.assertEqual(outbound["departureDateTime"], "2026-11-20T00:00:00.000")
        self.assertFalse(inbound["isRequestedBound"])
        self.assertEqual(payload["travelers"], [{"passengerTypeCode": "ADT"}])

    def test_cookie_header_filters_by_host(self):
        header = ci.cookie_header(_session_ctx()["cookies"], "api-des.china-airlines.com")
        self.assertIn("datadome=DD", header)
        self.assertIn("hostonly=H", header)
        self.assertNotIn("other=O", header)

    def test_incap_spa_info_rebuilt_from_cookies(self):
        info = ci.incap_spa_info(_session_ctx())
        self.assertEqual(info, "visid_incap_2933525=VIS; incap_ses_724_2933525=SES")

    def test_client_facts_format(self):
        header = ci.build_client_facts(_session_ctx())
        self.assertTrue(header.startswith('eyJhbGciOiJub25lIn0.eyJzdWIiOiJmYWN0In0.'))
        self.assertIn(", ", header)
        self.assertTrue(header.endswith("jwe"))


# ---------------------------------------------------------------- 三態分離 / 請求流程
class TestQueryLeg(NetworkGuardMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.body = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_ok_status_and_parsed_flights(self):
        transport = FakeTransport([
            FakeResponse(200, {"access_token": "TOKEN"}),
            FakeResponse(200, self.body),
        ])
        result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                              date(2026, 11, 20), ci.RequestBudget(10), post=transport)
        self.assertEqual(result["status"], ci.STATUS_OK)
        self.assertEqual(result["status_label"], "有價")
        self.assertEqual(result["flight_count"], 4)
        self.assertEqual(len(transport.calls), 2)
        # 請求本身要帶到武裝取得的反爬憑證
        token_call, bounds_call = transport.calls
        self.assertEqual(token_call["headers"]["x-d-token"], "3:AAA==:BBB")
        self.assertIn("client_id=", token_call["data"])
        self.assertEqual(bounds_call["headers"]["authorization"], "Bearer TOKEN")
        self.assertIn("ama-client-facts", bounds_call["headers"])
        self.assertEqual(bounds_call["headers"]["ama-client-ref"].split(":")[1], "0")

    def test_no_data_status_when_200_but_empty(self):
        transport = FakeTransport([
            FakeResponse(200, {"access_token": "TOKEN"}),
            FakeResponse(200, {"data": {"airBoundGroups": []}}),
        ])
        result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                              date(2026, 12, 31), ci.RequestBudget(10), post=transport)
        self.assertEqual(result["status"], ci.STATUS_NO_DATA)
        self.assertEqual(result["flights"], [])
        self.assertEqual(result["flight_count"], 0)
        self.assertIn("evidence", result)
        self.assertIsNone(result["error"])

    def test_failed_status_on_5xx(self):
        calls = []

        def transport(url, data=None, headers=None, timeout=None, **kwargs):
            calls.append(url)
            if "token/initialization" in url:
                return FakeResponse(200, {"access_token": "TOKEN"})
            return FakeResponse(500, {"error": "boom"})

        with mock.patch.object(ci.time, "sleep"):  # 不吃掉退避秒數
            result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                                  date(2026, 12, 31), ci.RequestBudget(10), post=transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertIn("500", result["error"])
        # 3 次嘗試之後才放棄（1 + 重試 2 次），每次 2 個請求
        self.assertEqual(len(calls), 6)

    def test_403_stops_immediately_without_retry(self):
        transport = FakeTransport([FakeResponse(403, {"url": "captcha"})])
        with mock.patch.object(ci.time, "sleep") as sleep:
            result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                                  date(2026, 11, 20), ci.RequestBudget(10), post=transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertTrue(result.get("blocked"))
        self.assertEqual(len(transport.calls), 1)
        sleep.assert_not_called()

    def test_429_stops_immediately(self):
        transport = FakeTransport([FakeResponse(429, {})])
        result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                              date(2026, 11, 20), ci.RequestBudget(10), post=transport)
        self.assertTrue(result.get("blocked"))
        self.assertEqual(len(transport.calls), 1)

    def test_401_is_reported_as_failed_401(self):
        transport = FakeTransport([FakeResponse(401, {})])
        with mock.patch.object(ci.time, "sleep"):
            result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                                  date(2026, 11, 20), ci.RequestBudget(10), post=transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertEqual(result["http_status"], 401)

    def test_request_budget_enforced(self):
        transport = FakeTransport([FakeResponse(200, {"access_token": "TOKEN"})])
        with mock.patch.object(ci.time, "sleep"):
            result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                                  date(2026, 11, 20), ci.RequestBudget(1), post=transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertIn("請求上限", result["error"])
        self.assertTrue(result.get("budget_exhausted"))
        self.assertEqual(len(transport.calls), 1)


# ---------------------------------------------------------------- session 快取
class TestSessionCache(NetworkGuardMixin, unittest.TestCase):
    def _write(self, tmp: Path, saved_at: str) -> Path:
        path = tmp / "ci_session.json"
        path.write_text(json.dumps({
            "saved_at": saved_at,
            "cookies": [{"name": "a", "value": "b", "domain": ".china-airlines.com"}],
            "x_d_token": "tok",
            "private_facts": "jwe",
        }), encoding="utf-8")
        return path

    def test_fresh_cache_is_used(self):
        import tempfile
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
            path = self._write(Path(tmp), "2026-11-01T03:50:00.000Z")
            loaded = ci.load_session(path, ttl=1200, now=now)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["_age_seconds"], 600)

    def test_expired_cache_is_ignored(self):
        from datetime import datetime, timezone
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
            path = self._write(Path(tmp), "2026-11-01T03:00:00.000Z")  # 60 分鐘前
            self.assertIsNone(ci.load_session(path, ttl=1200, now=now))

    def test_broken_cache_is_ignored(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ci_session.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(ci.load_session(path))

    def test_missing_cache_is_none(self):
        self.assertIsNone(ci.load_session(Path("/nonexistent/ci_session.json")))


# ---------------------------------------------------------------- CLI / 路徑防呆
class TestCliGuards(NetworkGuardMixin, unittest.TestCase):
    def test_output_path_must_not_be_in_data(self):
        with self.assertRaises(SystemExit):
            ci.guard_output_path(Path("data/20261101.json"))
        with self.assertRaises(SystemExit):
            ci.guard_output_path(Path("data/sub/deep.json"))
        self.assertEqual(ci.guard_output_path(Path("ci_fares/x.json")),
                         Path("ci_fares/x.json"))

    def test_default_out_path(self):
        self.assertEqual(ci.default_out_path("TPE-NRT", [date(2026, 11, 20)]),
                         Path("ci_fares/TPE-NRT_2026-11-20.json"))
        self.assertEqual(
            ci.default_out_path("TPE-NRT", [date(2026, 11, 20), date(2026, 11, 21)]),
            Path("ci_fares/TPE-NRT_2026-11-20_2026-11-21.json"))

    def test_route_whitelist(self):
        self.assertEqual(ci.parse_route("TPE-NRT"), ("TPE-NRT", "TPE", "NRT"))
        with self.assertRaises(SystemExit):
            ci.parse_route("TPE-KIX")
        with self.assertRaises(SystemExit):
            ci.parse_route("NRT-TPE")

    def test_date_parsing(self):
        args = mock.Mock(date="2026-11-20", start_date=None, end_date=None)
        self.assertEqual(ci.parse_dates(args), [date(2026, 11, 20)])

        args = mock.Mock(date=None, start_date="2026-11-20", end_date="2026-11-22")
        self.assertEqual(len(ci.parse_dates(args)), 3)

        for kwargs in ({"date": None, "start_date": None, "end_date": None},
                       {"date": "2026-11-20", "start_date": "2026-11-20", "end_date": None},
                       {"date": None, "start_date": "2026-11-22", "end_date": "2026-11-20"}):
            with self.assertRaises(SystemExit):
                ci.parse_dates(mock.Mock(**kwargs))

    def test_payload_status_legend_and_risk_notes(self):
        results = [{
            "query_date": "2026-11-20", "route_id": "TPE-NRT",
            "status": ci.STATUS_NO_DATA, "status_label": ci.STATUS_LABELS[ci.STATUS_NO_DATA],
            "flights": [], "flight_count": 0, "fare_count": 0, "error": None,
        }]
        payload = ci.build_payload("TPE-NRT", "TPE", "NRT", results, _session_ctx(),
                                   ci.RequestBudget(10), "2026-11-01T04:00:00.000Z")
        self.assertEqual(payload["totals"]["no_data"], 1)
        self.assertEqual(payload["totals"]["ok"], 0)
        self.assertIn("cookie_ttl_observed_minutes", payload["session"])
        self.assertIn("cookie_reuse_is_measured_not_guaranteed", payload["session"])
        self.assertIn("DataDome", " ".join(payload["session"]["bot_protection"]))
        self.assertTrue(payload["risk_notes"])

    def test_playwright_is_optional_dependency(self):
        """playwright 若不存在，必須給出清楚的安裝指引（不是 ImportError 裸奔）。"""
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("playwright"):
                raise ImportError("No module named 'playwright'")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(ci.ArmError) as ctx:
                ci._load_playwright()
        self.assertIn("pip install playwright", str(ctx.exception))


# ====================================================================== P0-1
# 結構異常 **不得** 被誤標成 no_data（"確定無資料" vs "看不懂資料"）
def _structure_body(prices: dict, *, fare_family: str = "ECONEW") -> dict:
    """單一 airBoundGroup / 單一 airBound 的最小合法骨架，只換 prices。"""
    flight_id = "SEG-CI100-TPENRT-2026-11-20-0930"
    return {"data": {"airBoundGroups": [{
        "boundDetails": {
            "segments": [{"flightId": flight_id}],
            "originLocationCode": "TPE",
            "destinationLocationCode": "NRT",
        },
        "airBounds": [{
            "fareFamilyCode": fare_family,
            "availabilityDetails": [{
                "flightId": flight_id, "cabin": "economy", "bookingClass": "B",
                "statusCode": "A", "quota": 1,
            }],
            "prices": prices,
        }],
    }]}}


class TestStructureAnomalyIsNeverNoData(NetworkGuardMixin, unittest.TestCase):
    """P0-1：結構異常 → failed；結構完整且明確為空 → no_data。"""

    def test_missing_total_is_failed_not_no_data(self):
        body = _structure_body({"totalPrices": [{"amount": 12345}]})
        classified = ci.classify_air_bounds(body, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_FAILED)
        self.assertEqual(classified["flights"], [])
        self.assertIn("total", classified["error"])
        self.assertIn("結構異常", classified["error"])
        self.assertEqual(classified["parse_stats"]["rejected_no_total"], 1)

    def test_wrong_total_type_is_failed(self):
        body = _structure_body({"totalPrices": [{"total": "12345"}]})
        classified = ci.classify_air_bounds(body, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_FAILED)
        self.assertIn("型別錯誤", classified["error"])
        self.assertEqual(classified["parse_stats"]["rejected_bad_total"], 1)

    def test_empty_price_array_is_no_data(self):
        body = _structure_body({"totalPrices": []})
        classified = ci.classify_air_bounds(body, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_NO_DATA)
        self.assertIsNone(classified["error"])

    def test_zero_total_is_no_data(self):
        body = _structure_body({"totalPrices": [{"total": 0}]})
        classified = ci.classify_air_bounds(body, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_NO_DATA)

    def test_empty_groups_is_no_data(self):
        classified = ci.classify_air_bounds({"data": {"airBoundGroups": []}}, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_NO_DATA)
        self.assertEqual(classified["evidence"]["airBoundGroups"], 0)

    def test_missing_air_bound_groups_is_failed(self):
        """bounds 為空但無法判定原因（缺 key）→ failed，不可標成查無。"""
        classified = ci.classify_air_bounds({"data": {}}, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_FAILED)
        self.assertIn("airBoundGroups", classified["error"])

    def test_data_wrong_type_is_failed(self):
        classified = ci.classify_air_bounds({"data": [1, 2, 3]}, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_FAILED)
        self.assertIn("data", classified["error"])

    def test_group_without_segments_is_failed(self):
        body = {"data": {"airBoundGroups": [{"boundDetails": {}, "airBounds": []}]}}
        classified = ci.classify_air_bounds(body, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_FAILED)
        self.assertIn("segments", classified["error"])

    def test_query_leg_propagates_structure_failure(self):
        """整合層：200 + 欄位改名 → status=failed（不是 no_data），且不重試。"""
        transport = FakeTransport([
            FakeResponse(200, {"access_token": "TOKEN"}),
            FakeResponse(200, _structure_body({"totalPrices": [{"amount": 12345}]})),
        ])
        result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                             date(2026, 11, 20), ci.RequestBudget(10), post=transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertIn("total", result["error"])
        self.assertNotIn("evidence", [k for k in result if result[k] is None])
        # 結構異常屬確定性失敗：同一個回應重試無意義 → 只花 2 個請求
        self.assertEqual(len(transport.calls), 2)

    def test_query_leg_empty_prices_is_no_data(self):
        transport = FakeTransport([
            FakeResponse(200, {"access_token": "TOKEN"}),
            FakeResponse(200, _structure_body({"totalPrices": []})),
        ])
        result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                             date(2026, 11, 20), ci.RequestBudget(10), post=transport)
        self.assertEqual(result["status"], ci.STATUS_NO_DATA)
        self.assertIsNone(result["error"])

    def test_verified_fixture_still_parses_as_ok(self):
        """正常樣本不受嚴格分類影響（避免修 P0-1 弄壞正常路徑）。"""
        body = json.loads(FIXTURE.read_text(encoding="utf-8"))
        classified = ci.classify_air_bounds(body, "TPE-NRT")
        self.assertEqual(classified["status"], ci.STATUS_OK)
        self.assertEqual(classified["parse_stats"]["anomalies"], [])
        self.assertEqual(len(classified["flights"]), 4)


# ====================================================================== main()
class _FakeSession:
    def __init__(self, transport):
        self._transport = transport

    def post(self, url, data=None, headers=None, timeout=None, **kwargs):
        return self._transport(url, data=data, headers=headers, timeout=timeout, **kwargs)


def _ok_transport(body):
    """token 200 + air-bounds 200（正常結果）。"""
    def transport(url, data=None, headers=None, timeout=None, **kwargs):
        if "token/initialization" in url:
            return FakeResponse(200, {"access_token": "TOKEN"})
        return FakeResponse(200, body)
    return transport


def _always_500_transport(url, data=None, headers=None, timeout=None, **kwargs):
    if "token/initialization" in url:
        return FakeResponse(200, {"access_token": "TOKEN"})
    return FakeResponse(500, {"error": "boom"})


class MainTestBase(NetworkGuardMixin, unittest.TestCase):
    """main() 的離線測試骨架：不連網、不冷啟動、不睡真的延遲。"""

    def setUp(self):
        super().setUp()
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.out = self.dir / "out.json"
        self.body = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def run_main(self, transport, args, *, session_ctx="default", arm=None, events=None):
        """跑一次 `main()`。

        - `events`：給一個 list 就會依序記錄 `("request", url)` / `("sleep", 秒)`，
          用來驗證「對外請求之間一定有延遲、且順序正確」。
        - `time.sleep` 一律被 mock，測試不會真的等。
        """
        ctx = _session_ctx() if session_ctx == "default" else session_ctx
        if events is not None:
            inner = transport

            def transport(url, data=None, headers=None, timeout=None, **kwargs):
                events.append(("request", url))
                return inner(url, data=data, headers=headers, timeout=timeout, **kwargs)

        sleep_kwargs = ({"side_effect": lambda seconds: events.append(("sleep", seconds))}
                        if events is not None else {})
        sleep_patch = mock.patch.object(ci.time, "sleep", **sleep_kwargs)
        self.sleep_mock = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        patches = [
            mock.patch.object(ci.requests, "Session", lambda *a, **k: _FakeSession(transport)),
            mock.patch.object(ci, "load_session", return_value=ctx),
            mock.patch.object(ci, "session_cache_path", return_value=self.dir / "cache.json"),
        ]
        if arm is None:
            patches.append(mock.patch.object(
                ci, "arm_session", side_effect=AssertionError("測試不應啟動瀏覽器")))
        else:
            patches.append(mock.patch.object(ci, "arm_session", side_effect=arm))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(sys, "argv", ["fetch_ci_fares.py", *args]):
            return ci.main()

    def argv_single(self):
        return ["--date", "2026-11-20", "--out", str(self.out)]

    def argv_range(self, start, end):
        return ["--start-date", start, "--end-date", end, "--out", str(self.out)]

    def write_existing_success(self) -> dict:
        old = {
            "generated_at": "OLD-RUN",
            "totals": {"queries": 1, "ok": 1, "no_data": 0, "failed": 0},
            "queries": [{
                "query_date": "2026-11-20", "route_id": "TPE-NRT",
                "status": "ok", "flights": [{"flight_no": "CI 100"}],
                "flight_count": 1, "fare_count": 1, "error": None,
            }],
        }
        self.out.write_text(json.dumps(old), encoding="utf-8")
        return old


# ====================================================================== P0-2
class TestOutputWriteSafety(MainTestBase):
    """P0-2：原子寫入；失敗的輪不得覆寫既有成功資料。"""

    def test_failed_run_keeps_previous_success_file_intact(self):
        self.write_existing_success()
        before = self.out.read_bytes()
        exit_code = self.run_main(_always_500_transport, self.argv_single())
        self.assertEqual(exit_code, ci.EXIT_ALL_FAILED)
        self.assertEqual(self.out.read_bytes(), before, "失敗輪不可改寫既有成功檔")
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["generated_at"],
                         "OLD-RUN")

    def test_aborted_run_keeps_previous_success_file_intact(self):
        self.write_existing_success()
        before = self.out.read_bytes()
        transport = FakeTransport([FakeResponse(403, {"url": "captcha"})])
        exit_code = self.run_main(
            transport, self.argv_range("2026-11-20", "2026-11-21"))
        self.assertEqual(exit_code, ci.EXIT_ABORTED)
        self.assertEqual(self.out.read_bytes(), before)

    def test_no_data_run_may_write(self):
        self.write_existing_success()
        transport = _ok_transport({"data": {"airBoundGroups": []}})
        exit_code = self.run_main(transport, self.argv_single())
        self.assertEqual(exit_code, ci.EXIT_OK)
        payload = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(payload["queries"][0]["status"], "no_data")

    def test_successful_overwrite_backs_up_previous_and_records_it(self):
        self.write_existing_success()
        exit_code = self.run_main(_ok_transport(self.body), self.argv_single())
        self.assertEqual(exit_code, ci.EXIT_OK)
        payload = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertNotEqual(payload["generated_at"], "OLD-RUN")
        self.assertEqual(payload["previous"]["generated_at"], "OLD-RUN")
        bak = self.out.with_name(self.out.stem + ci.BACKUP_SUFFIX)
        self.assertTrue(bak.exists())
        self.assertEqual(json.loads(bak.read_text(encoding="utf-8"))["generated_at"],
                         "OLD-RUN")

    def test_atomic_write_leaves_no_partial_file_when_replace_fails(self):
        self.write_existing_success()
        before = self.out.read_bytes()
        tmp = self.out.with_name(self.out.name + ci.TMP_SUFFIX)
        with mock.patch.object(ci.os, "replace", side_effect=OSError("disk full")):
            exit_code = self.run_main(_ok_transport(self.body), self.argv_single())
        self.assertEqual(exit_code, ci.EXIT_ENV)
        self.assertEqual(self.out.read_bytes(), before, "原檔不可被 truncate")
        self.assertFalse(tmp.exists(), "失敗後不可留下半截 .tmp")

    def test_failed_run_writes_when_no_existing_file(self):
        """沒有舊資料時仍要記錄失敗（否則完全看不到發生什麼事）。"""
        exit_code = self.run_main(_always_500_transport, self.argv_single())
        self.assertEqual(exit_code, ci.EXIT_ALL_FAILED)
        payload = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(payload["queries"][0]["status"], "failed")


# ====================================================================== P0-3
class TestExitCodes(MainTestBase):
    """P0-3：exit code 語意（見 fetch_ci_fares.py docstring / README）。"""

    def test_all_ok_is_zero(self):
        self.assertEqual(self.run_main(_ok_transport(self.body), self.argv_single()),
                         ci.EXIT_OK)

    def test_all_no_data_is_zero(self):
        transport = _ok_transport({"data": {"airBoundGroups": []}})
        self.assertEqual(self.run_main(transport, self.argv_single()), ci.EXIT_OK)

    def test_all_failed_is_three(self):
        self.assertEqual(self.run_main(_always_500_transport, self.argv_single()),
                         ci.EXIT_ALL_FAILED)
        self.assertNotEqual(ci.EXIT_ALL_FAILED, ci.EXIT_OK)

    def test_partial_failure_is_four(self):
        calls = {"n": 0}

        def transport(url, data=None, headers=None, timeout=None, **kwargs):
            if "token/initialization" in url:
                return FakeResponse(200, {"access_token": "TOKEN"})
            calls["n"] += 1
            return FakeResponse(200, self.body) if calls["n"] == 1 else FakeResponse(500, {})

        exit_code = self.run_main(transport, self.argv_range("2026-11-20", "2026-11-21"))
        self.assertEqual(exit_code, ci.EXIT_PARTIAL)
        payload = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(payload["totals"]["ok"], 1)
        self.assertEqual(payload["totals"]["failed"], 1)

    def test_aborted_by_403_after_success_is_five(self):
        transport = FakeTransport([
            FakeResponse(200, {"access_token": "TOKEN"}),
            FakeResponse(200, self.body),
            FakeResponse(403, {"url": "captcha"}),
        ])
        exit_code = self.run_main(
            transport, self.argv_range("2026-11-20", "2026-11-21"))
        self.assertEqual(exit_code, ci.EXIT_ABORTED)

    def test_environment_error_is_one_when_browser_unavailable(self):
        def arm_fails(*args, **kwargs):
            raise ci.ArmError("需要 Playwright 才能冷啟動取得 cookie")

        exit_code = self.run_main(_ok_transport(self.body), self.argv_single(),
                                  session_ctx=None, arm=arm_fails)
        self.assertEqual(exit_code, ci.EXIT_ENV)

    def test_write_failure_is_one(self):
        blocker = self.dir / "blocker"
        blocker.write_text("x", encoding="utf-8")
        bad_out = blocker / "sub" / "out.json"  # mkdir 一定失敗（NotADirectoryError）
        exit_code = self.run_main(_ok_transport(self.body),
                                  ["--date", "2026-11-20", "--out", str(bad_out)])
        self.assertEqual(exit_code, ci.EXIT_ENV)

    def test_unexpected_exception_is_six(self):
        with mock.patch.object(ci, "query_leg", side_effect=ValueError("boom")):
            exit_code = self.run_main(_ok_transport(self.body), self.argv_single())
        self.assertEqual(exit_code, ci.EXIT_UNEXPECTED)

    def test_usage_error_exit_code_is_two(self):
        with self.assertRaises(SystemExit) as ctx:
            ci.parse_dates(mock.Mock(date="2026-13-40", start_date=None, end_date=None))
        self.assertEqual(ctx.exception.code, ci.EXIT_USAGE)

    def test_compute_exit_code_matrix(self):
        cases = [
            ({"ok": 3, "no_data": 0, "failed": 0}, False, ci.EXIT_OK),
            ({"ok": 0, "no_data": 3, "failed": 0}, False, ci.EXIT_OK),
            ({"ok": 1, "no_data": 0, "failed": 1}, False, ci.EXIT_PARTIAL),
            ({"ok": 0, "no_data": 0, "failed": 2}, False, ci.EXIT_ALL_FAILED),
            ({"ok": 5, "no_data": 0, "failed": 0}, True, ci.EXIT_ABORTED),
            ({"ok": 0, "no_data": 0, "failed": 0}, False, ci.EXIT_OK),
        ]
        for totals, aborted, want in cases:
            self.assertEqual(ci.compute_exit_code(totals, aborted=aborted), want, totals)

    def test_dry_run_does_not_arm_browser_or_write_file(self):
        """--dry-run 在有可用快取時：不開瀏覽器、不寫檔（無副作用）。"""
        exit_code = self.run_main(
            _ok_transport(self.body),
            ["--date", "2026-11-20", "--dry-run", "--out", str(self.out)])
        self.assertEqual(exit_code, ci.EXIT_OK)
        self.assertFalse(self.out.exists())


# ====================================================================== P0-4
class TestGracefulFailurePaths(MainTestBase):
    """P0-4：原本會 traceback 的路徑，全部改成友善訊息 + 非零結束碼。"""

    def test_non_utf8_session_cache_is_ignored(self):
        path = self.dir / "ci_session.json"
        path.write_bytes(b"\xff\xfe\x00not utf8")
        self.assertIsNone(ci.load_session(path))

    def test_session_cache_with_non_string_saved_at_is_ignored(self):
        path = self.dir / "ci_session.json"
        path.write_text(json.dumps({
            "saved_at": 1730000000,
            "cookies": [{"name": "a", "value": "b", "domain": ".china-airlines.com"}],
            "x_d_token": "tok",
            "private_facts": "jwe",
        }), encoding="utf-8")
        self.assertIsNone(ci.load_session(path))

    def test_session_cache_with_list_saved_at_is_ignored(self):
        path = self.dir / "ci_session.json"
        path.write_text(json.dumps({
            "saved_at": [1, 2],
            "cookies": [{"name": "a", "value": "b", "domain": ".china-airlines.com"}],
            "x_d_token": "tok",
            "private_facts": "jwe",
        }), encoding="utf-8")
        self.assertIsNone(ci.load_session(path))

    def test_save_session_oserror_returns_false_without_raising(self):
        with mock.patch.object(ci, "atomic_write_text", side_effect=OSError("read-only fs")):
            ok = ci.save_session(self.dir / "cache.json", {"a": 1})
        self.assertFalse(ok)

    def test_save_session_success_returns_true(self):
        self.assertTrue(ci.save_session(self.dir / "cache.json", {"a": 1}))

    def test_browser_launch_failure_becomes_arm_error(self):
        class _FakeChromium:
            executable_path = "/nonexistent/chrome"

            def launch_persistent_context(self, *args, **kwargs):
                raise RuntimeError("Executable doesn't exist at /x/chrome")

        class _FakePlaywright:
            chromium = _FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(ci, "profile_dir", return_value=self.dir / "profile"), \
                mock.patch.object(ci, "_load_playwright", return_value=lambda: _FakePlaywright()):
            with self.assertRaises(ci.ArmError) as ctx:
                ci.arm_session("TPE-NRT", "TPE", "NRT", date(2026, 11, 20),
                               date(2026, 11, 21))
        message = str(ctx.exception)
        self.assertIn("冷啟動失敗", message)
        self.assertIn("Executable doesn't exist", message)
        self.assertIn("playwright install chromium", message)

    def test_goto_timeout_becomes_arm_error(self):
        class _Page:
            def goto(self, *a, **k):
                raise TimeoutError("Page.goto: Timeout 60000ms exceeded")

            def set_default_timeout(self, *a):
                pass

            def on(self, *a):
                pass

            def mouse(self):
                raise AssertionError

        class _Ctx:
            pages = []

            def new_page(self):
                return _Page()

            def cookies(self):
                return []

            def close(self):
                pass

        class _FakeChromium:
            executable_path = "/nonexistent/chrome"

            def launch_persistent_context(self, *args, **kwargs):
                return _Ctx()

        class _FakePlaywright:
            chromium = _FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(ci, "profile_dir", return_value=self.dir / "profile"), \
                mock.patch.object(ci, "_load_playwright", return_value=lambda: _FakePlaywright()):
            with self.assertRaises(ci.ArmError) as ctx:
                ci.arm_session("TPE-NRT", "TPE", "NRT", date(2026, 11, 20),
                               date(2026, 11, 21))
        self.assertIn("Timeout", str(ctx.exception))

    def test_profile_mkdir_oserror_becomes_arm_error(self):
        blocker = self.dir / "blocker"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch.object(ci, "profile_dir", return_value=blocker / "profile"):
            with self.assertRaises(ci.ArmError) as ctx:
                ci.arm_session("TPE-NRT", "TPE", "NRT", date(2026, 11, 20),
                               date(2026, 11, 21))
        self.assertIn("profile", str(ctx.exception))

    def test_main_reports_playwright_missing_without_traceback(self):
        def arm_fails(*args, **kwargs):
            raise ci.ArmError("需要 Playwright\n    pip install playwright")

        exit_code = self.run_main(_ok_transport(self.body), self.argv_single(),
                                  session_ctx=None, arm=arm_fails)
        self.assertEqual(exit_code, ci.EXIT_ENV)

    def test_atomic_write_text_cleans_tmp_on_failure(self):
        path = self.dir / "x.json"
        with mock.patch.object(ci.os, "replace", side_effect=OSError("nope")):
            with self.assertRaises(OSError):
                ci.atomic_write_text(path, "{}")
        self.assertFalse(path.exists())
        self.assertFalse(path.with_name(path.name + ci.TMP_SUFFIX).exists())

    def test_atomic_write_text_writes_content(self):
        path = self.dir / "y.json"
        ci.atomic_write_text(path, '{"a": 1}')
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})


# ====================================================================== P1-1
class TestMaxRequestsAbortsRun(MainTestBase):
    """P1-1：`--max-requests` 用盡 → 立即停止整輪、剩餘日期未執行、exit 非 0。"""

    def test_budget_abort_stops_remaining_dates_and_exits_nonzero(self):
        events = []
        exit_code = self.run_main(
            _ok_transport(self.body),
            self.argv_range("2026-11-20", "2026-11-22") + ["--max-requests", "2"],
            events=events)
        # 有中止 → exit code 必須非 0，且用與 403/429 不同的專用 code
        self.assertEqual(exit_code, ci.EXIT_BUDGET_ABORT)
        self.assertNotEqual(exit_code, ci.EXIT_OK)
        self.assertNotEqual(exit_code, ci.EXIT_ABORTED)

        payload = json.loads(self.out.read_text(encoding="utf-8"))
        queries = payload["queries"]
        self.assertEqual(len(queries), 3)
        # 第一個日期正常跑完（2 個請求就把上限用光）
        self.assertEqual(queries[0]["status"], ci.STATUS_OK)
        # 第二個日期：一進場就撞上限 → 中止
        self.assertEqual(queries[1]["status"], ci.STATUS_FAILED)
        self.assertTrue(queries[1]["budget_exhausted"])
        self.assertTrue(queries[1]["aborted"])
        self.assertEqual(queries[1]["abort_reason"], ci.ABORT_REASON_BUDGET)
        # 第三個日期：完全未執行，原因字串與 403/429 中止不同
        third = queries[2]
        self.assertEqual(third["status"], ci.STATUS_FAILED)
        self.assertTrue(third["aborted"])
        self.assertEqual(third["abort_reason"], ci.ABORT_REASON_BUDGET)
        self.assertIn("未執行", third["error"])
        self.assertIn("請求上限", third["error"])
        self.assertNotIn("反爬", third["error"])
        # 摘要：已用請求數 / 上限 + 中止原因
        self.assertEqual(payload["requests_used"], 2)
        self.assertEqual(payload["requests_limit"], 2)
        self.assertTrue(payload["aborted"])
        self.assertEqual(payload["abort_reason"], ci.ABORT_REASON_BUDGET)
        self.assertIn("已用盡", payload["abort_message"])
        # 中止後：不再發請求、也不再 sleep（剩餘日期連間隔都不必等）
        self.assertEqual(len([e for e in events if e[0] == "request"]), 2)
        self.assertEqual(len([e for e in events if e[0] == "sleep"]), 1)

    def test_budget_and_blocked_use_distinct_reasons_and_exit_codes(self):
        self.assertNotEqual(ci.abort_message_for(ci.ABORT_REASON_BUDGET, 30),
                            ci.abort_message_for(ci.ABORT_REASON_BLOCKED, 30))
        self.assertIn("請求上限", ci.abort_message_for(ci.ABORT_REASON_BUDGET, 30))
        self.assertIn("反爬", ci.abort_message_for(ci.ABORT_REASON_BLOCKED, 30))

        transport = FakeTransport([
            FakeResponse(200, {"access_token": "TOKEN"}),
            FakeResponse(200, self.body),
            FakeResponse(403, {"url": "captcha"}),
        ])
        exit_code = self.run_main(
            transport, self.argv_range("2026-11-20", "2026-11-22"))
        self.assertEqual(exit_code, ci.EXIT_ABORTED)
        payload = json.loads(self.out.read_text(encoding="utf-8"))
        blocked_date, placeholder = payload["queries"][1], payload["queries"][2]
        # 被攔截的那一天
        self.assertTrue(blocked_date["blocked"])
        self.assertTrue(blocked_date["aborted"])
        self.assertEqual(blocked_date["abort_reason"], ci.ABORT_REASON_BLOCKED)
        # 剩餘日期：未執行，原因字串明確區分於請求上限中止
        self.assertTrue(placeholder["aborted"])
        self.assertEqual(placeholder["abort_reason"], ci.ABORT_REASON_BLOCKED)
        self.assertIn("反爬", placeholder["error"])
        self.assertIn("未執行", placeholder["error"])
        self.assertNotIn("請求上限", placeholder["error"])
        self.assertEqual(payload["abort_reason"], ci.ABORT_REASON_BLOCKED)
        self.assertIn("反爬", payload["abort_message"])

    def test_budget_exhaustion_after_partial_success_is_still_nonzero(self):
        """即使前面有成功日期，用盡上限也絕不會回 0。"""
        transport = _ok_transport(self.body)
        exit_code = self.run_main(
            transport,
            self.argv_range("2026-11-20", "2026-11-21") + ["--max-requests", "3"])
        self.assertEqual(exit_code, ci.EXIT_BUDGET_ABORT)
        payload = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(payload["totals"]["ok"], 1)
        self.assertEqual(payload["totals"]["failed"], 1)
        self.assertEqual(payload["abort_reason"], ci.ABORT_REASON_BUDGET)

    def test_compute_exit_code_distinguishes_two_abort_reasons(self):
        totals = {"ok": 1, "no_data": 0, "failed": 2}
        self.assertEqual(
            ci.compute_exit_code(totals, aborted=True, abort_reason=ci.ABORT_REASON_BUDGET),
            ci.EXIT_BUDGET_ABORT)
        self.assertEqual(
            ci.compute_exit_code(totals, aborted=True, abort_reason=ci.ABORT_REASON_BLOCKED),
            ci.EXIT_ABORTED)
        self.assertEqual(
            ci.compute_exit_code(totals, aborted=True), ci.EXIT_ABORTED)


# ====================================================================== P1-2
class TestRetryClassification(NetworkGuardMixin, unittest.TestCase):
    """P1-2：只有 5xx / timeout / 連線錯誤重試；400/404/422 等 4xx 立即停止。"""

    def _query(self, transport, budget=None):
        with mock.patch.object(ci.time, "sleep"):  # 不吃掉退避與間隔秒數
            return ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                                date(2026, 11, 20), budget or ci.RequestBudget(20),
                                post=transport)

    def test_normal_date_is_exactly_two_requests(self):
        transport = FakeTransport([
            FakeResponse(200, {"access_token": "TOKEN"}),
            FakeResponse(200, json.loads(FIXTURE.read_text(encoding="utf-8"))),
        ])
        result = self._query(transport)
        self.assertEqual(result["status"], ci.STATUS_OK)
        self.assertEqual(len(transport.calls), 2)

    def test_400_404_422_fail_fast_on_first_request(self):
        for status in (400, 404, 422):
            with self.subTest(status=status):
                transport = FakeTransport([FakeResponse(status, {"error": "nope"})])
                with mock.patch.object(ci.time, "sleep") as sleep:
                    result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE",
                                          "NRT", date(2026, 11, 20), ci.RequestBudget(20),
                                          post=transport)
                self.assertEqual(result["status"], ci.STATUS_FAILED)
                self.assertEqual(result["http_status"], status)
                self.assertEqual(len(transport.calls), 1, "確定性 4xx 不可重試")
                sleep.assert_not_called()  # 既不退避也不白等
                self.assertFalse(result.get("blocked"))
                self.assertFalse(result.get("budget_exhausted"))
                self.assertFalse(result.get("aborted"))

    def test_404_on_air_bounds_stops_after_two_requests(self):
        transport = FakeTransport([FakeResponse(200, {"access_token": "TOKEN"}),
                                   FakeResponse(404, {})])
        result = self._query(transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertEqual(result["http_status"], 404)
        self.assertEqual(len(transport.calls), 2)  # 若重試會變 6 個請求

    def test_other_4xx_also_fails_fast(self):
        for status in (405, 418, 451):
            with self.subTest(status=status):
                transport = FakeTransport([FakeResponse(status, {})])
                result = self._query(transport)
                self.assertEqual(result["status"], ci.STATUS_FAILED)
                self.assertEqual(len(transport.calls), 1)

    def test_500_on_token_retries_twice_for_three_requests(self):
        transport = FakeTransport([FakeResponse(500, {})], repeat_last=True)
        result = self._query(transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertIn("500", result["error"])
        self.assertEqual(result["http_status"], 500)
        self.assertEqual(len(transport.calls), 3)  # 1 + 重試 2 次

    def test_500_on_air_bounds_retries_up_to_six_requests(self):
        calls = []

        def transport(url, data=None, headers=None, timeout=None, **kwargs):
            calls.append(url)
            if "token/initialization" in url:
                return FakeResponse(200, {"access_token": "TOKEN"})
            return FakeResponse(500, {"error": "boom"})

        result = self._query(transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertEqual(result["http_status"], 500)
        self.assertEqual(len(calls), 6)  # 每次嘗試 2 個請求 × 3 次嘗試（上限）

    def test_timeout_is_retried(self):
        calls = []

        def transport(url, **kwargs):
            calls.append(url)
            raise ci.requests.exceptions.Timeout("read timed out")

        result = self._query(transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertEqual(len(calls), 3)  # timeout 屬暫時性錯誤 → 重試 2 次

    def test_connection_error_is_retried_but_other_exceptions_are_not(self):
        calls = []

        def conn_transport(url, **kwargs):
            calls.append(url)
            raise ci.requests.exceptions.ConnectionError("reset by peer")

        self._query(conn_transport)
        self.assertEqual(len(calls), 3)

        calls.clear()

        def bug_transport(url, **kwargs):
            calls.append(url)
            raise ValueError("回應看不懂")

        result = self._query(bug_transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        self.assertEqual(len(calls), 1)  # 非網路型例外＝確定性失敗，不重試


# ====================================================================== P1-3
class TestRequestPacing(MainTestBase):
    """P1-3：所有對外請求之間都有隨機 2–4s；本地操作不 sleep。"""

    def test_sleep_is_interleaved_between_every_pair_of_requests(self):
        events = []
        exit_code = self.run_main(_ok_transport(self.body),
                                  self.argv_range("2026-11-20", "2026-11-21"),
                                  events=events)
        self.assertEqual(exit_code, ci.EXIT_OK)
        kinds = [kind for kind, _ in events]
        # 4 個請求（每日 token → air-bounds）之間恰好 3 個間隔，
        # 且沒有前導 sleep（第一個請求之前）也沒有結尾 sleep
        self.assertEqual(kinds, ["request", "sleep", "request",
                                 "sleep", "request", "sleep", "request"])
        for kind, value in events:
            if kind == "sleep":
                self.assertGreaterEqual(value, ci.REQUEST_DELAY_MIN)
                self.assertLessEqual(value, ci.REQUEST_DELAY_MAX)

    def test_local_operations_do_not_sleep(self):
        """讀舊檔 / 備份 / 原子寫檔等本地操作不延遲（只有對外請求才 sleep）。"""
        self.write_existing_success()
        events = []
        exit_code = self.run_main(_ok_transport(self.body), self.argv_single(), events=events)
        self.assertEqual(exit_code, ci.EXIT_OK)
        self.assertEqual([kind for kind, _ in events], ["request", "sleep", "request"])
        self.assertTrue(self.out.exists())
        self.assertTrue(self.out.with_name(self.out.stem + ci.BACKUP_SUFFIX).exists())

    def test_sleep_between_air_bounds_and_retries(self):
        """同日 token → air-bounds、每次重試之前都有延遲（背靠背 = 失敗）。"""
        events = []

        def transport(url, data=None, headers=None, timeout=None, **kwargs):
            events.append(("request", url))
            if "token/initialization" in url:
                return FakeResponse(200, {"access_token": "TOKEN"})
            return FakeResponse(500, {"error": "boom"})

        with mock.patch.object(ci.time,
                               "sleep", side_effect=lambda s: events.append(("sleep", s))):
            result = ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                                  date(2026, 11, 20), ci.RequestBudget(20), post=transport)
        self.assertEqual(result["status"], ci.STATUS_FAILED)
        requests_seen = [e for e in events if e[0] == "request"]
        sleeps = [e for e in events if e[0] == "sleep"]
        self.assertEqual(len(requests_seen), 6)
        self.assertGreaterEqual(len(sleeps), 5)  # 5 個請求間隔 + 2 次退避
        for (k1, _), (k2, _) in zip(events, events[1:]):
            if k1 == "request" and k2 == "request":
                self.fail("兩個對外請求背靠背、中間沒有延遲")

    def test_401_rearm_then_retry_is_delayed(self):
        events = []
        token_calls = {"n": 0}

        def transport(url, data=None, headers=None, timeout=None, **kwargs):
            # 不自行 append：run_main 的 events wrapper 已記錄每一次請求
            if "token/initialization" in url:
                token_calls["n"] += 1
                if token_calls["n"] == 1:
                    return FakeResponse(401, {})
                return FakeResponse(200, {"access_token": "TOKEN"})
            return FakeResponse(200, self.body)

        arm = mock.Mock(return_value=_session_ctx())
        exit_code = self.run_main(transport, self.argv_single(), events=events, arm=arm)
        self.assertEqual(exit_code, ci.EXIT_OK)
        self.assertEqual(arm.call_count, 1, "401 要重新武裝一次")
        self.assertEqual([kind for kind, _ in events],
                         ["request", "sleep", "request", "sleep", "request"])

    def test_sleep_bounds_are_configurable(self):
        budget = ci.RequestBudget(10, sleep_min=0.5, sleep_max=0.75)
        with mock.patch.object(ci.time, "sleep") as sleep:
            budget.spend()
            budget.pace()   # 第一個請求之前不睡
            sleep.assert_not_called()
            budget.spend()
            budget.pace()
        self.assertEqual(sleep.call_count, 1)
        delay = sleep.call_args[0][0]
        self.assertGreaterEqual(delay, 0.5)
        self.assertLessEqual(delay, 0.75)

    def test_bad_sleep_args_are_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            with mock.patch.object(sys, "argv", ["fetch_ci_fares.py", "--date", "2026-11-20",
                                                 "--sleep-min", "5", "--sleep-max", "1"]):
                ci.main()
        self.assertEqual(ctx.exception.code, ci.EXIT_USAGE)


# ====================================================================== P1-4
class TestDryRunHasNoSideEffects(MainTestBase):
    """P1-4：`--dry-run` 絕不開瀏覽器、不發請求；無快取 → 明確錯誤。"""

    def test_without_cache_errors_and_never_calls_arm_session(self):
        arm = mock.Mock()
        exit_code = self.run_main(
            _ok_transport(self.body),
            ["--date", "2026-11-20", "--dry-run", "--out", str(self.out)],
            session_ctx=None, arm=arm)
        self.assertEqual(exit_code, ci.EXIT_ENV)
        self.assertEqual(arm.call_count, 0, "--dry-run 不可啟動瀏覽器")
        self.assertFalse(self.out.exists())

    def test_with_cache_makes_zero_network_requests(self):
        events = []
        arm = mock.Mock()
        exit_code = self.run_main(
            _ok_transport(self.body),
            ["--date", "2026-11-20", "--dry-run", "--json", "--out", str(self.out)],
            arm=arm, events=events)
        self.assertEqual(exit_code, ci.EXIT_OK)
        self.assertEqual(events, [], "--dry-run 不可發任何 HTTP 請求")
        self.assertEqual(arm.call_count, 0)
        self.assertFalse(self.out.exists())

    def test_dry_run_with_refresh_cookie_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            with mock.patch.object(sys, "argv", ["fetch_ci_fares.py", "--date", "2026-11-20",
                                                 "--dry-run", "--refresh-cookie"]):
                ci.main()
        self.assertEqual(ctx.exception.code, ci.EXIT_USAGE)

    def test_401_rearm_is_not_bound_to_dry_run(self):
        """401 重新武裝條件已與 --dry-run 解耦（碼面驗證；dry-run 本身不發請求）。"""
        source = (REPO_ROOT / "fetch_ci_fares.py").read_text(encoding="utf-8")
        self.assertNotIn("and not args.dry_run", source)
        self.assertIn('result.get("http_status") == 401', source)


# ====================================================================== P1-5
class TestQueryResultSchemaIsUniform(MainTestBase):
    """P1-5：ok / no_data / failed / 中止 的日期物件 key 集合完全一致（缺值 null）。"""

    def _query(self, transport, budget=None):
        with mock.patch.object(ci.time, "sleep"):
            return ci.query_leg(mock.Mock(), _session_ctx(), "TPE-NRT", "TPE", "NRT",
                                date(2026, 11, 20), budget or ci.RequestBudget(20),
                                post=transport)

    def test_query_leg_states_share_identical_key_set(self):
        ok = self._query(FakeTransport([FakeResponse(200, {"access_token": "T"}),
                                        FakeResponse(200, self.body)]))
        no_data = self._query(FakeTransport([FakeResponse(200, {"access_token": "T"}),
                                             FakeResponse(200, {"data": {"airBoundGroups": []}})]))
        failed = self._query(FakeTransport([FakeResponse(500, {})], repeat_last=True))
        parse_error = self._query(self._boom_transport)
        terminal_4xx = self._query(FakeTransport([FakeResponse(422, {})]))
        blocked = self._query(FakeTransport([FakeResponse(403, {})]))
        budget_hit = self._query(FakeTransport([FakeResponse(200, {"access_token": "T"})]),
                                 budget=ci.RequestBudget(1))

        states = {"ok": ok, "no_data": no_data, "failed": failed,
                  "parse_error": parse_error, "failed_422": terminal_4xx,
                  "blocked": blocked, "budget": budget_hit}
        baseline = set(ok)
        self.assertEqual(baseline, set(ci.QUERY_RESULT_KEYS))
        for name, result in states.items():
            with self.subTest(state=name):
                self.assertEqual(set(result), baseline, f"{name} 的 key 集合與 ok 不同")
                for key in ("http_status", "raw_bounds_count", "parse_stats",
                            "evidence", "summary", "abort_reason"):
                    self.assertIn(key, result, f"{name} 缺少 {key}")

        # 缺的值是明確的 null，而不是省略 key
        self.assertIsNone(no_data["summary"])
        self.assertIsNone(parse_error["http_status"])
        self.assertIsNone(failed["raw_bounds_count"])
        self.assertIsNone(blocked["raw_bounds_count"])
        self.assertIsNone(budget_hit["evidence"])
        self.assertEqual(failed["http_status"], 500)
        self.assertIsNotNone(ok["summary"])

    @staticmethod
    def _boom_transport(url, **kwargs):
        raise ValueError("回應看不懂")

    def test_aborted_placeholders_match_schema(self):
        # ① 403 中止 → 第三個日期是 placeholder（schema 與前兩天完全相同）
        transport = FakeTransport([FakeResponse(200, {"access_token": "TOKEN"}),
                                   FakeResponse(200, self.body),
                                   FakeResponse(403, {"url": "captcha"})])
        exit_code = self.run_main(
            transport, self.argv_range("2026-11-20", "2026-11-22"))
        self.assertEqual(exit_code, ci.EXIT_ABORTED)
        queries = json.loads(self.out.read_text(encoding="utf-8"))["queries"]
        baseline = set(queries[0])
        self.assertEqual(baseline, set(ci.QUERY_RESULT_KEYS))
        self.assertEqual(set(queries[1]), baseline, "被攔截日期 schema 不一致")
        self.assertEqual(set(queries[2]), baseline, "403 中止日期 schema 不一致")
        self.assertIn("http_status", queries[2])
        self.assertIn("raw_bounds_count", queries[2])
        self.assertIsNone(queries[2]["http_status"])
        self.assertIsNone(queries[2]["raw_bounds_count"])
        self.assertTrue(queries[2]["aborted"])

        # ② 請求上限中止 → 剩餘日期是 placeholder（先把上一輪輸出清掉，避免被保留舊檔）
        self.out.unlink()
        exit_code = self.run_main(
            _ok_transport(self.body),
            self.argv_range("2026-11-20", "2026-11-22") + ["--max-requests", "2"])
        self.assertEqual(exit_code, ci.EXIT_BUDGET_ABORT)
        queries = json.loads(self.out.read_text(encoding="utf-8"))["queries"]
        for query in queries:
            self.assertEqual(set(query), set(ci.QUERY_RESULT_KEYS))
            for key in ("http_status", "raw_bounds_count", "parse_stats", "evidence"):
                self.assertIn(key, query)
        self.assertTrue(queries[2]["aborted"])
        self.assertEqual(queries[2]["abort_reason"], ci.ABORT_REASON_BUDGET)

    def test_all_four_states_identical_in_one_run(self):
        """一次跑完 ok / no_data / failed / 中止四種狀態，key 集合完全相同。"""
        state = {"n": 0}

        def transport(url, data=None, headers=None, timeout=None, **kwargs):
            if "token/initialization" in url:
                state["token"] = state.get("token", 0) + 1
                if state["token"] == 3:
                    return FakeResponse(403, {"url": "captcha"})   # 中止
                return FakeResponse(200, {"access_token": "TOKEN"})
            state["n"] += 1
            if state["n"] == 1:
                return FakeResponse(200, self.body)                     # ok
            if state["n"] == 2:
                return FakeResponse(200, {"data": {"airBoundGroups": []}})  # no_data
            raise AssertionError("多餘的請求")

        exit_code = self.run_main(
            transport,
            self.argv_range("2026-11-20", "2026-11-23") + ["--max-requests", "8"])
        self.assertEqual(exit_code, ci.EXIT_ABORTED)
        queries = json.loads(self.out.read_text(encoding="utf-8"))["queries"]
        statuses = [q["status"] for q in queries]
        self.assertEqual(statuses, [ci.STATUS_OK, ci.STATUS_NO_DATA,
                                    ci.STATUS_FAILED, ci.STATUS_FAILED])
        self.assertTrue(queries[2]["blocked"])
        self.assertTrue(queries[3]["aborted"])
        key_sets = {frozenset(q) for q in queries}
        self.assertEqual(len(key_sets), 1, "四種狀態的 key 集合必須完全相同")
        self.assertEqual(key_sets.pop(), frozenset(ci.QUERY_RESULT_KEYS))


if __name__ == "__main__":
    unittest.main()
