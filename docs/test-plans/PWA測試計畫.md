# PWA — 測試計畫

> **對應 BDD**：`docs/bdds/PWA.feature`（52 個 Scenario：Happy Path 14（含 1 個 Outline ×4 rows）／Error Handling 14（E1–E14）／Edge Cases 8／Business Rules 16（含 1 個 Outline ×2 rows））
> **操作流程**：`docs/interaction-flows/PWA.md`（情境 P1-A~P2-F、異常表格 E1–E14、邊界與限制、26 項驗收清單）
> **技術決策**：`docs/tech-decisions/PWA-2026-08-15.md`（D1–D8、T1–T13、Spike S1–S4、通知承載格式、manifest 規格）
> **背景規格**：`docs/development/離線功能.md`（既有 SW / IndexedDB 離線能力、node:test / Playwright / Lighthouse 用法）——PWA 開發規格（T6/T13）產出後應補交叉引用
> **測試日期**：2026-08-15（Phase 1 驗證 T5；Phase 2 驗證 T12 全量回歸）

---

## 1. 測試範圍總覽

| 層級 | 範圍 | 工具 | 負責 |
|------|------|------|------|
| 單元測試 | 爬蟲端下降偵測與節流（`fetch_prices.py` 擴充：`detect_drops` / `select_top_drops` / 週頻率守衛 / `build_notify_payload` / `call_notify`，純函式） | Python 3.12 標準庫 `unittest`（零新依賴，requirements 僅 `requests>=2.31`） | 後端 |
| 單元測試 | 推播服務 Worker（`worker/src/index.js`：`GET /vapid-public-key` / `POST /subscribe` / `POST /notify`、token 驗證、payload 格式化、404/410 失效訂閱清理） | Node v22 `node:test`（handler 與純函式分離，KV / push service 以 mock 注入） | 後端 |
| 單元測試 | 前端 PWA 模組（安裝按鈕狀態機、iOS UA／版本判定、訂閱 UI 三態、user gesture 防護、notificationclick 拼接、manifest / index.html / icons 靜態驗證、通知承載格式化） | Node v22 `node:test`（`web/pwa.js` UMD 匯出對齊 `cache.js`；靜態檔案以 `fs` 讀取驗證） | 前端 |
| 整合測試 | 前端→mock worker 訂閱管線、SW push/notificationclick × 離線快取、分頁 focus、子路徑 deep-link、file:// 降級 | Playwright（route interception + CDP `ServiceWorker.dispatchPushEvent` + `set_offline` + `add_init_script`） | 前端 |
| 端對端測試 | 完整 PWA 操作流程（安裝／訂閱／通知接收與點擊／E1–E14／邊界／商業規則／零回歸） | Playwright（python 1.62 + chromium）+ 本機靜態伺服器 | 前端 |
| 手動驗證 | 真實裝置（iOS 加到主畫面與 16.4+ 推播、Android／桌面安裝）、真實 push service、勿擾模式、Lighthouse 實測、maskable safe zone 視覺檢查、文件與 $0 成本檢查 | 手動 + Lighthouse CLI | QA |

> **技術棧說明**：本專案無 `package.json`（純 HTML+JS 單檔方案，JS 模組以 UMD 匯出供 Node `node:test` 雙跑）。PWA 功能新增兩類「後端」可測單元：① 爬蟲端 Python 純函式（drop_last 偵測與節流，沿用 `fetch_prices.py` 零依賴風格，以 stdlib `unittest` 執行）；② Cloudflare Worker（`worker/` 目錄，JS handler 與純函式分離，以 `node:test` 執行，KV 與 push service 呼叫注入 mock）。E2E 一律使用本機可用之 python playwright 1.62 + chromium；push 事件以 CDP `ServiceWorker.dispatchPushEvent` 模擬（本機無真實 push service，符合 D8「mocked push service」驗收）。
>
> **需新增/擴充的測試檔案**（詳細見附錄 B）：
> - `tests/unit/test_pwa_drops.py`（新增，爬蟲 Python 單元，§2 SYS-01~SYS-15）
> - `worker/src/index.test.js`（新增，Worker 單元，§3 HDL-01~HDL-11）
> - `tests/unit/pwa.test.js`（新增，前端 PWA 單元，§4 F-01~F-26）
> - `tests/e2e_pwa.py`（新增，PWA E2E：E2E-01~49 / INT-01~07；mock push service + CDP push 事件 + beforeinstallprompt stub）——既有 `tests/e2e_smoke.py`（69 checks）與 `tests/e2e_offline.py`（105 checks）**不動**（回歸門檻）

---

## 2. 後端/爬蟲單元測試（Python — drop_last 偵測與節流）

> 說明：Phase 2 在 `fetch_prices.py` 爬蟲完成後追加「較上次抓取下降（drop_last）偵測 → 節流選取 → 呼叫 Worker `/notify`」（D3 / T10）。此層邏輯為純函式（資料都在手上），以 Python stdlib `unittest` 測試（`tests/unit/test_pwa_drops.py`，執行 `python -m unittest tests.unit.test_pwa_drops` 或 `python -m unittest discover -s tests/unit`），不引入 pytest 新依賴。基準資料語意：最近一次抓取 = 上一週 `data/*.json` 原始檔（`scraped_at` 小於本次的最大者；**不可用 `api/latest.json`**——notify 步驟執行前 build_api.py 已以本次資料覆寫 latest.json，誤用作基準＝永遠自我比對＝無下降，見開發規格 §1.3 陷阱；D3 / 風險登錄「上次比較基準」）。

### 2.1 下降偵測（detect_drops）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| SYS-01 | 任一航班較上次下降 → 觸發（BDD: P2-B 每週五票價下降時收到單則摘要通知 / BR drop_last） | 上次抓取 TPE-NRT JX 804 = 26,008；本次 = 24,120 | 執行 detect_drops(prev, curr) | 回傳 1 筆 drop：{route, outbound_date, return_date, flight_no, old_price: 26008, new_price: 24120, drop_amount: 1888} |
| SYS-02 | 持平／上漲／僅低於平均 → 不觸發（BDD: @edge-case 非 drop_last 條件一律不觸發通知） | 上次 26,008；本次持平 26,008；另一航班上漲；某航班僅低於全域平均但較上次持平 | 執行 detect_drops(prev, curr) | 回傳空清單 → 不發通知；`drop_last` 為唯一觸發條件（below_avg / new_low / 週摘要皆不觸發） |
| SYS-03 | 多航班下降 → 全部列出（供合併單則）（BDD: P2-B 多個航班下降時合併為單則） | 3 個航班價格皆較上次下降 | 執行 detect_drops(prev, curr) | 回傳 3 筆 drop；由呼叫端合併為單則摘要（不逐航班連發） |
| SYS-14 | 下降計算邊界（補充：邊界） | 舊價為 0／缺價（null）／新舊差額為 0／新價缺失 | 執行 detect_drops | 差額 0 與缺價一律不觸發；0 舊價不誤判為「大降」；不拋例外 |
| SYS-07 | 基準為「最近一次抓取」非絕對價格（BDD: BR 下降比對以最近一次抓取資料為基準） | 上次資料 = 上週 `data/*.json` 原始檔（`scraped_at` 小於本次的最大者；**非 `api/latest.json`**——notify 前已被本次覆寫）；某航班現價高於歷史低點但低於上次抓取 | 決定比較基準並執行 detect_drops | 基準 = 最近一次抓取（上週 `data/` 檔）；只要較基準下降即觸發，不比較絕對價格或其他指標 |
| SYS-06 | 首次爬蟲無基準 → 跳過僅建立基準（BDD: E12 首次無基準資料） | 系統首次執行（無上次基準資料） | 執行 should_notify(prev=None, curr) | 回傳 (False, [])：跳過通知；以本次資料寫入基準（下週起正常觸發） |

### 2.2 節流選取與頻率守衛（select_top_drops / 週頻率）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| SYS-04 | 下降超過 3 條 → 取下降幅度最大 3 條（BDD: E11 下降航班超過 3 條只發 3 條） | detect_drops 回傳 5 筆（下降幅度不同） | 執行 select_top_drops(drops, max_n=3) | 依下降幅度由大到小排序，只回傳前 3 筆；其餘 2 筆不發送 |
| SYS-05 | 下降 ≤3 條 → 全保留（BDD: E11 邊界） | detect_drops 回傳 2～3 筆 | 執行 select_top_drops(drops, max_n=3) | 全部保留，順序依下降幅度遞減 |
| SYS-12 | 空 drops / 幅度相同排序穩定（補充：邊界） | drops 為空清單；或兩筆下降幅度相同 | 執行 select_top_drops | 空清單 → 空；幅度相同 → 依原始順序穩定排序（不 flaky） |
| SYS-08 | 同週已發送 → 不重複發送（BDD: @edge-case 通知頻率與爬蟲同頻維持每週一次） | 本週五已發送過（last_notified 日期 = 本週） | 執行 within_weekly_window(last_notified, now) | 回傳已發送 → 不發送額外通知；跨週後（下週五）恢復可發送 |
| SYS-15 | 同週二次觸發（workflow_dispatch）守衛（補充：並發/重入） | 同週內排程後又被手動觸發爬蟲 | 第二次執行通知流程 | 週頻率守衛阻擋第二次發送，通知頻率維持每週五一次 |

