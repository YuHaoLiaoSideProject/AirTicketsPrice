# 稽核報告：`fetch_ci_fares.py` 的錯誤處理實際行為

> 稽核對象：`fetch_ci_fares.py`（1229 行，commit 當下工作區版本）
> 稽核方式：**靜態逐行比對 + 離線 mock 實測**（`/tmp/ci_err_probe.py`，patch `_post` / `_load_playwright` /
> `load_session`，未對華航站方發出任何查詢請求）
> 稽核範圍：錯誤處理、三態、重試、中止、exit code、覆寫行為；**未修改任何既有檔案**
> 稽核日期：2026-11-02

一句話結論：**三態骨架與 403 立即中止是「真的做到」的，但外圍有一圈未被涵蓋的例外
（瀏覽器啟動／goto 逾時／寫檔／快取解碼）會以 traceback 結束，且失敗的一輪會無條件覆蓋
上一輪成功的 JSON。另有 9 處規格落差、其中 2 處會造成資料語意錯誤（把解析失敗偽裝成
`no_data`）與資料遺失（覆寫）。**

---

## 0. 摘要表（錯誤情境 → 實際行為 → 是否符合規格 → 風險）

| # | 情境 | 程式碼實際行為 | 符合規格？ | 風險 |
|---|---|---|---|---|
| 1 | `ok` / `no_data` / `failed` 三態 | 三態存在；`no_data` 有 `evidence`，不寫 0 元、不寫假陣列 | ✅ 大致符合 | — |
| 2 | 200 但**價格欄位改名**（`total`→`amount`） | `parse_air_bounds` 靜默 `continue`（L465-467）→ `flights=[]` → 標成 **`no_data`** | ❌ **規格說「解析失敗＝failed」** | **高**（把壞掉當查無） |
| 3 | 200 但 `data`/`airBoundGroups` 型別異常（list/str） | `AttributeError` → 落入通用 `except`（L1023）→ 重試 3 次（6 個請求）→ `failed`，`http_status=None` | ⚠️ 不會 crash，但歸類與請求數不符規格 | 中 |
| 4 | 回應是 HTML（被攔成過場頁） | `_json_or_error` L870-878 拋 `RuntimeError` → 通用 except → 重試 3 次（**6 個請求**）→ `failed` | ⚠️ 不 crash；但重試對「非 JSON」無意義且燒預算 | 中 |
| 5 | 403（token 或 air-bounds） | `BlockedError`（L905/L935）→ 立即 `failed`+`blocked`，**只花 1–2 個請求、無 sleep** | ✅ 符合 | — |
| 6 | 429 | 同上，立即停止 | ✅ 符合 | — |
| 7 | 403/429 之後的日期 | 逐筆補成 `status=failed` + 錯誤文字「已中止…」（L1139-1152） | ⚠️ 有標記但**無獨立欄位**、缺 `http_status`/`raw_bounds_count`（schema 不一致）；exit code 仍可能 0（見 §4） | 中 |
| 8 | 401（cookie 失效） | `HttpStatusError(401)` → 不重試（L1013-1018）→ `main` 重新武裝**一次** + 重試**一次**（L1168-1181） | ✅ 骨架符合；但 `--dry-run` 時**完全不重新武裝**（L1169 `and not args.dry_run`），且 ArmError 內容被丟棄 | 低–中 |
| 9 | 5xx / timeout / 連線錯誤 | 重試 2 次，backoff 3s/6s（L971, L1020, L1026） | ✅ 符合 | — |
| 10 | **400 / 404 / 422** 等非攔截 4xx | 同樣重試 3 次（L1010-1022 對所有 `>=400` 一視同仁） | ❌ 規格只說網路錯誤／timeout／5xx 才重試 | 中（燒 budget、惹站方） |
| 11 | `--max-requests` 用完 | 停止**發請求**，但仍逐日迴圈、每日仍 sleep 2–4s、全記 `failed`+`budget_exhausted`，**不會**標「已中止」 | ❌ 規格說「超過即停止」 | 中 |
| 12 | 請求間隔 | 只在「不同日期之間」隨機 2–4s（L1158-1161） | ❌ 同日期內 token→air-bounds 背靠背、重試與重新武裝後也不延遲 | 中 |
| 13 | Playwright 未安裝 | `_load_playwright` L530-540 → `ArmError` 帶安裝指令 → `main` L1124 印訊息回 **exit 1** | ✅ 符合 | — |
| 14 | **chromium 不存在 / launch 失敗** | `launch_persistent_context` 的例外是 Playwright `Error`，**不是 `ArmError`** → 直穿 `main`（只 catch `ArmError`）→ **traceback** | ❌ | **高** |
| 15 | **`page.goto(flights 首頁)` 逾時** | L701 未包 try → `TimeoutError` → **traceback** | ❌ | **高** |
| 16 | 冷啟動表單互動逾時（`box.click` / `option.click` / `triggers.nth().click`） | L721 / L730 / L739 未包 try → **traceback** | ❌ | **高** |
| 17 | 冷啟動整體等待 | `deadline = time.time() + 180`（L764, L767）有上限，**不會無限等**；但等完才在 L796-812 拋 `ArmError` | ✅ 有 timeout | 低 |
| 18 | 冷啟動成功但找不到 `privateFacts` | L796 判斷後拋 `ArmError`（含觀測到的 statuses、403/429/503 提示）→ 優雅 exit 1 | ✅ | — |
| 19 | 表單 POST 200 但無 `privateFacts`（結構改版） | 同上（`private_facts` 為 None → `ArmError`） | ✅ | — |
| 20 | **快取檔不是合法 UTF-8** | `load_session` 只 catch `JSONDecodeError, OSError`（L278）→ **`UnicodeDecodeError` 直穿 → traceback** | ❌ | **中–高** |
| 21 | **快取 `saved_at` 型別異常**（int/list） | L289 `saved_at.replace(...)` → **`AttributeError` → traceback** | ❌ | 中 |
| 22 | 快取 JSON 壞掉 / 過期 / 缺欄位 | 回 `None` → 冷啟動（L278-295） | ✅ | — |
| 23 | **磁碟滿／權限不足（輸出檔）** | L1219-1221 未包 try → **traceback**（實測 `/proc/...` 得 `FileNotFoundError`） | ❌ | 中 |
| 24 | **磁碟滿／權限不足（session 快取）** | `save_session` L301 未包 try → **traceback**（冷啟動成功後才炸，等於白跑） | ❌ | 中 |
| 25 | `--date 2026-13-40` | `date.fromisoformat` L233 → **`ValueError` traceback**（實測 exit 1，但無友善訊息） | ❌（其他參數錯誤是 `SystemExit`，此處不一致） | 低 |
| 26 | 失敗的一輪寫檔 | 無條件覆寫同名 JSON（見 §5）→ **舊的成功資料消失** | ❌ 規格未定義，但風險明確 | **高** |

