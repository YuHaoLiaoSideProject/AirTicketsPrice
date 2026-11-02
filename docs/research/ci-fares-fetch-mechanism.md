# 中華航空（CI）票價抓取機制與延遲策略

> 分析日期：2026-11-01　範圍：唯讀程式碼分析（**未實際呼叫華航 API**）
> 對象：`scripts/probe_ci_coverage.py`（目前唯一會抓華航票價的程式）

## 〇、一句話摘要

**純 HTTP 兩步走**（GET 首頁撈公開 `em-api-key` → POST airtrfx `histogram-distribution`），**有延遲：每組請求間隨機 2–4 秒**，但**只在手動執行**（未接任何 workflow 排程），因為它是探測腳本、且實測票價覆蓋率僅 38%。

---

## 一、請求流程（預設 N=8 ⇒ 共 11 次 HTTP 請求）

| 步 | 動作 | Endpoint | 請求數 |
|---|---|---|---|
| 1 | GET 首頁 HTML，用 regex 從 `__NEXT_DATA__` 內嵌資料撈出 `em-api-key`（64 chars，公開給任何訪客） | `https://flights.china-airlines.com/zh-tw/flights` | **1** |
| 2 | 逐組 POST 查票價（每組一個「週六去」單日 payload，`weekDays:["SAT"]`、1 夜、ECONOMY、TWD） | `https://openair-california.airtrfx.com/airfare-sputnik-service/v3/ci/fares/histogram-distribution` | **N**（預設 8） |
| 3 | 區間對照查詢（一次查整個日期區間，驗證第 2 步的「空值」不是查詢形狀造成） | 同上 | **1** |
| 4 | token 重用測試（同一把 key 再打一次） | 同上 | **1** |

- 認證方式：僅帶 `em-api-key` header（+ `content-type`、`origin`、`referer: https://flights.china-airlines.com/`、Chrome UA、`accept-language`）。**沒有** cookie、**沒有** 瀏覽器、**沒有** JS 執行。
- 首頁正則也會抓一個 JWT（`JWT_RE`）並存進 `Credentials.jwt`，但**後續 API 完全沒用到它**（死程式碼／僅用於輸出「JWT 有/無」）。
- 命名雖是 `ci-graphql-feasibility.md`，實作用的是 airtrfx 的 **REST histogram** endpoint，不是 GraphQL。
- 全部請求共用同一個 `requests.Session()`。

---

## 二、延遲設定表

| 延遲點 | 位置（函式／行號） | 數值 | 出現位置 |
|---|---|---|---|
| 逐組查詢之間 | `main()` 迴圈，`random.uniform(args.sleep_min, args.sleep_max)`（L302–305） | **隨機 2.0–4.0 秒**（`--sleep-min` / `--sleep-max`，預設 2/4）；第 1 組不延遲 | 請求之間 |
| token 重用測試前 | `main()` L371 `time.sleep(random.uniform(...))` | 同上 2.0–4.0 秒 | 請求之間 |
| 區間對照查詢前 | **無延遲**（L337 起，緊接在逐組迴圈之後直接打） | 0 | 請求之間（缺口） |
| 401 → 重取首頁 token 後重試 | `query_histogram()` L161–164 | **無延遲**（立刻重取首頁 + 立刻重打） | token 重取之間 |
| 429 / 403 | `query_histogram()` L154–159 | **無退避**：立即設 `stats["aborted"]` 並回傳失敗，不重試、不遵守 `Retry-After`、不換 IP | — |
| timeout / 連線錯誤 | `query_histogram()` L146–149 | **無延遲、無重試**：直接回 `STATUS_FAILED`（`for attempt in range(2)` 實際只為 401 服務） | — |
| 單次請求 timeout | `--timeout` 預設 30 秒 | 30s | — |

**請求上限**：`--max-requests` 預設 **15**。實際判斷是 `stats["api_requests"] >= args.max_requests - 2`（L306），只發生在逐組迴圈內 → 保留 2 次額度給「區間對照」與「token 重用」。觸發後：印警告、`break` 出逐組迴圈，仍會跑區間對照／token 重用（若未被 `aborted` 阻擋），最後正常輸出摘要。**超限不會自動中止流程，只是少查幾組。**

**Token 快取**：有。`Credentials` 類別（L86–118）同一輪只 GET 首頁一次；僅在 API 回 **401** 時 `load(is_refresh=True)` 重取一次。`home_fetches` / `refreshes` 計數會印在摘要。實測一輪 11 次請求只用 1 次首頁、0 次重取。

**三態分離**（不把錯誤吞成一團）：`有票價` / `API 回 200 但無資料` / `請求失敗`；空結果會保留 `slim_body()` 證據（histogram 長度、`buckets.totalResults`）。

---

## 三、目前有沒有在自動跑？

**沒有 —— 只在手動執行。**
依據：`.github/workflows/` 只有 `weekly-crawl-jp.yml`、`weekly-crawl-other.yml`、`deploy.yml` 三支；grep 全 repo 的 `.yml` 找不到任何 `probe_ci_coverage` 的引用。三支 workflow 執行的 Python 只有 `fetch_prices.py`（`--region jp` / `--region other` / `--route` / `--notify`）、`build_api.py`。`probe_ci_coverage.py` 只出現在 `docs/research/ci-coverage-probe.md` 的用法範例與 `.pi/subsessions/*` 的開發紀錄。