### 2.3 payload 與呼叫（build_notify_payload / call_notify）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| SYS-09 | notify payload 符合承載格式（BDD: BR 通知承載格式） | 選取後 drops（含 route / outbound_date / return_date / flight_no / old_price / new_price） | 執行 build_notify_payload(drops) | 輸出 `{"drops": [...]}`，欄位名與型別完全符合 Tech Decision 通知承載規格；最多 3 筆 |
| SYS-10 | /notify 附 Bearer PUSH_API_TOKEN（BDD: P2-F 系統自動呼叫推播服務附 PUSH_API_TOKEN / BR POST /notify） | repo secret `PUSH_API_TOKEN` 注入環境變數 | 執行 call_notify(payload, token, url) | 以 `Authorization: Bearer <token>` POST `/notify`，body 為 payload |
| SYS-11 | 401 / 網路錯誤 → 失敗標記但不中斷已完成的爬蟲（BDD: E6 notify 401 資料照常提交） | mock 回應 401（token 失效）或連線失敗 | 執行 call_notify | 回傳失敗狀態（不 raise 打斷流程）；爬蟲資料已在呼叫前完成寫入與 commit，流程順序保證資料照常提交 |
| SYS-13 | 既有爬蟲純函式行為不變（BDD: BR 既有爬蟲、data/ 與 api/ 維持原樣，僅追加通知呼叫） | Phase 2 改動後 | 執行既有純函式（gen_trip_dates / build_payload / _error_code 等）回歸案例 | 既有 data/ 與 api/ 產出流程不變；新增邏輯僅為爬蟲完成後的 notify 追加呼叫 |

---

## 3. 推播服務單元測試（Cloudflare Worker — node:test）

> 說明：`worker/src/index.js`（T8）提供三個端點。測試將 handler 與純函式分離：payload 格式化、訂閱資料驗證、token 驗證為純函式；KV 讀寫與 Web Push 呼叫以 mock 注入（對齊 `cache.js` 的注入式設計）。執行 `node --test worker/src/index.test.js`（CommonJS require，與既有 `tests/unit/*.test.js` 相同模式）。

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| HDL-01 | GET /vapid-public-key 回傳公鑰（BDD: BR GET /vapid-public-key） | Worker 已部署，VAPID 公鑰為 env/secret | 請求 GET /vapid-public-key | 回 200 + VAPID 公鑰（base64url，前端 `PushManager.subscribe` 可解析）；無需任何驗證 |
| HDL-02 | POST /subscribe 有效訂閱 → 寫入 KV（BDD: BR POST /subscribe） | body 含有效 PushSubscription（endpoint / keys.p256dh / keys.auth）＋正確 Bearer token | 請求 POST /subscribe | 回 200（T8 合約：成功碼 200）；訂閱以 endpoint 為 key 寫入 KV（僅此一筆寫入，mock KV 驗證） |
| HDL-03 | POST /subscribe 無效資料／達上限／錯 token → 拒絕且不寫入（BDD: BR POST /subscribe「無效資料被拒絕」） | body 缺 endpoint 或 p256dh／auth 格式錯誤／非 JSON；或 KV 訂閱數已達 `MAX_SUBS` 上限（D6 防灌爆）；或 token 缺/錯 | 請求 POST /subscribe | 回 400（invalid subscription / subscription limit reached）／401（unauthorized）；KV 無任何寫入（mock KV 驗證） |
| HDL-04 | POST /notify 有效 token → 對全部訂閱者廣播（BDD: BR POST /notify / P2-F 對全部訂閱者廣播） | KV 有 3 筆有效訂閱；`Authorization: Bearer <正確 token>`；body 為非空 drops payload | 請求 POST /notify | 以 VAPID 私鑰對 3 個 endpoint 各發一次 Web Push（mock fetch 驗證 3 次呼叫）；回 200 `{ok:true, sent:3, failed:0}`；drops 缺失/空 → 400 `drops required`（T8 合約） |
| HDL-05 | POST /notify 與 /subscribe 無效／缺 token → 401（BDD: E6 notify 401 / BR 未附正確 token 被拒絕） | 無 Authorization header 或 token 錯誤 | 請求 POST /notify 或 /subscribe | 回 401；不發送任何 Web Push、不寫入 KV |
| HDL-06 | POST /notify 無訂閱者 → 空廣播回成功（BDD: E7 空訂閱者） | KV 無任何訂閱；token 正確 | 請求 POST /notify | 回 200（空廣播）；0 次 Web Push 呼叫、無錯誤 |
| HDL-07 | POST /notify push service 回 404/410 → 刪除失效訂閱（BDD: E5 訂閱過期自動清理） | 廣播時某訂閱 endpoint 回 404（或 410） | 請求 POST /notify | 該訂閱自動從 KV 刪除（mock KV 驗證）；其餘訂閱正常發送；回 200 |
| HDL-08 | notify payload 格式化 title/body/data.url（BDD: BR 通知承載格式） | drops = TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008） | 執行 formatNotification(drops)（drops 含 1 筆 TPE-NRT） | title =「✈️ 票價下降了！」；body =「TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）」；data.url = `?route=TPE-NRT`（**相對 SW scope 路徑**；sw.js 以 `registration.scope` 拼接後 = `/web/?route=TPE-NRT`，開發規格 §3.2） |
| HDL-09 | 憑證分層：私鑰與訂閱名單只在 Worker 端（BDD: BR 憑證分層） | VAPID 私鑰自 env secret（`VAPID_PRIVATE_KEY`）讀取；訂閱存於 KV | 檢視 handler 邏輯與 /vapid-public-key 回應 | 回應內容僅公鑰（不含私鑰）；私鑰只存在於 Worker secret；訂閱名單只存在於 KV；`/notify` 以 Bearer token 防陌生人灌爆 KV |
| HDL-10 | 訂閱以裝置/瀏覽器為單位（KV key 語意）（BDD: @edge-case 訂閱以瀏覽器/裝置為單位） | 同一裝置重複訂閱（endpoint 相同）vs 不同裝置 | 重複呼叫 POST /subscribe | 相同 endpoint → KV 覆寫（單筆）；不同 endpoint → 各自獨立（無跨裝置同步） |
| HDL-11 | KV 不可用／push service 呼叫失敗 → 500 不誤報成功（補充：依賴失敗） | KV get/put 拋錯 或 Web Push 網路失敗（非 404/410） | 請求 /notify 或 /subscribe | 回 500 且不假裝成功；失效訂閱不誤刪 |

---

## 4. 前端單元測試（web/pwa.js 等 — node:test）

> 說明：安裝按鈕狀態機、iOS 判定、訂閱 UI 三態、user gesture 防護、notificationclick 拼接等邏輯建議抽為 `web/pwa.js`（UMD 匯出對齊 `cache.js`，瀏覽器掛全域、Node `require`）。manifest / index.html / icons 靜態驗證以 `node:test` + `fs` 讀取部署檔逐欄位斷言。執行 `node --test tests/unit/pwa.test.js`。

### 4.1 安裝（Phase 1）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| F-01 | 安裝按鈕只在 beforeinstallprompt 後出現（BDD: P1-A 顯示安裝按鈕 / BR 安裝按鈕只在事件後出現） | 瀏覽器具安裝條件但事件未觸發 | 執行 installState 狀態機（事件前） | 狀態 = idle → 不顯示「安裝 App」按鈕；事件觸發後 → 顯示（F-01b 對應 E2E-38 三段時機） |
| F-02 | deferred prompt 暫存、點擊才呼叫、取消後可再觸發（BDD: P1-A 暫存提示點擊才呼叫原生流程 / P1-A 取消後按鈕保留） | beforeinstallprompt 已觸發（deferred prompt 暫存） | 狀態機走 prompt → cancel → prompt | 點擊按鈕才呼叫 `prompt()`；取消 → 狀態回可再觸發（deferred 保留）；不接受安裝前按鈕不消失 |
| F-03 | display-mode standalone → 隱藏安裝入口（BDD: P1-C 已安裝模式隱藏 / BR 安裝按鈕時機） | `matchMedia('(display-mode: standalone)')` matches | 執行 shouldShowInstall() | 回 false → 安裝按鈕與「加到主畫面」提示皆隱藏；非 standalone 回 true |
| F-04 | iOS UA 判定（BDD: P1-B iOS 依提示加到主畫面 / BR iOS 依 UA 顯示提示） | UA 含 iPhone/iPad（Safari 標記） vs 不含 | 執行 isIOS(ua) | iOS UA → true（顯示「加到主畫面」提示、不顯示安裝按鈕）；其他 UA → false |
| F-21 | iOS 版本判定 16.4+（BDD: P2-D iOS 16.4+ 訂閱 / @edge-case iOS 16.4 以下限制） | UA 版本 16.4／16.3／17.x | 執行 iosVersionAtLeast(ua, 16, 4) | 16.4+ → 可訂閱；<16.4 → 顯示 iOS 推播限制提示、不提供 email 等替代方案 |