統計：**規格落差 9 處**（#2, #3/#4 的重試分類、#7, #8 的 dry-run、#10, #11, #12, #25 屬同類「歸類/停止條件」）
—— 嚴格計數為 **9 條明確落差**（§2 逐條列出）。**會 crash（traceback）的路徑 8 條**（#14,#15,#16,#20,#21,#23,#24,#25）。
**失敗會覆蓋舊資料：會**（#26）。

---

## 1. 逐條規格比對（7 條）

### R1 三態嚴格分離 — 部分符合（有 2 個實質落差）

程式碼依據：`query_leg` L944-1035（狀態指派 L976-995）、`build_payload` L1037-1078（`status_legend`、`totals`）。

* `ok`：L982-984，200 + `flights` 非空才給 `ok` + `summary`。✅
* `no_data`：L986-993，保留 `raw_bounds_count` 與 `evidence{airBoundGroups, response_keys}`，不寫 0 元。✅
* `failed`：`error` 一定有值；`http_status` **不保證有值** ❌（落差 G2）。
* **最主要落差（G1）**：`parse_air_bounds` L465-467
  ```python
  total = price.get("total")
  if total is None:
      continue          # 靜默跳過整個 airBound
  ```
  只要站方把 `total` 改名／搬位置（回應結構改版），`flights` 就是空的 → 被 L986 判成 **`no_data`**。
  **實測**：餵 `airBoundGroups: [ … prices.totalPrices: [{amount: 12345}] …]` →
  `status=no_data`、`evidence.airBoundGroups=1`，且 `error=None`。
  這正是規格要求「不可偽裝成有查／不可混用」的那條紅線：結構改版會被誤讀成「這天沒位子」。

### R2 401 → 重新武裝一次 + 重試一次 — 符合骨架，但被 `--dry-run` 打斷

程式碼依據：`fetch_token` L907-908／`fetch_air_bounds` L937-938 → `HttpStatusError(401)`；
`query_leg` L1013-1018（401 不進重試迴圈）；`main` L1168-1181。

* 正常模式：重新武裝 1 次 + 重試 1 次 ✅（實測 `arm_session` 被呼叫 1 次）。
* 重試仍 401 → 不再武裝（`query_leg` 直接回 401）✅ 不會無限迴圈。
* **落差 G3a**：`and not args.dry_run`（L1169）。`--dry-run` 的說明是「只查詢／解析，不寫檔」，
  但實際上它**改變了網路行為**：401 時完全不重新武裝（實測：重新武裝次數 0，結果停在 failed）。