---

## 四、與星宇 `fetch_prices.py` 對照

| 項目 | 華航 `probe_ci_coverage.py` | 星宇 `fetch_prices.py` |
|---|---|---|
| 排程 | ❌ 只手動 | ✅ 每週五 04:00 UTC（日本）/ 10:00 UTC（其他），另有 0–1800s cron jitter |
| 每輪請求量 | **11 次**（1 首頁 + 8 逐組 + 2 驗證） | **440 次**（11 航線 × 40 週），jp workflow 240、other workflow 200 |
| 請求間延遲 | 隨機 **2–4 s**（僅逐組之間） | 隨機 **2–6 s**（每筆 job 之間，`config.REQUEST_DELAY_MIN/MAX`） |
| 重試 | **無**（僅 401 重取 token） | **3 次**，指數退避 `3s, 6s`（`RETRY_BACKOFF * (attempt+1)`） |
| UA | 固定 1 個 Chrome UA | 4 個 UA 池隨機輪替 |
| 429/403 | 立即停止、不繞過 | 無專門處理（靠 `raise_for_status` + 重試） |
| 寫入資料 | 不寫檔（純 stdout/JSON） | 寫 `data/YYYYMMDD.json` |
| 為何規模小 | 目的只是**量覆蓋率**（實測 3/8 = 38%，一次性探測）；且區間查詢已證明 1 次可取代 8 次逐組 | 目的是**每週產生全航線歷史價格**，供前端與降價通知 |

→ 華航這支的請求量約為星宇單一 workflow 的 **1/22**，不是設計上保守，而是「探測用途 + 覆蓋率不足所以不量產」。

---

## 五、風險點清單

| # | 風險 | 嚴重度 | 說明 |
|---|---|---|---|
| 1 | **票價覆蓋率僅 38%**（3/8），資料集稀疏且無時間規律 | 🔴 高 | 即使接排程，缺 6 成日期；且首頁促銷價與 API 不一致（坑 1 成立）。**這是目前不量產的根本原因** |
| 2 | 完全沒有排程，無監測 | 🔴 高 | 站方改版（沒了 `em-api-key`）不會有人知道 |
| 3 | 429/403 無退避、不遵守 `Retry-After` | 🟡 中 | 目前是「直接放棄」的保守策略，安全但脆；一旦被限速整輪全滅 |
| 4 | timeout / 5xx **完全不重試** | 🟡 中 | 單一瞬斷就讓該組變成「請求失敗」，也沒有 backoff |
| 5 | 請求上限只檢查逐組迴圈 | 🟡 中 | 區間對照 + token 重用不受 `--max-requests` 管轄；`api_requests` 也不計首頁 GET → 實際總請求可達 `max-requests + 1` |
| 6 | 區間對照查詢**前無延遲** | 🟢 低 | 逐組最後一筆 → 區間查詢會連打 2 次 |
| 7 | 401 重取 token 前無延遲；重取後若遇 429 不特別處理 | 🟢 低 | 目前未觸發（11 次全 200） |
| 8 | JWT 抓了不用（`JWT_RE` / `Credentials.jwt`） | 🟢 低 | 死程式碼，易誤導後續維護者以為需要 Bearer token |
| 9 | `home_fetches` 未列入 `max_requests` 上限 | 🟢 低 | 同上第 5 點 |

> 註：**沒有**發現「錯誤被靜默吞掉」的問題 —— 三態分離 + `raw_evidence` 保留證據做得比星宇那支好。

---

## 六、依據（讀過的檔案）

- `scripts/probe_ci_coverage.py`（全文 502 行）：L33–50 常數與 endpoint；L86–118 `Credentials` token 快取；L134–188 `query_histogram`（429/403、401、例外處理）；L240–250 參數預設（sleep 2/4、max-requests 15）；L286 首頁取 token；L296–309 逐組迴圈與延遲/cap 判斷；L337–365 區間對照；L368–392 token 重用
- `fetch_prices.py`（L87–115 `query_flights` 重試與 backoff；L528–533 主迴圈延遲）
- `config.py`（L10–28 航線 11 條；L37 `NUM_WEEKS=40`；L68–69 `REQUEST_DELAY_MIN/MAX = 2.0/6.0`；L72–73 `RETRIES=3`、`RETRY_BACKOFF=3`）
- `.github/workflows/weekly-crawl-jp.yml`（L6 `cron: "0 4 * * 5"`；L47–49 cron jitter；L51–58 執行 `fetch_prices.py`）
- `.github/workflows/weekly-crawl-other.yml`（L6 `cron: "0 10 * * 5"`；L46–57 執行 `fetch_prices.py --region other`）
- `.github/workflows/deploy.yml`（只跑 `build_api.py`，無抓取）
- `docs/research/ci-graphql-feasibility.md`（首頁 token 來源、airtrfx endpoint、48h 效期、`booking*` 403 硬牆）
- `docs/research/ci-coverage-probe.md`（實測輸出：11 次請求、覆蓋率 3/8 = 38%、token 重用 1 次首頁 0 次重取）

Outcome: OK