### 4.2 訂閱 UI 與流程（Phase 2）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| F-05a | 權限 default＋無訂閱 → 「開啟票價提醒」（BDD: P2-A Outline row 1） | permission=default, subscription=null | 執行 subscriptionUI(permission, sub, { vapidReady: true }) | 顯示「開啟票價提醒」 |
| F-05b | 權限 granted＋無訂閱 → 「開啟票價提醒」（BDD: P2-A Outline row 2） | permission=granted, subscription=null | 執行 subscriptionUI(permission, sub, { vapidReady: true }) | 顯示「開啟票價提醒」 |
| F-05c | 權限 granted＋已訂閱 → 「關閉票價提醒」（BDD: P2-A Outline row 3） | permission=granted, subscription=有效 | 執行 subscriptionUI(permission, sub, { vapidReady: true }) | 顯示「關閉票價提醒」＋狀態「已訂閱」 |
| F-05d | 權限 denied → 拒絕引導（BDD: P2-A Outline row 4 / E1） | permission=denied | 執行 subscriptionUI(permission, sub, { vapidReady: true }) | 顯示拒絕引導「通知已封鎖，請到瀏覽器網站設定中允許通知」；不嘗試彈權限詢問 |
| F-06 | 權限詢問僅在 user gesture 發生（BDD: P2-A 詢問僅在此使用者點擊時發生 / BR 頁面載入不自動彈） | 頁面載入（無點擊）| 執行 init 流程 | 不呼叫 requestPermission；僅在按鈕 click handler 中呼叫（無 gesture 的程式呼叫被防護攔截） |
| F-07 | 同意權限 → 訂閱 → POST /subscribe → 已訂閱（BDD: 同意權限後訂閱成功） | permission=granted；PushManager.subscribe mock 回有效訂閱 | 執行 subscribeFlow() | requestPermission → `PushManager.subscribe({ userVisibleOnly: true, applicationServerKey: <公鑰> })` → POST /subscribe 成功 → 狀態「已訂閱」、按鈕變「關閉票價提醒」 |
| F-08 | 退訂：移除本機訂閱＋通知 Worker 刪除 KV（BDD: P2-C 關閉票價提醒完成退訂） | 已訂閱 | 執行 unsubscribeFlow() | `getSubscription().unsubscribe()` → 呼叫 Worker 刪除 KV 記錄 → 狀態回「未訂閱」、按鈕變「開啟票價提醒」 |
| F-09 | 訂閱失敗 → 可重試提示、頁面不受影響（BDD: E2 訂閱失敗） | POST /subscribe 失敗或 subscribe 拋錯 | 執行 subscribeFlow() 失敗分支 | 狀態顯示「訂閱失敗，請稍後重試」；按鈕可再點重試；訂閱流程不影響圖表/航線/離線功能 |
| F-10 | VAPID 公鑰取得失敗 → 按鈕停用（BDD: E3 公鑰抓取失敗） | GET /vapid-public-key 失敗 | 執行 init（公鑰失敗分支） | 「開啟票價提醒」按鈕停用＋提示「提醒功能暫時不可用」；其餘功能正常；下次載入成功 → 自動恢復可用 |
| F-11 | 權限 denied → 拒絕引導、不重複詢問（BDD: E1） | requestPermission 回 'denied' | 執行 subscribeFlow 失敗分支 | 顯示拒絕引導文案；不再次 requestPermission；使用者到設定允許後重新點按鈕 → 重跑訂閱流程（F-11b） |
| F-12 | 權限詢問被忽略 → 維持未訂閱、無錯誤（BDD: E4） | requestPermission 被關閉（無結果） | 執行 subscribeFlow | 狀態維持「未訂閱」；不顯示任何錯誤提示；再點按鈕 → 重新 requestPermission |
| F-13 | iOS 未安裝 → 提示且不發權限請求（BDD: E8） | iOS UA 且非 standalone | 點「開啟票價提醒」 | 顯示「需加到主畫面後才收得到通知」提示；不呼叫 requestPermission；加到主畫面後再點 → 正常訂閱流程（F-13b） |
| F-29 | macOS Safari 未加到 Dock → 提示且不發權限請求（BDD: E8b，桌機版） | macOS Safari UA 且非 standalone | 點「開啟票價提醒」 | 顯示「需加到 Dock（程式塢）後才收得到通知」提示；不呼叫 requestPermission；加到 Dock 後再點 → 正常訂閱流程（F-29d；修電腦版「通知服務連線失敗」誤導） |

### 4.3 SW 通知處理（Phase 2）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| F-19a | 通知承載格式化（Outline row 1）（BDD: BR 通知承載格式） | drops = [TPE-NRT 東京 8/22–8/30 26008→24120]（1 筆） | 執行 formatNotification(drops) | title「✈️ 票價下降了！」；body「TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）」；data.url = `?route=TPE-NRT`（相對路徑，開發規格 §2.4；解析後 = `/web/?route=TPE-NRT`） |
| F-19b | 通知承載格式化（Outline row 2）（BDD: BR 通知承載格式） | drops = [TPE-KIX 大阪 8/23–8/31 12900→11500]（1 筆） | 執行 formatNotification(drops) | body「TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）」；data.url = `?route=TPE-KIX`（相對路徑；解析後 = `/web/?route=TPE-KIX`） |
| F-14 | notificationclick deep-link 以 SW scope 為基準拼接（BDD: P2-B 點擊開啟航線 / @edge-case 子路徑部署） | `registration.scope = /AirTicketsPrice/web/`；data.url = 相對路徑 `?route=TPE-NRT` | 執行 resolveNotificationUrl(scope, data.url) | 開啟路徑 = `/AirTicketsPrice/web/?route=TPE-NRT`（scope 基準拼接，航線參數正確套用） |
| F-15 | notificationclose 無副作用（BDD: E13 滑掉通知） | 通知關閉事件 | 執行 notificationclose handler | 不開啟頁面、不 focus 分頁、不執行任何動作 |
| F-20 | 訂閱狀態以 getSubscription 為準（BDD: E5 訂閱過期後下次開啟顯示未訂閱 / P2-A Outline） | 頁面載入時 getSubscription 回空（訂閱已過期） | 執行 init 訂閱狀態檢查 | 狀態顯示「未訂閱」；點按鈕可重新訂閱（F-20b：getSubscription 有效 → 「已訂閱」） |

### 4.4 靜態規格驗證（manifest / index.html / icons）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| F-16 | manifest 欄位齊全（BDD: BR manifest 欄位齊全） | 讀取 `web/manifest.webmanifest`（fs） | 執行 schema 驗證 | 具備 name / short_name / start_url（`./`）/ scope（`./`）/ display（standalone）/ icons（192、512、512-maskable）/ theme_color / background_color / lang（zh-Hant）；JSON 可解析 |
| F-17 | index.html 具備 PWA 連結與 iOS meta（BDD: BR index.html） | 讀取 `web/index.html`（fs） | 檢視 head | 含 `<link rel="manifest">`、`apple-touch-icon`（180）、`theme-color`、`mobile-web-app-capable`、`apple-mobile-web-app-capable`、`apple-mobile-web-app-status-bar-style` meta |
| F-18 | icons 存在且用途正確（BDD: BR maskable 圖示 80% safe zone 前置 / manifest icons） | 讀取 `web/icons/`（fs） | 驗證檔案與 manifest 對照 | icon-192.png（192×192）、icon-512.png（512×512）、icon-512-maskable.png（512×512、purpose=maskable）、apple-touch-icon.png（180×180）皆存在（尺寸可由 Pillow 腳本於 CI 驗證；safe zone 視覺驗證屬 MAN-12） |
| F-25 | 前端不包含 VAPID 私鑰（BDD: BR 憑證分層） | 讀取 `web/app.js` / `web/pwa.js` / `web/sw.js`（fs） | 掃描內容 | 不含 VAPID 私鑰字串／`VAPID_PRIVATE_KEY`／硬編碼 secret；公鑰僅由 `/vapid-public-key` 或打包常量提供 |

### 4.5 補充測試（智能補充：並發 / 生命週期 / 依賴失敗）

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| F-22 | 快速連點「開啟票價提醒」→ 單次訂閱流程（補充：並發） | 使用者快速連點按鈕 | 執行 subscribeFlow（重入防護） | 只有第一次點擊觸發 requestPermission/訂閱；後續點擊被忽略直到流程完成（防重入旗標） |
| F-23 | 訂閱流程中頁面中止（補充：生命週期） | subscribe 進行中頁面被關閉/切離線 | 中止流程 | 進行中的 requestPermission/subscribe 被中止，無殘留狀態更新、無未完成 KV 寫入 |
| F-24 | SW 註冊失敗（非 secure context）→ 安裝/訂閱入口降級（BDD: E14 file:// 開啟降級） | `file://` 或非 https（`navigator.serviceWorker` 不可用/註冊失敗） | 執行 init | SW 不註冊；無安裝資格、無推播能力；頁面降級為純記憶體快取瀏覽（既有行為）；以 `http://localhost` 開啟 → 能力恢復（F-24b） |
| F-26 | 訂閱狀態持久化與頁面重載一致性（補充：生命週期） | 已訂閱後 reload | reload 後執行 init | 狀態仍「已訂閱」（以 pushManager.getSubscription 還原）；不重複訂閱、不彈權限詢問 |

---

## 5. 整合測試（Playwright route interception + CDP）

> 使用 Playwright 的 `route` 攔截 `/vapid-public-key`、`/subscribe`、`/notify` 與 `api/**`，`context.set_offline` 控制連線，`add_init_script` 注入 permission / push / install stub，CDP `ServiceWorker.dispatchPushEvent` 觸發 push——驗證「前端 ↔ SW ↔ mock worker ↔ 離線快取」整合行為。