* **落差 G3b**：L1178-1179
  ```python
  except ArmError as exc:
      log(f"❌ 重新武裝失敗：{exc}")
  ```
  只寫 stderr；`result["error"]` 仍是「取得 token 未授權（HTTP 401）」，JSON 裡看不到
  「重新武裝失敗」這個真正原因。
* 另外 `--dry-run` 與 401 的語意耦合，也讓「拿 dry-run 驗證 401 流程」變得不可能。

### R3 403/429 → 立即停止整輪 — 符合（但中止標記的 schema 不一致）

程式碼依據：`BLOCK_STATUSES` L140-141；`fetch_token` L905-906；`fetch_air_bounds` L935-936；
`query_leg` L996-1003（不 sleep、不重試，直接 return）；`main` L1184-1186（`aborted=True`）。

* 實測：403 只花 1 個請求、429 只花 2 個請求，`sleep` 未被呼叫 ✅、`blocked=True` ✅。
* 其餘日期在 L1139-1152 補成 `failed` + 「已中止：先前的查詢被反爬攔截…」✅ 語意有保留。
* **落差 G4**：中止的日期使用 `failed` 且**缺** `http_status` 與 `raw_bounds_count` 欄位
  （實測 keys 只有 9 個，其他 `failed` 有 10 個、`ok` 有 12 個）→ 下游若用固定 schema 解析會 KeyError。
  沒有 `aborted: true` 這種機器可判別的欄位，只能靠比對 error 中文字串。

### R4 網路錯誤／timeout／5xx → 重試 2 次、backoff 3s/6s — 符合；但重試範圍過寬

程式碼依據：`RETRIES=2`、`RETRY_BACKOFF=3`（L127-129）；`timeout=HTTP_TIMEOUT(30)` L858-864；
`HttpStatusError` 分支 L1010-1022；通用分支 L1023-1028。

* 延遲公式 `RETRY_BACKOFF * (attempt + 1)` = 3s、6s ✅（實測 log 為 3s/6s）。
* 總嘗試 3 次（1 + 2）✅。
* **落差 G5**：`if status >= 400` 一律拋 `HttpStatusError`（L909, L939），而 L1010 對**所有**
  非 401 的 `HttpStatusError` 都重試 → **400/404/422 也會重試 3 次**。實測 400：3 個請求、3 次 log。
  規格只授權「網路錯誤／timeout／5xx」重試。
* **落差 G6**：JSON 解析失敗（HTML 攔截頁、`data` 型別異常）走通用 `except`（L1023）也被重試 3 次。
  實測 HTML 回應：**6 個請求**（每次嘗試都會重新取 token + air-bounds）。
  這在反爬情境下反而是「硬闖」，與 R3 的精神衝突，也讓 `--max-requests 30` 只夠跑 5 個壞日期。
* 註：`tests/unit/test_ci_fares.py::test_failed_status_on_5xx` 已把「6 calls」寫成期望值，
  所以這是**已被測試固化的行為**，改動時要同步改測試。

### R5 請求間隔 2–4 秒（隨機） — 部分符合

程式碼依據：L127-128、L1158-1161。

* 不同日期之間：`random.uniform(2.0, 4.0)` ✅。
* **落差 G7**：同一日期內的 `token/initialization` → `search/air-bounds` 兩個請求**背靠背、無延遲**
  （L973-975）。重試（L1021/L1027）、401 重新武裝後的第一次查詢、以及冷啟動後的第一次查詢也都沒有延遲。
  若規格指的是「每個 HTTP 請求之間」，則只實作了「日期之間」。

### R6 `--max-requests` 總上限 — 部分符合（停止語意不符）

程式碼依據：`RequestBudget` L880-896；`_post` L858-864（每次請求前 `spend()`）。

* 上限本身**有效**：實測 `--max-requests 2` 時 `requests_used=2`，第 3 個請求確實沒發出 ✅。
* **落差 G8**：達到上限**不會停止整輪**。實測 3 日 / 上限 2：
  ```
  2026-11-20 ok
  2026-11-21 failed budget_exhausted=True
  2026-11-22 failed budget_exhausted=True
  等待訊息出現次數: 2      ← 還照樣 sleep 兩次
  ```
  每天都還是進 `query_leg`、每天都還是 sleep 2–4s、每天都記 `failed`，且**沒有** `aborted` 標記
  （`main` 只在 `result.get("blocked")` 時設 aborted，L1184）。
* 附註：`--max-requests 0` 或負數不會被驗證；`0` 在 L887 被視為「無上限」（`if self.max_requests`），
  負數則第一個請求就 `BudgetExceeded`。兩者都不在 help 說明中。

### R7 Playwright 可選依賴 — 符合（但只涵蓋 import 失敗）

程式碼依據：`_load_playwright` L530-540。

* import 失敗 → `ArmError`，訊息含 `pip install playwright && playwright install chromium` 與
  `xvfb-run` 提示；`main` L1124-1126 印出並 `return 1`。✅ 實測符合。
