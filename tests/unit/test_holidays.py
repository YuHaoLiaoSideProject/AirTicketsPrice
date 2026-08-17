#!/usr/bin/env python3
"""農曆過年旺季區間 — 曆法計算單元測試（D1/D2 驗算）

對照：
  docs/tech-decisions/農曆過年旺季-2026-08-15.md（D1 來源 / D2 區間定義）
  https://www.hko.gov.hk/tc/gts/time/calendar/text/files/T2027c.txt（獨立對照）

執行：python -m unittest discover -s tests/unit
"""
import unittest
from datetime import date

import holidays


class TestLunarNewYear(unittest.TestCase):
    """正月初一驗算（對照香港天文台年度文字檔）。"""

    def test_chinese_new_year_2026_2029(self):
        # 天文台 T2026c/T2027c/T2028c/T2029c：正月初一
        expected = {
            2026: date(2026, 2, 17),
            2027: date(2027, 2, 6),   # 與現行 CONFIG.PEAKS 一致
            2028: date(2028, 1, 26),
            2029: date(2029, 2, 13),
        }
        for year, d in expected.items():
            with self.subTest(year=year):
                self.assertEqual(holidays._lunar_new_year(year), d)


class TestPeakRange(unittest.TestCase):
    """過年區間 = [除夕前最後一個週六, 初一後第一個週六]。"""

    def test_2027_matches_legacy_manual_config(self):
        # 原 web/aggregate.js CONFIG.PEAKS 手動設定 2027-01-30 ~ 2027-02-06
        self.assertEqual(holidays.peak_range(2027),
                         (date(2027, 1, 30), date(2027, 2, 6)))

    def test_2026(self):
        # 初一 2/17(二)，除夕 2/16(一) → 前週六 2/14、後週六 2/21
        self.assertEqual(holidays.peak_range(2026),
                         (date(2026, 2, 14), date(2026, 2, 21)))

    def test_2028(self):
        # 初一 1/26(三)，除夕 1/25(二) → 前週六 1/22、後週六 1/29
        self.assertEqual(holidays.peak_range(2028),
                         (date(2028, 1, 22), date(2028, 1, 29)))

    def test_2029(self):
        # 初一 2/13(二)，除夕 2/12(一) → 前週六 2/10、後週六 2/17
        self.assertEqual(holidays.peak_range(2029),
                         (date(2029, 2, 10), date(2029, 2, 17)))

    def test_range_boundaries_are_saturdays(self):
        for year in range(2025, 2032):
            f, t = holidays.peak_range(year)
            self.assertEqual(f.weekday(), 5, f"{year} from 應為週六")
            self.assertEqual(t.weekday(), 5, f"{year} to 應為週六")
            self.assertLessEqual(f, t)


class TestPeaksInRange(unittest.TestCase):
    """視窗交集邏輯。"""

    def test_window_containing_cny(self):
        peaks = holidays.peaks_in_range(date(2026, 8, 15), date(2027, 5, 22))
        self.assertEqual(peaks, [{
            "label": "農曆過年",
            "from": "2027-01-30",
            "to": "2027-02-06",
        }])

    def test_window_without_cny(self):
        peaks = holidays.peaks_in_range(date(2027, 3, 1), date(2027, 12, 31))
        self.assertEqual(peaks, [])

    def test_window_cross_year_catches_both(self):
        # 跨年視窗（2027 底 ~ 2028 初）→ 涵蓋 2028 過年
        peaks = holidays.peaks_in_range(date(2027, 12, 1), date(2028, 2, 28))
        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["from"], "2028-01-22")
        self.assertEqual(peaks[0]["to"], "2028-01-29")

    def test_partial_overlap_included(self):
        # 視窗只涵蓋過年區間尾段（交集即納入）
        peaks = holidays.peaks_in_range(date(2027, 2, 1), date(2027, 4, 1))
        self.assertEqual(peaks, [{
            "label": "農曆過年",
            "from": "2027-01-30",
            "to": "2027-02-06",
        }])

    def test_empty_when_min_gt_max(self):
        self.assertEqual(
            holidays.peaks_in_range(date(2027, 5, 1), date(2027, 1, 1)), [])

    def test_accepts_iso_strings(self):
        # build_api 傳入 outbound_date 字串（'YYYY-MM-DD'）
        peaks = holidays.peaks_in_range("2026-08-15", "2027-05-22")
        self.assertEqual(peaks, [{
            "label": "農曆過年",
            "from": "2027-01-30",
            "to": "2027-02-06",
        }])

    def test_sorted_by_from(self):
        peaks = holidays.peaks_in_range(date(2026, 1, 1), date(2029, 12, 31))
        self.assertEqual([p["from"] for p in peaks], sorted(p["from"] for p in peaks))
        self.assertEqual(len(peaks), 4)  # 2026/2027/2028/2029


if __name__ == "__main__":
    unittest.main()