| # | 測試名稱 | Given | When | Then |
|---|---------|-------|------|------|
| INT-01 | SW push → notificationclick → 離線快取繪圖（BDD: P2-E 離線時點擊通知看快取） | 已訂閱＋已快取東京航線；context.set_offline(true) | CDP dispatchPushEvent（data.url = route=TPE-NRT）→ 點擊通知（dispatchNotificationClickEvent） | 開啟/聚焦頁面；以快取資料顯示東京趨勢圖；頁首顯示離線橫幅；訂閱狀態維持「已訂閱」 |
| INT-02 | 訂閱狀態與離線快取彼此獨立（BDD: @edge-case 訂閱與離線快取獨立） | 已訂閱＋已快取；離線 | 開啟頁面（離線） | 快取繪圖＋離線橫幅照常；訂閱狀態仍正確顯示「已訂閱」；離線不使訂閱失效 |
| INT-03 | notificationclick × 分頁 focus × route 參數（BDD: E10 分頁已開啟） | 已開啟票價頁分頁但顯示其他航線 | 點擊通知（route=TPE-NRT） | 聚焦既有分頁並切換到東京航線；分頁數不增加（不重開新分頁） |
| INT-04 | 子路徑部署 deep-link 端到端（BDD: @edge-case 子路徑部署） | 以子路徑（如 `/AirTicketsPrice/web/`）serve 頁面；SW scope 為 `/AirTicketsPrice/web/` | 點擊通知 | 開啟路徑 = `/AirTicketsPrice/web/?route=TPE-NRT`；航線參數正確套用並顯示該航線 |
| INT-05 | 訂閱管線整合：公鑰 → subscribe → KV（BDD: 同意權限後訂閱成功） | route 攔截 `/vapid-public-key`（回測試公鑰）與 `/subscribe`（記錄 body） | 頁面內點「開啟票價提醒」→ 同意權限 | requestPermission 被呼叫（user gesture）；subscribe 以 userVisibleOnly + 公鑰執行；POST /subscribe 收到有效訂閱 body 並回 201；UI 變「已訂閱」 |
| INT-06 | file:// 開啟降級整合（BDD: E14） | 以 `file://` URL 開啟（Playwright 支援） | 開啟頁面 | SW 不註冊（registration 不存在）；無安裝資格、無推播能力；頁面以記憶體快取正常繪圖；改以 `http://localhost` 開啟 → SW 註冊與 PWA 能力恢復 |
| INT-07 | 多則通知 × 點擊一則（BDD: @edge-case 同時多則通知） | 通知中心同時有多則（不同 route） | 點擊其中一則（route=TPE-NRT） | 只開啟該則對應東京航線；其他通知不產生任何連動動作（無多分頁/無航線切換連鎖） |

---

## 6. 端對端測試（Playwright + chromium）

### 6.0 E2E 模擬技術（`tests/e2e_pwa.py` helpers）

> PWA 的瀏覽器原生能力無法在 headless 環境真實觸發，全部以 Playwright 既有能力 mock（D8 驗收：mocked push service 端到端）。模擬對照如下：

| 模擬對象 | Playwright 技術 |
|---------|----------------|
| **mock push service（Worker 三端點）** | ① 本機以 `page.route('**/vapid-public-key', ...)`、`**/subscribe`、`**/notify` 攔截：`/vapid-public-key` 回固定測試公鑰（RFC 8291 測試金鑰）；`/subscribe` 驗證 body 後回 201 並記錄（測試側變數）；`/notify` 依情境回 200／401／空訂閱清單。② 或起第二個本機 http server 實作 mock worker（`tests/mock_worker.py`）更貼近真實網路。token 驗證：mock 端比對 `Authorization: Bearer`（E6 以錯誤 token 模擬 401） |
| **push 事件（收到通知）** | CDP：`session = context.new_cdp_session(page)` → `session.send('ServiceWorker.enable')` → `session.send('ServiceWorker.dispatchPushEvent', { origin, registrationId, data })` 觸發 SW `push` handler（驗證 title/body 顯示）；或 `page.evaluate(() => navigator.serviceWorker.ready.then(r => r.showNotification(...)))` 直接驗證顯示邏輯 |
| **notificationclick / notificationclose** | CDP `ServiceWorker.dispatchNotificationClickEvent`（含 notification data → 驗證 deep-link 開啟/聚焦）；notificationclose 以 `dispatchNotificationCloseEvent` 驗證無副作用 |
| **beforeinstallprompt** | `context.add_init_script` 在頁面掛 listener 前攔截：暴露 `window.__deferredPrompt`（含 stub `prompt()` → `userChoice`），測試以 `page.evaluate` 觸發事件並控制 accept／cancel；Chromium 若能原生判定 installability（`Page.getInstallabilityErrors`）則直接等待真實事件 |
| **Notification.permission / requestPermission** | `add_init_script`：`Object.defineProperty(Notification, 'permission', ...)` 可寫狀態 + `requestPermission()` 依測試參數回 `'granted' / 'denied' / 'default'`（忽略情境模擬 E4：promise 永不 resolve） |
| **PushManager.subscribe / getSubscription** | `add_init_script` 覆寫 `PushManager.prototype.subscribe`（回 stub PushSubscription 或拋錯）與 `getSubscription`（回現有訂閱／空）；訂閱狀態以測試側可變變數跨 page 控制 |
| **display-mode: standalone** | `add_init_script` 覆寫 `matchMedia('(display-mode: standalone)')` → matches（已安裝模式 P1-C）；iOS standalone 併用 iOS UA |
| **iOS UA / 版本** | `browser.new_context(user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) ... Mobile/15E148 Safari/604.1')` 注入 iPhone UA；版本分支以 UA 字串控制（16.4±） |
| **離線** | 沿用 `tests/e2e_offline.py` 技術：`context.set_offline(True)` + cookie `offline=1`（reload 持久）+ `navigator.onLine` 覆寫（INIT_ONLINE）；SW 兜底 reload 由既有 `web/sw.js` 提供 |
| **訂閱狀態持久化** | 同 context 內以 IndexedDB/localStorage（或測試側變數 + init script）預置「已訂閱」狀態，模擬 E5 訂閱過期則預置空 |

### 6.1 Happy Path（P1-A~P2-F）

| # | 測試名稱（來源 BDD） | 操作步驟 | 預期結果 |
|---|---------|---------|---------|
| E2E-01 | 符合安裝條件顯示「安裝 App」按鈕（P1-A @smoke @p0） | 開啟頁面（未 mock beforeinstallprompt）→ 檢視按鈕 → `page.evaluate` 觸發 beforeinstallprompt → 再檢視 | 事件未觸發時不顯示按鈕；觸發後按鈕出現；按鈕點擊才呼叫 `prompt()`（deferred 暫存） |
| E2E-02 | 接受安裝 → 主畫面圖示 + standalone（P1-A @smoke @p0） | mock beforeinstallprompt → 點「安裝 App」→ stub prompt 回 accepted → 觸發 appinstalled → 以 matchMedia standalone 重開 | 原生安裝確認框顯示；接受後安裝完成；manifest icons（192/512/maskable）存在；之後開啟以 standalone 模式（無瀏覽器工具列） |
| E2E-03 | 取消安裝 → 按鈕保留可再觸發（P1-A @p0） | mock beforeinstallprompt → 點按鈕 → stub prompt 回 dismissed | 安裝未完成（無 appinstalled）；「安裝 App」按鈕仍顯示；再點可再次觸發 prompt |
| E2E-04 | iOS Safari 依 UA 顯示「加到主畫面」提示（P1-B @p1） | iPhone UA context 開啟頁面 | 顯示「加到主畫面」逐步提示（分享 → 加到主畫面）；不顯示「安裝 App」按鈕；head 含 apple-touch-icon（180） |
| E2E-05 | 已安裝模式隱藏入口且離線照常（P1-C @smoke @p0 @regression） | matchMedia standalone stub 開啟 → 檢視按鈕/提示 → 建立快取後 set_offline(true) reload | 安裝按鈕與「加到主畫面」提示皆隱藏；離線 reload 以 SW 兜底 + 快取繪圖（既有離線能力不回歸） |
| E2E-06a | 入口依權限/訂閱顯示：default＋無訂閱 → 開啟按鈕（P2-A Outline row 1 @smoke @p0） | permission=default、getSubscription=null → 開啟頁面 | 顯示「開啟票價提醒」；頁面載入不自動彈權限詢問 |
| E2E-06b | 入口：granted＋無訂閱 → 開啟按鈕（P2-A Outline row 2 @smoke @p0） | permission=granted、無訂閱 → 開啟頁面 | 顯示「開啟票價提醒」；不自動彈詢問 |
| E2E-06c | 入口：granted＋已訂閱 → 關閉按鈕（P2-A Outline row 3 @smoke @p0） | permission=granted、getSubscription=有效 → 開啟頁面 | 顯示「關閉票價提醒」＋狀態「已訂閱」；不自動彈詢問 |
| E2E-06d | 入口：denied → 拒絕引導（P2-A Outline row 4 @smoke @p0） | permission=denied → 開啟頁面 | 顯示拒絕引導「通知已封鎖，請到瀏覽器網站設定中允許通知」 |
| E2E-07 | user gesture 才觸發權限詢問（P2-A @smoke @p0） | permission=default → 開啟頁面（統計 requestPermission 呼叫）→ 點「開啟票價提醒」 | 載入時 0 次 requestPermission；點擊後立即觸發權限詢問（僅 user gesture 時） |
| E2E-08 | 同意權限 → 訂閱成功狀態變「已訂閱」（P2-A @smoke @p0） | permission=default → 點按鈕 → requestPermission 回 granted → subscribe 回有效訂閱 → mock /subscribe 201 | 以 VAPID 公鑰建立訂閱（userVisibleOnly）；POST /subscribe 寫入；狀態變「已訂閱」、按鈕變「關閉票價提醒」 |
| E2E-09 | 收到單則摘要通知（P2-B @smoke @p0） | 已訂閱；CDP dispatchPushEvent（drops 2 筆） | 通知中心出現 1 則通知：title「✈️ 票價下降了！」；body 列出下降航班與期間、新舊價格；多航班合併為單則（1 則非 2 則） |
| E2E-10 | 點擊通知開啟對應航線（P2-B @smoke @p0） | dispatchNotificationClickEvent（data.url = ?route=TPE-NRT）→ 檢視開啟的分頁 | 開啟新分頁載入 `/web/?route=TPE-NRT`；頁面顯示東京航線趨勢圖與最新價格 |
| E2E-11 | 關閉票價提醒完成退訂（P2-C @p0） | 已訂閱 → 點「關閉票價提醒」→ mock 退訂端點 | 本機 PushSubscription 移除（unsubscribe）；Worker KV 刪除記錄（mock 收到 `POST /subscribe {endpoint, action:'remove'}` 請求）；狀態回「未訂閱」、按鈕變「開啟票價提醒」；之後 push 不再收到 |
| E2E-12 | iOS 已加到主畫面可訂閱（P2-D @p1） | iPhone UA + matchMedia standalone → 點「開啟票價提醒」→ 同意 → subscribe 成功 | 執行權限詢問並建立訂閱（與一般流程相同）；訂閱成功狀態「已訂閱」 |
| E2E-13 | 離線點通知看快取資料（P2-E @p1 @regression） | 已訂閱＋已快取東京；set_offline(true) → dispatchPushEvent → 點通知 | 頁面以快取資料顯示東京趨勢圖；頁首離線橫幅；訂閱狀態維持「已訂閱」 |
| E2E-14 | 系統自動廣播通知（P2-F @smoke @p0） | 模擬爬蟲端：以正確 Bearer token 呼叫 mock /notify（drops 1 筆） | mock 端收到具 token 的 POST；對全部訂閱者廣播（mock 端驗證各 endpoint 收到 Web Push payload）；使用者零操作即收到通知 |