* **落差**：只處理「import 失敗」。**chromium 不存在、launch 失敗、goto 逾時、選擇器逾時全部不是
  `ArmError`**，因此沒有被 L1124 接住 → traceback（詳見 §3）。

---

## 2. 例外涵蓋完整性（逐情境）

| 情境 | 會怎麼走 | 會 crash？ |
|---|---|---|
| chromium 不存在 | `_find_chromium` L542-564 找不到 → `exe=None`；`launch_persistent_context` 拋 Playwright `Error`（非 `ArmError`）→ 直穿 `main` | **會（traceback）** |
| playwright 未安裝 | `_load_playwright` → `ArmError` → main 印訊息 exit 1 | 不會 |
| playwright 已安裝但 `sync_playwright()` 本身失敗（缺 driver / 權限） | 非 `ArmError` → 直穿 | **會** |
| profile 目錄損壞（`browser_profile` 半毀） | launch 拋錯 → 直穿 | **會** |
| `profile.mkdir` 失敗（權限） | L608 未包 try → `OSError` | **會** |
| 冷啟動 `goto(www 首頁)` 失敗 | L692-699 有 try，只 log「暖身略過」 | 不會（吞掉） |
| 冷啟動 `goto(flights 首頁)` 失敗／60s 逾時 | L701 未包 try | **會** |
| 冷啟動表單：找不到 input/option、click 逾時 | L721 `box.click()`、L730 `option.click()` 未包 try（僅 `cell.first.click()` 在 `_pick_date` 內有包） | **會** |
| 冷啟動日期選擇器改版 | L737-742 明確 `raise ArmError`（找不到觸發器／點不到日期） | 不會 |
| 冷啟動「現在預訂」找不到 | L747-760 逐顆嘗試，失敗則 `raise ArmError` | 不會 |
| 冷啟動挑戰未清關 | L764-786 等到 180s 上限 → L796 `ArmError`（含 statuses 與反爬提示） | 不會（有上限，非無限等） |
| 冷啟動成功但 `ctx.cookies()` 失敗（瀏覽器已崩） | L784 未包 try → Playwright `Error` | **會** |
| 表單 POST 200 但找不到 `privateFacts` | `captured["private_facts"]` 為 None → L796 `ArmError` | 不會 |
| `air-bounds` 200 但 JSON 結構異常 | `_json_or_error` 擋掉非 object（L876）；`data` 型別異常 → `AttributeError`（L1023 通用 except）→ 重試 3 次 → `failed` | 不會 |
| `air-bounds` 200 但欄位改名 | 靜默跳過（L465-467）→ 標成 `no_data` | 不會，但**語意錯誤** |
| 回應是 HTML | `resp.json()` 拋 `ValueError` → `RuntimeError`（L874）→ 通用 except → 重試 3 次 → `failed` | 不會 |
| 寫輸出檔失敗 | L1219-1221 未包 try | **會** |
| 寫 session 快取失敗 | `save_session` L299-306 未包 try | **會** |
| 快取 JSON 壞掉 | L278 catch → 重新武裝 | 不會 |
| 快取**非 UTF-8** 位元組 | `read_text(encoding="utf-8")` 在 try 內，但 `UnicodeDecodeError` 不是 `JSONDecodeError` 也不是 `OSError` → 直穿 | **會** |
| 快取 `saved_at` 是 int/list | L289 `saved_at.replace` | **會** |
| 快取過期／缺 `cookies`/`x_d_token`/`private_facts` | 回 None → 冷啟動 | 不會 |
| `--date not-a-date` / `2026-13-40` | `date.fromisoformat` L233 → `ValueError` | **會**（traceback，exit 1） |
| 航線不支援／參數互斥 | `SystemExit("❌ …")` L218-243 | 不會（訊息清楚，exit 1） |

**實測證據節錄**（`/tmp/ci_err_probe.py`）：

```
[4] 4b chromium 啟動失敗 -> RuntimeError : Executable doesn't exist ... | ArmError? False
    4c flights 首頁 goto 超時 -> TimeoutError : Page.goto: Timeout 60000ms exceeded | ArmError? False
B) 瀏覽器啟動失敗 -> 未捕捉 RuntimeError : Executable doesn't exist      # main() 內直接爆
[2] 非法 UTF-8 bytes -> 💥 UnicodeDecodeError
    saved_at 是 int  -> 💥 AttributeError: 'int' object has no attribute 'replace'
[5] 寫檔到不可建立目錄 -> 💥 FileNotFoundError
```

---

## 3. 未捕捉例外清單（會以 traceback 結束的路徑）

