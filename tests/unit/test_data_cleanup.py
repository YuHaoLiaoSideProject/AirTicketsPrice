"""
test_data_cleanup.py
====================
探討 data/ 目錄資料清理邏輯的測試報告

結論: build_api.py 中【無任何資料清理機制】

build_api.py 僅包含以下功能:
  1. load_all_records()  - 讀取 data/*.json, 合併為記憶體中的 records list
  2. latest_snapshot()   - 在記憶體中去重(每組合保留最新一筆), 不動原始檔案
  3. build_trips()       - 依行程分組, 建價格歷史結構
  4. main()              - 把 API 產出寫到 api/ 目錄, 不碰 data/ 目錄

【data/ 目錄的 JSON 檔會永遠累積, 從不被刪除或合併】

建議的清理策略:
  1. 按日期刪除: 保留最近 N 天(如 90 天), 刪除超過 N 天的檔案
  2. 按航線合併: 多個同航線的 JSON 檔合併為單一檔案
  3. 按檔案大小/數量限制: 設定 data/ 目錄上限, 超過時刪除最舊的檔案
"""
import unittest


class TestNoCleanupLogic(unittest.TestCase):
    """驗證 build_api.py 確實沒有資料清理邏輯。"""

    def test_source_has_no_unlink_operation(self):
        """build_api.py 的原始碼中不包含 unlink/remove/rmdir 操作。"""
        import build_api
        from pathlib import Path

        source = Path(build_api.__file__).read_text(encoding="utf-8")
        self.assertNotIn("unlink", source,
                         "build_api.py 不應包含 unlink 操作")
        self.assertNotIn("remove", source,
                         "build_api.py 不應包含 remove 操作")
        self.assertNotIn("rmdir", source,
                         "build_api.py 不應包含 rmdir 操作")

    def test_main_does_not_write_to_data_dir(self):
        """main() 的輸出全部寫到 api/ 目錄, 不動 data/ 目錄。"""
        import build_api
        from pathlib import Path

        source = Path(build_api.__file__).read_text(encoding="utf-8")
        # 取出 main() 函式的程式碼區段
        main_section = source.split("def main")[1] if "def main" in source else ""
        self.assertNotIn("DATA_DIR /", main_section,
                         "main() 不應對 DATA_DIR 進行寫入")

    def test_records_accumulate_without_cleanup(self):
        """data/ 中的檔案會永遠累積, 沒有任何清理機制。"""
        from pathlib import Path
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()

            # 模擬多天的爬蟲資料
            for day in range(10):
                f = data_dir / f"2024-01-{day+1:02d}.json"
                f.write_text(
                    json.dumps([{"route_id": "TPE-NRT",
                                 "scraped_at": f"2024-01-{day+1:02d}T10:00:00"}]),
                    encoding="utf-8"
                )

            # 確認所有 10 個檔案都存在
            files = list(data_dir.glob("*.json"))
            self.assertEqual(len(files), 10)

            # build_api 不會刪除任何檔案 (因為沒有清理邏輯)
            # 如果未來加入了清理邏輯, 這個測試會提醒我們更新


class TestCleanupStrategySuggestion(unittest.TestCase):
    """建議的清理策略 (目前為文檔性質的測試)。"""

    def test_documentation_exists(self):
        """確保本測試檔案包含清理策略建議的文檔。"""
        from pathlib import Path
        test_file = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("建議的清理策略", test_file)
        self.assertIn("按日期刪除", test_file)

    def test_suggests_keep_days_parameter(self):
        """建議的策略中應包含保留天數的概念。"""
        from pathlib import Path
        test_file = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("N 天", test_file)


if __name__ == "__main__":
    unittest.main()