### 6.2 Error Handling（E1–E14）

| # | 測試名稱（來源 BDD） | 操作步驟 | 預期結果 |
|---|---------|---------|---------|
| E2E-15 | 權限封鎖顯示拒絕引導且可恢復（E1 @error @p0） | permission=denied → 開啟 → 點按鈕 → 切 permission=granted → 再點 | 顯示拒絕引導文案；不重複彈權限詢問；允許通知後重新點「開啟票價提醒」→ 重新執行訂閱流程 |
| E2E-16 | 訂閱失敗可重試（E2 @error @p0） | 同意權限 → mock subscribe 拋錯／/subscribe 回 500 → 檢視 UI → 恢復後再點 | 顯示「訂閱失敗，請稍後重試」；按鈕可再點重試；頁面瀏覽與圖表完全不受影響；重試後訂閱成功 |
| E2E-17 | VAPID 公鑰抓取失敗按鈕停用（E3 @error @p0） | mock /vapid-public-key 回 500 → 開啟頁面 → 檢視 → 改回 200 → reload | 「開啟票價提醒」停用＋「提醒功能暫時不可用」；圖表、航線、離線完全正常；下次載入後按鈕自動恢復可用 |
| E2E-18 | 權限詢問被忽略（E4 @error @p1） | requestPermission stub 永不 resolve（模擬關閉詢問框）→ 點按鈕 → 再點 | 訂閱狀態維持「未訂閱」；無任何錯誤提示；再點「開啟票價提醒」重新彈出權限詢問 |
| E2E-19 | 訂閱過期 404/410 自動清理並可重新訂閱（E5 @error @p1） | 訂閱已失效（stub push service 回 404）→ mock /notify → 下次開啟頁面 → 重新訂閱 | /notify 時失效訂閱被自動刪除（mock KV 驗證）；下次開啟顯示「未訂閱」；點「開啟票價提醒」建立新訂閱並恢復通知 |
| E2E-20 | notify 401 資料照常提交（E6 @error @p1） | 以錯誤 token 呼叫 mock /notify（回 401）；爬蟲資料已於呼叫前寫入 | 通知不發送（0 次 Web Push）；爬蟲資料仍提交（data/ api/ 產出不受影響）；workflow 該步驟標記失敗（CI 層面驗證見 MAN-15） |
| E2E-21 | 空訂閱者空廣播（E7 @error @p2） | mock KV 無訂閱者 → 呼叫 /notify | mock /notify 回 200（空廣播）；0 次 Web Push 發送、無錯誤 |
| E2E-22 | iOS 未安裝提示且不發權限請求（E8 @error @p1） | iPhone UA、非 standalone → 點「開啟票價提醒」 | 顯示「需加到主畫面後才收得到通知」；requestPermission 呼叫數 = 0；改以 standalone 開啟後再點 → 正常訂閱流程 |
| E2E-22b | macOS Safari 未加到 Dock 提示且不發權限請求（E8b @error @p1，F-29） | macOS Safari UA、非 standalone → 點「開啟票價提醒」 | 顯示「需加到 Dock（程式塢）後才收得到通知」；requestPermission 呼叫數 = 0；改以 standalone 開啟後再點 → 正常訂閱流程（E2E-12b） |
| E2E-12b | macOS Safari 已加到 Dock 可訂閱（F-29d @p1） | macOS Safari UA + standalone → 點「開啟票價提醒」→ 同意 → subscribe 成功 | 執行權限詢問並建立訂閱（與一般流程相同）；訂閱成功狀態「已訂閱」 |
| E2E-23 | 離線點通知目標航線未快取（E9 @error @p1） | 已訂閱但未快取大阪；set_offline(true) → 點通知（route=TPE-KIX） | 頁面顯示「此航線尚未下載，需連網」提示；停留原航線；不白屏、不跳出錯誤卡 |
| E2E-24 | 分頁已開啟聚焦既有分頁（E10 @error @p1） | 已有分頁開啟但顯示其他航線 → 點通知（route=TPE-NRT） | 聚焦既有分頁並切換到東京航線；分頁總數不增加 |
| E2E-25 | 下降超過 3 條只發 3 條（E11 @error @p0） | dispatchPushEvent（drops 5 筆，幅度不同） | 通知只含下降幅度最大的 3 條（body 3 行）；其餘 2 條不發送 |
| E2E-26 | 首次無基準跳過通知（E12 @error @p1） | 模擬爬蟲端首次執行（無基準）→ 檢查 /notify 呼叫 | /notify 呼叫數 = 0（跳過通知）；以本次資料建立基準；下次觸發正常 |
| E2E-27 | 滑掉通知無動作（E13 @error @p1） | dispatchNotificationCloseEvent | 通知關閉；不開啟頁面、不 focus、不執行任何動作 |
| E2E-28 | file:// 開啟降級（E14 @error @p2） | 以 file:// URL 開啟 → 檢視 → 改 http://localhost 開啟 | SW 不註冊、無安裝資格、無推播能力；頁面降級為純記憶體快取（既有行為）；http://localhost 開啟後 SW 註冊與 PWA 能力恢復 |

### 6.3 Edge Cases

| # | 測試名稱（來源 BDD） | 操作步驟 | 預期結果 |
|---|---------|---------|---------|
| E2E-29 | 非 drop_last 一律不觸發（@edge @p0） | 爬蟲端模擬：票價持平／上漲／僅低於平均 → 呼叫 /notify 檢查 | /notify 呼叫數 = 0；不發送任何通知；drop_last 為唯一觸發條件 |
| E2E-30 | 通知頻率維持每週一次（@edge @p1） | 本週已通知狀態 → 同週再次觸發檢查 | 不發送額外通知（/notify 或廣播次數不增加）；下週五恢復 |
| E2E-31 | 子路徑部署 deep-link 正確（@edge @p1） | 以子路徑 serve 頁面 → 點通知 | 開啟路徑以 SW scope 為基準拼接（如 `/AirTicketsPrice/web/?route=TPE-NRT`）；航線參數正確套用 |
| E2E-32 | 訂閱以瀏覽器為單位（@edge @p1） | context A 訂閱成功 → context B（獨立 storage）開啟 → 回 context A | 瀏覽器 B 顯示「未訂閱」；瀏覽器 A 的訂閱不受影響 |
| E2E-33 | 訂閱狀態與離線快取獨立（@edge @p1） | 已訂閱＋快取東京 → set_offline(true) 開啟 | 快取繪圖＋離線橫幅；訂閱狀態仍「已訂閱」 |
| E2E-34 | iOS 16.4 以下限制提示（@edge @p2） | iPhone UA 版本 15.x／16.3 → 點「開啟票價提醒」 | 誠實提示 iOS 推播限制（需加到主畫面且 16.4+）；不提供 email 等替代方案 |
| E2E-35 | 多則通知點擊一則只開該則（@edge @p2） | 通知中心同時多則 → 點其中一則（route=TPE-NRT） | 只開啟該則東京航線；其他通知無連動動作 |