| # | 位置 | 例外 | 觸發條件 |
|---|---|---|---|
| C1 | L701 `page.goto(FLIGHTS_HOME_URL)` | playwright `TimeoutError` | 首頁 60s 未載入、DNS/網路問題 |
| C2 | L721 `box.click()` / L730 `option.click()` / L739 `triggers.nth(idx).click()` | playwright `TimeoutError` | 版面改版、元素不可點、25s 預設逾時 |
| C3 | L784 `ctx.cookies()` | playwright `Error` | 瀏覽器在等待期間崩潰 |
| C4 | L665 附近 `launch_persistent_context` / L652 `sync_playwright()` | playwright `Error` | chromium 未安裝、driver 缺失、profile 損壞、`--no-sandbox` 被拒 |
| C5 | L608 `profile.mkdir` | `OSError` | `~/.cache` 權限、唯讀 fs |
| C6 | L301 `save_session` → `path.write_text` | `OSError` | 磁碟滿、權限不足、`CI_FARES_CACHE_DIR` 指向不可寫位置 |
| C7 | L1219-1221 輸出檔 `mkdir` / `write_text` | `OSError` | 磁碟滿（**最痛**：跑完整輪才炸，且可能留下半截 JSON） |
| C8 | L277 `load_session` → `path.read_text` | `UnicodeDecodeError` | 快取檔被截斷成非 UTF-8、或內容被別的程式改寫 |
| C9 | L289 `saved_at.replace(...)` | `AttributeError` / `TypeError` | 快取 `saved_at` 為非字串（例如被寫成 epoch int） |
| C10 | L233/236/238 `date.fromisoformat` | `ValueError` | CLI 日期格式錯誤（與其他參數的 `SystemExit` 處理不一致） |

> `main()` 只 catch `ArmError`（L1124）與 `RuntimeError`（L1157，且 `query_leg` 內部已自行吸收，
> 幾乎不會觸發）。因此 C1–C10 全部是「使用者看到 Python traceback」。

---

## 4. exit code 行為（實測）

`return 0 if totals["failed"] == 0 or totals["ok"] or totals["no_data"] else 1`（L1225）
⟺ 只有「`failed>0` 且 `ok==0` 且 `no_data==0`」才回 1。

| 情境 | exit | 實測 |
|---|---|---|
| 全部 `ok` | **0** | ✅ |
| 全部 `no_data` | **0** | ✅ |
| `ok` + `no_data` | **0** | ✅ |
| **部分 `failed`（只要有 1 個 ok 或 no_data）** | **0** | ✅（mixed：1 no_data + 2 failed → 0） |
| **中途 403 中止（前面已有 ok）** | **0** | ✅（abort_403：ok=1, failed=2 → **0**） |
| 403 於第一天（無任何 ok/no_data） | 1 | ✅ |
| 全部 `failed` | **1** | ✅ |
| `--max-requests` 用完但先前有 ok | **0** | ✅ |
| 冷啟動 `ArmError` | 1 | ✅ |
| 任一 C1–C10 crash | 1（Python 預設） | ✅ |
| argparse 參數錯誤 | 2（argparse 預設） | 未實測，標準行為 |

**結論：目前沒有可用的結束碼設計。**
1. **排程／CI 無法分辨「成功」與「被 403 中止」** —— 這是最嚴重的一點。一輪 3 天只成功 1 天就回 0，
   自動化會以為一切正常，錯誤被永久靜音。
2. **`crash` 與「全部失敗」都是 1**，無法從 exit code 區分「程式邏輯壞了」與「站方擋人」。
3. 沒有任何「部分成功」的專用碼（例如 3 = 404/被擋、4 = 設定錯誤 <-- 這類約定完全不存在）。
4. `no_data` 被視為成功（合理），但**解析失敗被誤判成 `no_data`（G1）時也會回 0** → 靜默的資料品質退化。

建議的語意（只是建議，未實作）：`0` 全 ok；`2` 全部 no_data；`3` 部分失敗；`4` 全部失敗；
`5` 被反爬中止；`1` 設定／環境錯誤；`6` 未預期例外。

---

## 5. 輸出檔：原位覆寫，**失敗的一輪會蓋掉上一輪成功的資料**

程式碼依據：`default_out_path` L254-261（檔名只由 route + 日期決定）；`main` L1219-1221
（`out_path.write_text(...)`，無備份、無合併、無時間戳、無原子寫入）。

* 同一日期重跑 → **同一個檔名、直接 truncate 覆寫**。沒有 `--out` 以外的任何版本化機制。
* **實測（資料遺失風險已證實）**：先放一份 `generated_at: "OLD"` 的成功 JSON，再跑一次全 500 失敗：
  ```
  覆寫檢查 -> 舊資料是否還在： False | 現有 generated_at: 2026-11-02T00:00:07.000Z
  ```
  → **舊的成功資料完全消失**，新檔只有 `failed` 記錄。
* 最壞的情境組合：昨天跑出 3 天成功資料 → 今天 cookie 過期 + 站方 403 → 第 1 天後的日期全被標中止 →
  仍然照樣寫檔 → **3 天資料被換成「1 天 ok + 2 天 failed」**，且 exit code 還是 0（§4）。
* 次級風險：`write_text` 非原子。磁碟在寫入中途滿 → 檔案被截斷成殘缺 JSON（連 `json.loads` 都失敗），
  且**原檔已先被 truncate**，無法回復。
* 目前唯一的保護是 `--dry-run`（L1216-1218 跳過寫檔）與 `guard_output_path`（L245-252，只禁止寫 `data/`）。
* 輸出目錄內的既有檔案（`ci_fares/TPE-NRT_2026-11-20*.json`）就是這個命名規則的產物，
  亦即任何人都可能用一次失敗的重跑把它們覆蓋。

---

## 6. `no_data` 與 `failed` 的結構差異 & 人類可讀輸出

### 6.1 JSON 結構（實測 keys）

`no_data`（12 keys）：
```json
{"query_date": "...", "return_leg_date": "...", "route_id": "TPE-NRT",
 "status": "no_data", "status_label": "API 回 200 但無資料",
 "flights": [], "flight_count": 0, "fare_count": 0,
 "error": null, "http_status": 200, "raw_bounds_count": 0,
 "evidence": {"airBoundGroups": 0, "response_keys": ["x"]}}
```

`failed`（重試耗盡，10 keys）：
```json
{"query_date": "...", "return_leg_date": "...", "route_id": "TPE-NRT",
 "status": "failed", "status_label": "請求失敗",
 "flights": [], "flight_count": 0, "fare_count": 0,
 "error": "取得 token 失敗（HTTP 500）", "http_status": null,
 "raw_bounds_count": 0}
```

`failed`（403/429，11 keys）：多 `blocked: true`，`http_status` 為 403/429。
`failed`（額度用完，11 keys）：多 `budget_exhausted: true`，`http_status: null`。
`failed`（中止，**9 keys**）：**沒有** `http_status`、**沒有** `raw_bounds_count`。

差異與問題：
* 判別方式：`status` 三態 + `error` 是否為 null。`no_data` 一定有 `evidence`；`failed` 一定沒有。
* ❌ `no_data.http_status` 固定 200、`failed.http_status` 可能是 `null`（規格要求 failed 帶 http_status）。
* ❌ keys 數量 9/10/11/12 不一致 → 下游需全部用 `.get()`。
* ❌ 沒有 `aborted` / `blocked` / `budget_exhausted` 的統一枚舉；中止只能靠中文字串比對。
* ❌ `evidence` 只存「group 數量 + data 的 key 名稱」，**不存原始 body 片段或 hash**，
  離線無法證明「當時真的回 200 但沒資料」還是「解析器壞了」（正好被 G1 利用）。

### 6.2 stdout / stderr

* 所有進度訊息走 `log() → stderr`（L178-180）；`--json` 的 payload 走 stdout（L1213-1214）。✅ 分離乾淨。
* `ok`：`✅ 2026-11-20 [有價] 4 班 / 20 個票種，最低 TWD 8xxx（CI 100）` + 每班一行明細。
* `no_data`：`⚪ 2026-11-21 [API 回 200 但無資料] airBoundGroups=0`（**不顯示** `evidence.response_keys`）。
* `failed`：`❌ 2026-11-20 [請求失敗] 取得 token 失敗（HTTP 500）`（**不顯示** `http_status` 欄位、不顯示嘗試次數）。
* 中止：`❌ 2026-11-22 [請求失敗] 已中止：先前的查詢被反爬攔截（403/429）…` ——
  在 stderr 上與一般失敗**長得一樣**（同一圖示、同一 label），只能靠句子後半段分辨。
* 收尾：`📊 查詢 3 個日期：有價 1、查無 1、失敗 1（航班 4 班 / 票種 20 筆，HTTP 請求 9 個）`
  然後固定一行 ToS 警告。**沒有**「本輪被反爬中止」的總結警示，也沒有列出哪些日期失敗。
* 重試時：`⚠️  2026-11-20 第 1 次查詢失敗（…），3s 後重試`。

---

## 7. 吞掉錯誤的地方（逐處）