### 6.4 Business Rules

| # | 測試名稱（來源 BDD） | 操作步驟 | 預期結果 |
|---|---------|---------|---------|
| E2E-36 | manifest 生效且 SW 控制頁面（@business @p0） | 開啟頁面 → 檢視 manifest link 解析結果 → 檢視 SW registration | manifest 可解析且欄位齊全（name/short_name/start_url/scope/display/icons/theme_color/background_color/lang）；SW 控制頁面（navigator.serviceWorker.controller 存在）——Lighthouse「Installable」與「離線 reload」稽核屬 MAN-11 |
| E2E-37 | index.html 具備 PWA 連結與 iOS meta（@business @p0） | 檢視頁面 head DOM | 具備 rel="manifest" 連結、apple-touch-icon（180）、theme-color、mobile-web-app-capable、apple-mobile-web-app-capable、apple-mobile-web-app-status-bar-style |
| E2E-38 | 安裝按鈕三段時機（@business @p0） | ① 未觸發事件開啟 ② mock 觸發 beforeinstallprompt ③ standalone stub 開啟 | ① 不顯示按鈕；② 按鈕出現；③ 已安裝（standalone）模式不顯示 |
| E2E-39 | iOS 依 UA 顯示提示且圖示為 apple-touch-icon（@business @p1） | iPhone UA 開啟頁面 | 依 UA 顯示「加到主畫面」逐步提示；不顯示「安裝 App」按鈕；head 圖示為 apple-touch-icon（180） |
| E2E-40 | 下降比對以最近一次抓取為基準（@business @p0） | 模擬兩次資料（上週 vs 本週）：① 持平 ② 上漲 ③ 下降 | 僅 ③ 觸發 /notify；基準為「較上次抓取」（drop_last），非絕對價格或其他指標 |
| E2E-41 | GET /vapid-public-key 前端取得公鑰（@business @p1） | 開啟頁面 → 檢視前端對 /vapid-public-key 的請求與 subscribe 使用 | 前端成功取得 VAPID 公鑰並用於 `PushManager.subscribe(applicationServerKey)` |
| E2E-42 | POST /subscribe 驗證後寫入 KV（@business @p1） | 訂閱流程 → 檢視 mock /subscribe 收到 body | 有效訂閱被驗證並寫入（mock KV 驗證）；無效資料被拒絕不寫入（注入缺 p256dh 的 body → 400） |
| E2E-43 | POST /notify token 驗證＋廣播＋清理（@business @p0） | ① 正確 token → 廣播 ② 錯誤 token → 401 ③ 含 404 訂閱 → 清理 | ① 對全部訂閱者發送 Web Push；② 401 拒絕、不發送；③ 404/410 失效訂閱自動清理 |
| E2E-44a | 通知承載格式（Outline row 1）（@business @p0） | dispatchPushEvent（TPE-NRT 東京 8/22–8/30 26008→24120） | title「✈️ 票價下降了！」；body「TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）」；點擊 data.url = `/web/?route=TPE-NRT` |
| E2E-44b | 通知承載格式（Outline row 2）（@business @p0） | dispatchPushEvent（TPE-KIX 大阪 8/23–8/31 12900→11500） | body「TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）」；data.url = `/web/?route=TPE-KIX` |
| E2E-45 | 憑證分層（@business @p1） | 檢視 /vapid-public-key 回應 → 檢視前端程式碼/請求 | 公鑰公開可用；回應不含私鑰；私鑰與訂閱名單只在推播服務端（secret/KV）；爬蟲→Worker 以 Bearer token 保護 |
| E2E-46 | 既有測試與 Lighthouse 全量零回歸（@business @p0 @regression） | 執行 `node --test tests/unit/` ＋ `python tests/e2e_smoke.py` ＋ `python tests/e2e_offline.py` ＋ Lighthouse 複測 | 單元測試全綠；e2e_smoke 69 checks 全綠；e2e_offline 105 checks 全綠；Lighthouse 無 best-practices 回歸 |
| E2E-47 | 既有爬蟲維持原樣僅追加通知（@business @p1 @regression） | 檢查 workflow 內容與爬蟲產出 | data/ 與 api/ 產出流程不變；既有 GitHub Actions 爬蟲步驟維持原樣；僅在爬蟲完成後追加呼叫 /notify |
| E2E-48 | mock push service 訂閱/push/點擊全流程（@business @p1） | 完整流程：訂閱成功 → dispatchPushEvent → notificationclick → 航線開啟 | 訂閱流程通過；push 事件（收到通知）通過；notificationclick deep-link 開啟航線通過（對應 D8 驗收） |
| E2E-49 | 公開免登入即可安裝與訂閱（@business @p2） | 全新 context（無任何登入狀態）→ 安裝按鈕 → 訂閱流程 | 免登入即可瀏覽、安裝與訂閱；無訂閱者管理後台（訂閱由前端按鈕管理）；$0 成本檢查屬 MAN-13 |

---

## 7. 手動驗證（真實環境）

| # | 情境 | 驗證步驟 | 預期 |
|---|------|---------|------|
| MAN-01 | 真實安裝流程（P1-A，BDD: 接受安裝後主畫面出現圖示） | 真實 Chrome/Edge 桌面或 Android Chrome 開啟 GitHub Pages → 點「安裝 App」→ 接受 → 從主畫面開啟 | 主畫面出現「票價趨勢」圖示；原生安裝確認框流程正確；standalone 開啟無瀏覽器工具列 |
| MAN-02 | 真實 iOS 加到主畫面（P1-B / P2-D，BDD: iOS 依提示加到主畫面） | iPhone Safari 開啟 → 依「加到主畫面」提示操作（分享 → 加到主畫面）→ 從主畫面開啟 | 主畫面圖示使用 apple-touch-icon（180）；standalone 開啟；訂閱與每週五通知正常（16.4+） |
| MAN-03 | 真實推播通知接收與點擊（P2-B，BDD: 每週五收到通知 / 點擊開啟航線） | 真實裝置訂閱 → 觸發一次真實 /notify（或等每週五）→ 通知中心檢視 → 點擊 | 收到「✈️ 票價下降了！」單則摘要；點擊開啟對應航線趨勢頁（含離線時看快取） |
| MAN-04 | iOS 16.4+ 真實訂閱與接收（P2-D，BDD: iOS 已加到主畫面可訂閱） | iPhone 16.4+ 加到主畫面 → 訂閱 → 等待週五下降通知 | 訂閱成功、狀態「已訂閱」；每週五收到通知（APNs 送達） |
| MAN-05 | 瀏覽器設定封鎖/允許通知（E1，BDD: 權限被封鎖顯示拒絕引導） | 瀏覽器網站設定封鎖通知 → 開啟頁面 → 設定允許 → 回頁面重新點 | 封鎖時顯示拒絕引導且不重複詢問；允許後重新訂閱流程正常 |
| MAN-06 | iOS 未安裝 Safari 訂閱提示（E8，BDD: iOS 未加到主畫面） | iPhone Safari（未加到主畫面）點「開啟票價提醒」 | 顯示「需加到主畫面後才收得到通知」；不彈權限詢問 |
| MAN-07 | file:// 真實開啟（E14，BDD: file:// 本機開啟降級） | 以 file:// 直接開啟本機 HTML | SW 不註冊、無安裝資格、無推播；頁面以記憶體快取正常瀏覽；http://localhost 開啟恢復 |
| MAN-08 | 舊 iOS 版本限制（@edge-case，BDD: iOS 16.4 以下收不到推播） | iOS 16.4 以下實機（或 iOS Simulator 舊版本）開啟並嘗試訂閱 | 誠實提示 iOS 推播限制（需加到主畫面且 16.4+）；無 email 等替代方案 |
| MAN-09 | 裝置勿擾/靜音通知可達性（@edge-case，BDD: 裝置通知設定影響可達性） | 裝置開啟勿擾/靜音 → 觸發通知 | 通知可達性依裝置設定而定（可能不顯示）；此為裝置層級行為，頁面與訂閱狀態不受影響（自動化無法驗證，文件註記） |
| MAN-10 | 多則通知真實點擊（@edge-case，BDD: 同時多則通知） | 通知中心同時多則 → 點擊其中一則 | 只開啟該則對應航線；其他通知無連動 |
| MAN-11 | Lighthouse 實測（@business @p0，BDD: manifest 欄位齊全且 Lighthouse 稽核通過） | **手動驗證項**：Lighthouse 13 起官方已移除 PWA 安裝稽核（installable-manifest / service-worker / works-offline 不再存在於 audit set），自動化改以 **CDP `Page.getAppManifest` + `Page.getInstallabilityErrors`** 驗證（`tests/e2e_pwa.py` 內建，errors 為空 = 可安裝，Phase 1 已通過）；Lighthouse 保留跑 performance / best-practices 確認無回歸（`npx lighthouse http://localhost:8000/web/`） | 「Installable」以 CDP installability errors=空替代（manifest 完整 + SW 控制 + maskable）；「離線 reload」以既有 e2e_offline 情境 D + e2e_pwa 離線 reload check 替代；best-practices 無回歸 |
| MAN-12 | maskable 80% safe zone（@business @p1，BDD: maskable 圖示主體落在 80% safe zone 內） | 以圖像工具（Pillow 腳本或 Figma 檢視）將 maskable 圖示裁切為圓形/安全區檢視 | 圖示主體落在 80% safe zone 內，不因 maskable 裁切而缺角 |
| MAN-13 | $0 成本與免費 tier 用量（@business @p2，BDD: 公開免登入、少數親友規模且維持 $0 成本） | 檢視 GitHub Pages / Cloudflare 用量儀表板（每月） | 基礎設施維持 $0（GitHub Pages + Cloudflare Workers 免費 tier；100k req/day、KV 1GB 遠超需求）；無訂閱者管理後台 |
| MAN-14 | README 與文件完整性（@business @p2，BDD: README 與文件完整說明） | 檢視 README 與 docs/ | 包含「安裝 App」操作說明；推播訂閱說明、`PUSH_API_TOKEN` secret 設定與 Worker 部署步驟；明列 iOS 限制（需加到主畫面且 16.4+） |
| MAN-15 | GitHub Actions 真實 notify 失敗標記（E6，BDD: notify 401 資料照常提交） | 於 GitHub Actions 以錯誤 `PUSH_API_TOKEN` 觸發一次 workflow（或等待週五） | 爬蟲資料正常 commit（data/ api/ 更新）；notify 步驟標記失敗；輪換 token 後下週恢復 |