| # | 位置 | 內容 | 影響 |
|---|---|---|---|
| S1 | L465-467 `parse_air_bounds` | `if total is None: continue` | **最嚴重**：解析失敗 → 空結果 → `no_data`（G1） |
| S2 | L551 `_find_chromium` | `except Exception: pass`（取 `executable_path` 失敗） | 可接受（後面還有 fallback），但失敗原因永久遺失 |
| S3 | L670 / L684 `on_request` / `on_response` | `except Exception as exc: captured["errors"].append(...)` | `captured["errors"]` **從未寫入 session 或任何輸出**（比對 L815-838 的 session dict 無此欄位）→ 完全靜默 |
| S4 | L712 consent 點擊 | `except Exception: pass` | 可接受（非必要步驟） |
| S5 | L774 `page.title()` | `except Exception: pass` | 可接受（僅用於提示挑戰頁） |
| S6 | L788-791 `finally: ctx.close()` | `except Exception: pass` | 可接受 |
| S7 | L299-306 `save_session` chmod | `except OSError: pass` | 低（僅權限位設定失敗）；但同函式 L301 的 `write_text` **沒有**保護（C6） |
| S8 | L1178-1179 401 重新武裝失敗 | `except ArmError as exc: log(...)` | `result["error"]` 仍為 401 訊息，JSON 中看不到「重新武裝失敗」→ 診斷資訊丟失 |
| S9 | L692-699 暖身 goto | `except Exception: log("暖身略過")` | 可接受（設計如此） |
| S10 | L973 重試迴圈 | 重試會**重新取 token**，上一個 token 直接丟棄 | 非吞錯，但讓一次失敗耗 2 個請求（見 G6） |
| S11 | L1157 `except RuntimeError` | 註解自承「防禦性；`query_leg` 已自行處理」 | 死碼；若真的觸發會把整輪 `aborted=True`（語意錯：那是「被擋」不是「額度」） |
| S12 | L198-199 `parse_flight_id` 失敗回 `{}` | 上游 `or bound_details.get(...)` | `flight_id` 解析失敗時，多個 bound 會共用同一個空字串 key，被合併成同一班（潛在票價混合） |

---

## 8. 建議修正清單（**只列建議，未動手改**）

依「資料正確性 → 資料遺失 → crash → 行為一致性」排序。

### P0 — 資料正確性／資料遺失

1. **`parse_air_bounds` 要能區分「沒有資料」與「有資料但解析失敗」**：
   回傳 `(flights, parse_stats{saw_bounds, saw_prices, rejected_no_total})`；
   `saw_bounds > 0 且 rejected_no_total > 0 且 flights == 0` → `failed`（`error="回應結構改變：找不到 prices.total"`），
   **不可** 標 `no_data`。並在 `evidence` 加 `raw_body_sha256` + 前 2KB 片段。
2. **寫檔改為「先寫 tmp → `os.replace`」**，並在覆寫前：
   (a) 備份舊檔為 `<name>.<timestamp>.json`（或加 `--no-clobber`／`--history`）；
   (b) 若本輪 `totals.ok == 0`，**預設不覆寫**既有含 `ok` 的檔（除非 `--force`）。
3. **退出碼重新設計**（見 §4 建議）並寫進 docstring：`0` 全成功、`3` 部分失敗、`4` 全失敗、
   `5` 被反爬中止、`1` 環境/設定錯誤、`6` 未預期例外。同時把「中止」獨立成
   `status="aborted"` 或在 query 上加 `"aborted": true`，別再用 `failed` 假裝。

### P1 — crash 路徑

4. `arm_session` 外層包一層 `except Exception as exc: raise ArmError(...) from exc`
   （或在 `main` 加 `except Exception`），把 playwright `Error`/`TimeoutError` 轉成 `ArmError`，
   並在訊息裡附「chromium 是否存在 / 是否為 DISPLAY 問題」的提示。
5. `main` 的 `try/except` 擴大為 `except (ArmError, Exception)`（分開處理、印友善訊息、回專用 exit code），
   不要讓 traceback 成為唯一的使用者體驗。
6. `load_session` 的 `except` 改為 `except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError)`；
   `saved_at` 先 `isinstance(saved_at, str)` 檢查。日誌要區分「壞掉」與「格式不符」。
7. `save_session` 的 `write_text` 包 `try/except OSError` → 回 `False` 並在 `main` log「⚠️ 快取寫入失敗，
   本輪仍會繼續但下次需重新武裝」（不應因此中斷已完成的查詢）。
8. 輸出檔寫入包 `try/except OSError` → log 友善訊息 + 回 exit 7（或至少不要把已跑完的結果丟掉）。
9. `parse_dates` 用 `try: date.fromisoformat(...) except ValueError: raise SystemExit("❌ 日期格式錯誤…")`，
   與其他參數錯誤一致。

### P2 — 行為一致性

10. **401 重新武裝不要綁 `--dry-run`**（L1169 移除該條件）；或在 help 明確寫「dry-run 亦不改網路行為」。
11. **只對 `timeout/連線錯誤/5xx` 重試**：把 4xx（401 例外處理、403/429 中止）直接判 `failed`，
    不再重試（同時省下最多 4 個請求/日期）。