---

## 8. 測試環境

| 項目 | 需求 |
|------|------|
| Node.js 版本 | v22.23.1（單元測試 `node:test`，執行 `node --test tests/unit/pwa.test.js` 與 `node --test worker/src/index.test.js`） |
| Python / 爬蟲測試 | Python 3.12 + `requests>=2.31`；單元以標準庫 `unittest`（`python -m unittest discover -s tests/unit`） |
| Python / Playwright | Python 3 + playwright 1.62（E2E 驅動） |
| 瀏覽器（自動化） | Chromium 149（`/usr/bin/chromium`，透過 Playwright 啟動）；CDP `ServiceWorker.dispatchPushEvent` 模擬 push |
| 瀏覽器（手動驗證） | Chrome / Edge（桌面安裝流程）、Android Chrome、iOS Safari 16.4+／<16.4（加到主畫面、APNs 推播）；Safari / Firefox 回歸 |
| 測試 OS | Linux（開發機）；手動驗證另測 macOS / Windows / iOS / Android |
| 靜態伺服器 | `python3 -m http.server`（repo 根，模擬 GitHub Pages）；子路徑部署以 `/AirTicketsPrice/web/` 路徑 serve（S2） |
| 推播服務（自動化） | mock push service：Playwright `route` 攔截 `/vapid-public-key`、`/subscribe`、`/notify`（或 `tests/mock_worker.py` 本機實作）；CDP dispatch 觸發 SW push 事件 |
| 推播服務（手動） | Cloudflare Workers 正式部署（`wrangler deploy`）；真實 push service = FCM / APNs / Mozilla autopush |
| 離線模擬 | Playwright `context.set_offline(true/false)`；cookie `offline=1`（reload 持久）+ `navigator.onLine` 覆寫（沿用 `tests/e2e_offline.py` INIT_ONLINE） |
| 安裝資格模擬 | `beforeinstallprompt` stub（add_init_script 暴露 deferred prompt）；`matchMedia('(display-mode: standalone)')` stub；iOS UA 注入 |
| 稽核 | Lighthouse（`npx lighthouse <url>`）：installable / offline / best-practices（Phase 1 與 Phase 2 結束各跑一次；不追求特定工具版本分數，D8） |
| 測試資料 | 真實快照：`api/index.json` + `api/trips/*.json`（既有）；下降偵測以 mock 上週/本週兩份 fixture；通知承載以 Tech Decision 規格 fixture |
| 部署環境 | GitHub Pages（HTTPS，天然滿足 SW/manifest 條件）；`file://` 僅開發驗證降級 |

---

## 9. 缺陷追蹤模板

| 欄位 | 說明 |
|------|------|
| ID | BUG-PWA-XXX（PWA = 本功能） |
| 測試案例 | 對應以上測試編號（SYS-xx / HDL-xx / F-xx / INT-xx / E2E-xx / MAN-xx） |
| 嚴重程度 | P0（阻擋，例如安裝流程壞、訂閱完全失效、通知不發、既有 69/105 checks 回歸）/ P1（主要，例如 404/410 清理失敗、節流選取錯誤、deep-link 錯航線）/ P2（次要，例如文案、icon 尺寸、meta 順序） |
| 重啟步驟 | 逐步操作（含 viewport、UA、mock 注入條件、offline/CDP 時序） |
| 預期 vs 實際 | 對照 BDD Then 步驟 |
| 環境 | OS / Browser / 版本 / 資料快照日期 / 連線狀態 / Worker 部署狀態 |

---

## 附錄 A：BDD 覆蓋對照矩陣

> 52/52 Scenario 全數對應（含 2 個 Scenario Outline 共 6 rows 展開為獨立案例）。測試層級對應：@smoke/@happy-path/@p0 → E2E + 前端單元（F-）；@error-handling → 依邏輯所在層（爬蟲/Worker → SYS-/HDL-，瀏覽器 UX → F-）＋ E2E；@edge-case → E2E + 手動（MAN-，自動化不可達者註記）；@business-rules → 單元（F-/SYS-/HDL-）+ E2E + MAN。補充測試（SYS-12/14/15、HDL-11、F-22~26）為智能補充（邊界/並發/依賴失敗/生命週期），不取代任何 BDD 對應。