12. **解析失敗不要重試 3 次**（或只重試 1 次）：非 JSON／結構異常屬於「對方回應已到」，重試無意義；
    若仍要重試，`--max-requests` 應按「邏輯查詢」計數而非「HTTP 請求」計數。
13. **`--max-requests` 用完要中止整輪**：`main` 看到 `result.get("budget_exhausted")` 就 `aborted=True`
    並跳過剩餘日期的 sleep。
14. **請求間隔改為「每個 HTTP 請求」**（含 token → air-bounds、重試、重新武裝後），
    或至少把同日期內的兩個請求加上 0.5–1s。
15. `failed` 一律填 `http_status`：在 `HttpStatusError` 重試耗盡時把 `exc.status` 寫入 `result["http_status"]`
    （目前 L1010-1022 只在 401 分支寫）。中止的日期也補 `http_status`/`raw_bounds_count`（可為 null）。
16. stdout/stderr：`failed` 行印出 `http_status` 與「已嘗試 N 次」；中止行改用不同圖示（例如 🛑）；
    總結加一行「⚠️ 本輪被反爬中止，N 個日期未查」。
17. `--max-requests` 驗證：`< 1` 直接 `SystemExit`，避免 `0` 靜默變成無上限。
18. `captured["errors"]`（S3）寫進 session 或輸出，否則 hook 例外永遠沒人知道。

### P3 — 測試補強（現有測試缺口）

19. `tests/unit/test_ci_fares.py` 目前**完全沒有測 `main()`**：建議補 exit code 表（§4 全部情境）、
    覆寫保護、`--dry-run` 401、中止日期的 schema。
20. 補測：非 UTF-8 快取、`saved_at` 型別異常、400 不重試、HTML 回應的重試次數、
    「結構改版 → 不得為 `no_data`」。
21. 把 `test_failed_status_on_5xx` 的期待值（6 calls）標註為「目前行為」而非「規格」，
    以免 P2-12 修正時被誤判為 regression。

---

## 9. 驗證方法與邊界

* 靜態：完整讀過 `fetch_ci_fares.py`（1-1229）與 `tests/unit/test_ci_fares.py` 相關段落。
* 動態：`/tmp/ci_err_probe.py`（僅在 `/tmp`，未進入 repo）
  * patch `F._post` 餵入 200/400/401/403/429/500/504/HTML/list/結構異常 等假回應 → 觀察 `query_leg` 的
    `status`/`http_status`/`keys`/實際請求數（見 §1、§2 引用之實測輸出）。
  * patch `F.load_session` 所需的壞檔（非 UTF-8、`saved_at` int、JSON 壞、過期、list）→ 觀察 crash。
  * patch `F._load_playwright` / `F.arm_session` / `F.load_session` / `F._post` 後呼叫 `F.main()` →
    量測 exit code、覆寫行為、`--dry-run` 對 401 的影響、`--max-requests` 的停止語意。
  * 用 `/proc/...` 當輸出路徑 → 證實寫檔失敗為未捕捉 `FileNotFoundError`。
* **未**對華航站方發出任何票價查詢（`_post` 全程被 mock）。
  揭露：驗證 `--max-requests -1` 的參數驗證時，一次指令 `--dry-run` **意外觸發了冷啟動流程**
  （瀏覽器開啟 `flights.china-airlines.com` 首頁，未送出任何表單查詢，60s 後被 timeout 中止）。
  該次執行未寫入任何輸出檔；`ci_fares/` 與 repo 內檔案時間戳未變（已核對）。此屬驗證過程的操作失誤，
  不影響上述結論（冷啟動路徑的行為另外以 mock 驗證）。
* repo 內僅新增本文件 `docs/research/ci-fares-error-handling.md`；未修改 `fetch_ci_fares.py`、
  `ci_fares/*`、`tests/*`；未執行任何 git 操作。

---

## 10. 給「目前錯誤的請求會怎麼處理？」的三行答案

1. **HTTP 層的錯誤處理是好的**：403/429 立刻停（1–2 個請求、零 sleep）、401 重新武裝一次、5xx 退避 3s/6s 兩次、
   `no_data` 不寫成 0 —— 這部分程式真的照規格做。
2. **但錯誤的「分類」與「外圍」不可靠**：解析失敗會被誤標成 `no_data`（結構改版＝看起來像沒位子）、
   重試耗盡後 `failed` 沒有 `http_status`、400 也被重試、`--max-requests` 用完不停整輪、
   exit code 在「部分失敗／中途被 403 中止」時仍是 0，而瀏覽器啟動／goto 逾時／寫檔／快取非 UTF-8
   共 8 條路徑會直接 traceback。
3. **資料會不見**：輸出檔名固定且 `write_text` 直接覆寫，一輪全失敗或中途被擋的執行會把上一輪成功的
   JSON 蓋掉（已實測），且寫入非原子。

Outcome: OK