| # | BDD Scenario | Tags / 嚴重度 | 對應測試案例 |
|---|--------------|--------------|--------------|
| 1 | 符合安裝條件的瀏覽器顯示「安裝 App」按鈕（P1-A） | @smoke @happy-path @p0 | F-01, F-02, E2E-01, E2E-38 |
| 2 | 接受安裝後主畫面出現 App 圖示並以 standalone 開啟（P1-A） | @smoke @happy-path @p0 | E2E-02, MAN-01 |
| 3 | 取消安裝確認後按鈕保留可再次觸發（P1-A） | @happy-path @p0 | F-02, E2E-03 |
| 4 | iOS Safari 依提示「加到主畫面」後以 standalone 開啟（P1-B） | @happy-path @p1 | F-04, E2E-04, MAN-02 |
| 5 | 已安裝模式隱藏安裝入口且離線能力照常（P1-C） | @smoke @happy-path @p0 @regression | F-03, E2E-05 |
| 6 | 頁面依權限與訂閱狀態顯示對應的提醒入口（P2-A，Outline ×4） | @smoke @happy-path @p0 | F-05a~d, E2E-06a~d |
| 7 | 點「開啟票價提醒」於 user gesture 下觸發權限詢問（P2-A） | @smoke @happy-path @p0 | F-06, E2E-07 |
| 8 | 同意權限後訂閱成功狀態變「已訂閱」（P2-A） | @smoke @happy-path @p0 | F-07, INT-05, E2E-08 |
| 9 | 每週五票價下降時收到單則摘要通知（P2-B） | @smoke @happy-path @p0 | SYS-01, SYS-03, F-19, E2E-09 |
| 10 | 點擊通知開啟對應航線頁面（P2-B） | @smoke @happy-path @p0 | F-14, E2E-10, MAN-03 |
| 11 | 關閉票價提醒完成退訂且不再收到通知（P2-C） | @happy-path @p0 | F-08, E2E-11 |
| 12 | iOS 已加到主畫面的 PWA 可正常訂閱（P2-D） | @happy-path @p1 | F-21, E2E-12, MAN-02, MAN-04 |
| 13 | 離線時點擊通知仍可開啟頁面看快取資料（P2-E） | @happy-path @p1 @regression | INT-01, INT-02, E2E-13 |
| 14 | 系統每週五自動偵測下降並廣播通知，使用者零操作（P2-F） | @smoke @happy-path @p0 | SYS-10, HDL-04, E2E-14, E2E-48 |
| 15 | 權限被封鎖時顯示拒絕引導且不重複詢問（E1） | @error-handling @p0 | F-11, E2E-15, MAN-05 |
| 16 | 訂閱失敗顯示可重試提示且不影響頁面瀏覽（E2） | @error-handling @p0 | F-09, E2E-16 |
| 17 | 取得 VAPID 公鑰失敗時停用訂閱按鈕（E3） | @error-handling @p0 | F-10, E2E-17 |
| 18 | 權限詢問被忽略時維持未訂閱且無錯誤提示（E4） | @error-handling @p1 | F-12, E2E-18 |
| 19 | 訂閱過期（push service 回 404/410）時自動清理並可重新訂閱（E5） | @error-handling @p1 | HDL-07, F-20, E2E-19 |
| 20 | 通知發送授權失敗（401）不影響使用者且資料照常提交（E6） | @error-handling @p1 | HDL-05, SYS-11, E2E-20, MAN-15 |
| 21 | 推播服務沒有訂閱者時空廣播回成功（E7） | @error-handling @p2 | HDL-06, E2E-21 |
| 22 | iOS 未加到主畫面時提示且不發無效權限請求（E8） | @error-handling @p1 | F-13, E2E-22, MAN-06 |
| 23 | 離線點通知且目標航線未快取時顯示提示並停留原航線（E9） | @error-handling @p1 | INT-01, E2E-23 |
| 24 | 通知對應分頁已開啟時聚焦既有分頁並切換航線（E10） | @error-handling @p1 | INT-03, E2E-24 |
| 25 | 下降航班超過 3 條時只發下降幅度最大的 3 條（E11） | @error-handling @p0 | SYS-04, SYS-05, E2E-25 |
| 26 | 首次爬蟲無基準資料時跳過通知僅建立基準（E12） | @error-handling @p1 | SYS-06, E2E-26 |
| 27 | 滑掉通知時無任何後續動作（E13） | @error-handling @p1 | F-15, E2E-27 |
| 28 | file:// 本機開啟時無 SW 與推播，降級為一般頁面（E14） | @error-handling @p2 | F-24, INT-06, E2E-28, MAN-07 |
| 29 | 非 drop_last 條件一律不觸發通知 | @edge-case @p0 | SYS-02, SYS-14, E2E-29 |
| 30 | 通知頻率與爬蟲同頻，維持每週一次 | @edge-case @p1 | SYS-08, SYS-15, E2E-30 |
| 31 | 子路徑部署下通知 deep-link 以 SW scope 為基準拼接 | @edge-case @p1 | F-14, INT-04, E2E-31 |
| 32 | 訂閱以瀏覽器/裝置為單位，無跨裝置同步 | @edge-case @p1 | HDL-10, E2E-32 |
| 33 | 訂閱狀態與離線快取彼此獨立，離線不失效 | @edge-case @p1 | INT-02, E2E-33 |
| 34 | iOS 16.4 以下版本收不到推播且無其他替代方案 | @edge-case @p2 | F-21, E2E-34, MAN-08 |
| 35 | 裝置通知設定（勿擾/靜音）影響通知可達性 | @edge-case @p2 | MAN-09（裝置層級，自動化不可達，文件註記） |
| 36 | 同時有多則通知時點擊一則只開啟該則航線 | @edge-case @p2 | INT-07, E2E-35, MAN-10 |
| 37 | manifest 欄位齊全且 Lighthouse 安裝與離線稽核通過 | @business-rules @p0 | F-16, E2E-36, MAN-11 |
| 38 | maskable 圖示主體落在 80% safe zone 內 | @business-rules @p1 | F-18, MAN-12 |
| 39 | index.html 具備 PWA 所需連結與 iOS meta | @business-rules @p0 | F-17, E2E-37 |
| 40 | 「安裝 App」按鈕只在瀏覽器觸發安裝事件後出現 | @business-rules @p0 | F-01, F-03, E2E-38 |
| 41 | iOS 依 UA 顯示「加到主畫面」提示且圖示為 apple-touch-icon | @business-rules @p1 | F-04, E2E-39 |
| 42 | 下降比對以最近一次抓取資料為基準（drop_last） | @business-rules @p0 | SYS-07, E2E-40 |
| 43 | GET /vapid-public-key 回傳前端訂閱所需公鑰 | @business-rules @p1 | HDL-01, E2E-41 |
| 44 | POST /subscribe 驗證後將訂閱寫入 KV | @business-rules @p1 | HDL-02, HDL-03, E2E-42 |
| 45 | POST /notify 驗證 token 後對全部訂閱者廣播並清理失效訂閱 | @business-rules @p0 | HDL-04, HDL-05, HDL-07, E2E-43 |
| 46 | 通知承載符合單則摘要格式（Outline ×2） | @business-rules @p0 | SYS-09, F-19a/b, HDL-08, E2E-44a/b |
| 47 | 憑證分層：公鑰公開、私鑰與訂閱名單只在推播服務端 | @business-rules @p1 | HDL-09, F-25, E2E-45 |
| 48 | 既有測試與 Lighthouse 全量零回歸 | @business-rules @p0 @regression | E2E-46 |
| 49 | 既有爬蟲、data/ 與 api/ 維持原樣，僅追加通知呼叫 | @business-rules @p1 @regression | SYS-13, E2E-47 |
| 50 | 訂閱與通知流程以 Playwright mock push service 端到端驗證 | @business-rules @p1 | E2E-48 |
| 51 | 公開免登入、少數親友規模且維持 $0 成本 | @business-rules @p2 | E2E-49, MAN-13 |
| 52 | README 與文件完整說明安裝、推播與 iOS 限制 | @business-rules @p2 | MAN-14 |

> 覆蓋率檢查：52/52 全數對應。Scenario Outline 展開：情境 6（4 rows）→ F-05a~d / E2E-06a~d；情境 46（2 rows）→ F-19a/b / E2E-44a/b。智能補充（SYS-12/14/15、HDL-11、F-22~F-26）為邊界/並發/依賴失敗/生命週期補充，均已標註「補充」，不取代任何 BDD 對應。

---

## 附錄 B：需新增／擴充的檔案

| 檔案 | 類型 | 說明 |
|------|------|------|
| `web/manifest.webmanifest` | 新增（功能） | Phase 1 manifest 規格（T2）：name/short_name/start_url（./）/scope（./）/display（standalone）/icons（192/512/512-maskable）/theme_color/background_color/lang（zh-Hant） |
| `web/icons/icon-192.png` `icon-512.png` `icon-512-maskable.png` `apple-touch-icon.png` | 新增（功能） | Phase 1 圖示（T1）；maskable 主體於 80% safe zone 內 |
| `scripts/gen_icons.py` | 新增（dev-only） | Pillow 一次性產生圖示（T1）；可於 CI/本地執行尺寸與 safe zone 檢查（F-18 / MAN-12 輔助） |
| `web/index.html` | 擴充（功能） | Phase 1（T3）：`<link rel="manifest">`、`apple-touch-icon`、`theme-color`、`mobile-web-app-capable`、`apple-mobile-web-app-capable`、`apple-mobile-web-app-status-bar-style`；Phase 2：安裝按鈕／訂閱按鈕 DOM |
| `web/pwa.js` | 新增（功能模組） | 安裝按鈕狀態機、iOS UA/版本判定、訂閱 UI 三態、user gesture 防護、notificationclick 拼接、通知承載格式化（UMD 匯出對齊 `cache.js`；§4 F-01~F-26 測試主體） |
| `web/app.js` | 擴充（功能） | 整合 pwa.js：beforeinstallprompt／安裝按鈕（T4）、訂閱 toggle 與狀態 UI（T9） |
| `web/sw.js` | 擴充（功能） | 新增 `push`／`notificationclick`／`notificationclose` handler（T9）；cache name bump（既有 shell precache 邏輯不動） |
| `web/styles.css` | 擴充（功能） | 安裝按鈕／訂閱按鈕／拒絕引導／iOS 提示／狀態樣式（沿用既有設計 token） |
| `worker/wrangler.toml` + `worker/src/index.js` | 新增（功能） | Cloudflare Worker（T8）：`GET /vapid-public-key`、`POST /subscribe`（驗證→KV）、`POST /notify`（驗 token→Web Push 廣播、404/410 清理）；VAPID 金鑰與 secret 配置 |
| `worker/src/index.test.js` | 新增（單元測試） | 對應 §3 HDL-01~HDL-11；handler 與純函式分離、KV/push 呼叫 mock 注入；執行 `node --test worker/src/index.test.js` |
| `fetch_prices.py` | 擴充（功能） | Phase 2（T10）：爬蟲完成後 drop_last 偵測（基準 = 上一週 `data/*.json` 原始檔；**非 `api/latest.json`**，notify 前已被本次覆寫）→ 節流選取 → `POST /notify`（Bearer `PUSH_API_TOKEN`） |
| `tests/unit/test_pwa_drops.py` | 新增（單元測試） | 對應 §2 SYS-01~SYS-15；stdlib `unittest`，零新依賴 |
| `tests/unit/pwa.test.js` | 新增（單元測試） | 對應 §4 F-01~F-26；`require('../../web/pwa.js')` + `fs` 讀取 manifest/index.html/icons 靜態驗證 |
| `tests/mock_worker.py` | 新增（E2E helper，可選） | 本機 mock push service 實作（三端點＋token 驗證＋KV 記憶體）；或直接以 Playwright route 攔截（§6.0） |
| `tests/e2e_pwa.py` | 新增（E2E） | 對應 §5 INT-01~07 / §6 E2E-01~49；helper：beforeinstallprompt stub、permission/push stub（add_init_script）、CDP dispatchPushEvent/dispatchNotificationClickEvent、iOS UA、set_offline（沿用 e2e_offline.py 技術） |
| `.github/workflows/weekly-crawl.yml` | 擴充（功能） | Phase 2（T11）：爬蟲完成後追加 `/notify` 步驟（repo secret `PUSH_API_TOKEN`）；既有爬蟲步驟維持原樣（E2E-47） |
| `README.md` + `docs/` | 擴充（文件） | T6/T13：安裝說明、推播訂閱說明、`PUSH_API_TOKEN` secret 設定、Worker 部署步驟（wrangler login/deploy、KV namespace）、iOS 限制（需加到主畫面且 16.4+） |
| 既有 `tests/e2e_smoke.py`（69 checks）／`tests/e2e_offline.py`（105 checks）／`tests/unit/aggregate.test.js`／`tests/unit/cache.test.js` | **不動** | 回歸門檻（D8）：PWA 上線後必須全綠；PWA 情境獨立成 `tests/e2e_pwa.py`，避免與既有回歸區塊相互干擾（沿用離線功能 T7 決策） |
