# 星宇航空機票價格爬蟲 ✈️

每週自動抓取**星宇航空（STARLUX, JX）**指定航線的來回票價（去程週六 → 回程下週日），
輸出 JSON 歷史檔案，供價格趨勢追蹤分析。

## 🏗️ 架構

```
GitHub Actions（每週五 09:00 UTC+8 自動執行）
    │
    ▼
fetch_prices.py
    ├─ POST ecapi.starlux-airlines.com/searchFlight/v2/flights/search
    │   （星宇官方訂票 API，免費、免註冊、免 token）
    ├─ 解析每個航班的來回總價（TWD）
    └─ 寫入 data/YYYYMMDD.json（每天一個檔，累積歷史）
```

## 📁 專案結構

```
├── fetch_prices.py              # 主程式
├── config.py                    # 設定（航線、週數、艙等）
├── requirements.txt
├── .github/workflows/weekly-crawl.yml   # 每週排程
├── data/                        # 歷史價格 JSON（自動產生）
└── docs/tech-decisions/機票價格爬蟲-2026-08-14.md  # 決策文件
```

## 🚀 快速開始

### 本機測試

```bash
pip install -r requirements.txt
python fetch_prices.py --date 2026-08-14   # --date 指定參考日期（測試）
python fetch_prices.py                       # 用今天日期
```

輸出範例 `data/20260814.json`：

```json
[
  {
    "route_id": "TPE-NRT",
    "outbound_date": "2026-08-15",
    "return_date": "2026-08-23",
    "outbound_flight_no": "JX 804",
    "outbound_departure_time": "15:00",
    "outbound_arrival_time": "19:25",
    "airline_code": "JX",
    "airline_name": "星宇航空",
    "price_total": 26488,
    "currency": "TWD",
    "status": "Available",
    "data_completeness": "Complete",
    "scraped_at": "2026-08-14T09:30:00.000Z",
    "source": "starlux_official_api"
  }
]
```

### 部署到 GitHub Actions（自動每週抓）

1. 建立 **public** repo 並推上去
2. 完成！workflow 已設定每週五 09:00 (UTC+8) 自動執行，資料會自動 commit 回 `data/`
3. 也可以到 Actions 頁面手動觸發（Run workflow）

> 不需要任何 Secrets — API 免費且無需認證。（選用：若要「票價下降通知」，需另設 `PUSH_API_TOKEN`，見「🔔 票價下降通知（選用）」）

## ⚙️ 設定（config.py）

| 參數 | 說明 | 預設 |
|------|------|------|
| `ROUTES` | 追蹤的航線 | TPE-NRT（東京）、TPE-KIX（大阪）、TPE-FUK（福岡）、TPE-CTS（札幌） |
| `NUM_WEEKS` | 查詢未來幾週 | 10 |
| `RETURN_AFTER_DAYS` | 回程 = 去程 + 天數（下週日） | 8 |
| `CABIN` | 艙等 | eco |

**可用航線**（星宇直飛）：TPE-NRT 東京 / TPE-KIX 大阪 / TPE-FUK 福岡 / TPE-CTS 札幌 / TPE-OKA 沖繩 ...

想加航線就在 `ROUTES` 加一行：

```python
{"route_id": "TPE-FUK", "origin": "TPE", "destination": "FUK"},  # 福岡
```

## 📊 資料說明

- `price_total` = 該航班 **來回總價**（含稅，TWD），來自星宇官方搜尋結果頁同款價格
- 每個航線/日期組合會存**所有航班**（如 JX 800 / JX 802 / JX 804）
- 重複執行會去重合併（同 key = route + 日期 + 班號），不會重複累積
- 查詢失敗的組合會跳過並回報，不影響已成功的資料

## 📡 公開 API（靜態 JSON）

資料每週自動更新，公開給任何程式抓取（CORS 全開、免認證）。

### 進入點（三條路都可用）

| 路徑 | 網址 | 適合 |
|------|------|------|
| GitHub Pages | `https://yuhaoliaosideproject.github.io/AirTicketsPrice/api/index.json` | 正式使用 |
| raw | `https://raw.githubusercontent.com/YuHaoLiaoSideProject/AirTicketsPrice/main/api/index.json` | 開發測試 |
| jsDelivr CDN | `https://cdn.jsdelivr.net/gh/YuHaoLiaoSideProject/AirTicketsPrice@main/api/index.json` | 全球加速 |

### API 成品（每次爬蟲後自動產生）

```
api/
├── index.json       # 目錄：來源檔清單、航線、產生時間（從這裡開始）
├── latest.json      # 最新快照：每 (航線,去程,回程,班號) 保留最新票價
└── trips/           # 每趟旅程的價格歷史（畫趨勢圖直接用）
    └── TPE-NRT/
        └── 2026-08-22_2026-08-30.json
```

### 消費端建議流程（轉存 DB）

1. `GET api/index.json` → 看有哪些檔案
2. 依序抓 `data/*.json`（每週原始檔）→ **upsert 進 DB**
   （key = `route_id + outbound_date + return_date + outbound_flight_no`，
   同 key 保留 `scraped_at` 最新者）
3. 畫趨勢圖 → 直接抓 `api/trips/*.json`（每航班一條價格歷史序列）

### trips 檔範例

```json
{
  "route_id": "TPE-NRT",
  "outbound_date": "2026-08-22",
  "return_date": "2026-08-30",
  "flights": [
    {
      "outbound_flight_no": "JX 804",
      "airline_name": "星宇航空",
      "history": [
        {"scraped_at": "2026-08-14T12:21:03.000Z", "price_total": 26008, "status": "Available"},
        {"scraped_at": "2026-08-21T01:00:00.000Z", "price_total": 24120, "status": "Available"}
      ]
    }
  ]
}
```

> 原始每週檔也在 repo 裡：`data/20260814.json`（可經同 base URL 存取）。

---

## 📊 票價趨勢圖（前端）

純靜態前端單頁儀表板（零建置、零依賴），顯示各航線未來 40 週來回票價趨勢：

- 40 週每週最低價折線 + 全域平均虛線 + 最低價標記 + 旺季區塊（過年／櫻花季）
- 航線切換（東京／大阪／福岡／札幌）、航班切換、日期範圍篩選、hover tooltip
- Summary 三卡（最便宜出發週／全域平均／旺季提醒）

| 檔案 | 說明 |
|------|------|
| `web/index.html` | 單頁結構（語意化標籤 + aria） |
| `web/styles.css` | 設計 token + RWD + 無障礙 |
| `web/app.js` | 資料層 + 圖表層 + 互動層 + 狀態處理 |
| `web/aggregate.js` | 聚合純函式（可單元測試） |
| `tests/e2e_smoke.py` | Playwright E2E 冒煙 + mocked 邊界 |
| `tests/unit/aggregate.test.js` | 聚合模組單元測試（node:test） |

**線上網址**：`https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/`

### 本機開發／驗證

```bash
# 1. 起本機伺服器（repo 根目錄，模擬 Pages）
python -m http.server 8000
# 開啟 http://localhost:8000/web/

# 2. 單元測試（聚合 + 快取 + PWA 純函式）
node --test tests/unit/*.test.js

# 3. E2E 冒煙（真實資料 + mocked 邊界 + 375px mobile）
python tests/e2e_smoke.py   # 全部綠才 commit

# 4. PWA 安裝 E2E（Phase 1：manifest / SW / 安裝按鈕 / iOS / standalone / 離線 reload）
python tests/e2e_pwa.py
```

> 注意：前端純消費既有靜態 API（`api/index.json` / `api/trips/*.json`），不需登入；新增航線只需更新 `config.py` 並等爬蟲產出資料，前端 `web/aggregate.js` 的 `CONFIG.ROUTES` 同步加上即可。

---

## 📴 離線功能

純前端「快取優先 + 增量更新」：再次開啟頁面秒開；有網路時只補載變更資料，沒網路時直接瀏覽上次載入的快取（含離線開頁）。

- **快取優先 + 增量更新**：開啟時先以 IndexedDB 快取秒繪圖表，再於背景比對 `generated_at`——相同 →「已是最新」（0 個 trip 請求）；伺服器較新 → 以 ETag 條件式請求（`If-None-Match`）只補載變更的 trip 檔（304 零 body）；伺服器較舊 →「資料可能過時」警示（不覆寫本地新資料）
- **離線瀏覽**：Service Worker 快取 app shell（7 檔：index.html / styles.css / app.js / aggregate.js / cache.js / pwa.js / sw.js）→ 離線也能開啟頁面；資料存 IndexedDB → 離線顯示上次載入的趨勢圖（航班切換 / 日期篩選 / hover / Summary 三卡皆可用），頁首顯示離線橫幅「離線模式 · 顯示上次資料（HH:MM）」
- **手動更新**：連網時可點「手動更新」強制重新驗證（重新抓 index + 跑完整增量同步）；離線時按鈕停用顯示「離線中，無法更新」
- **限制**：首次訪問需連網（載入一次後才有快取）；離線僅限「上次載入過」的航線，未載入航線顯示「此航線尚未下載，需連網」並停留原航線；快取以瀏覽器為單位——無痕視窗／另一瀏覽器等同首次訪問

| 檔案 | 說明 |
|------|------|
| `web/cache.js` | 快取層：IndexedDB 薄封裝（meta / units 兩 store）+ 比對／增量／降級純函式（UMD 匯出，可單元測試） |
| `web/sw.js` | Service Worker：app shell precache（SWR，不攔 api/） |
| `web/app.js` | cache-first 啟動／背景比對／增量同步／離線狀態層／手動更新 |
| `web/aggregate.js` | 新增 `formatLastUpdated`（上次更新 HH:MM） |
| `tests/unit/cache.test.js` | 快取層單元測試（node:test） |
| `tests/e2e_offline.py` | 離線 E2E（Playwright `setOffline` + 條件式 304／200／404 mock） |

### 離線功能驗證

```bash
node --test tests/unit/cache.test.js tests/unit/aggregate.test.js   # 純函式單元測試（含離線快取層）
python tests/e2e_smoke.py    # 既有 E2E 冒煙（回歸門檻，不得退步）
python tests/e2e_offline.py  # 離線情境 E2E（首次／二次訪問、離線瀏覽、手動更新、E1–E8、邊界、商業規則）
```

> 離線功能與「票價趨勢圖（前端）」共用同一份 `api/` 資料：條件式請求只省下載量、不改變資料內容與圖表行為。

---

## 📱 安裝為 App（PWA）

純靜態頁面可安裝成主畫面 App（Phase 1：manifest + 圖示 + 安裝入口；Phase 2 推播訂閱見「🔔 票價下降通知（選用）」），對應行為：

### Android Chrome（或桌面 Chrome / Edge）

1. 開啟 `https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/`
2. 瀏覽器具備安裝資格時，頁首會出現「**安裝 App**」按鈕（`beforeinstallprompt` 事件後才顯示）
3. 點「安裝 App」→ 原生安裝確認框 → 接受後主畫面出現「票價趨勢」圖示
4. 之後從主畫面圖示開啟 = **standalone 模式**（無瀏覽器工具列；此時安裝按鈕不再顯示）

> 按鈕取消安裝後仍保留，可再次點擊觸發；已安裝（standalone）模式下安裝入口永不顯示（P1-C）。

### iOS Safari

iOS 不支援 `beforeinstallprompt`，依 UA 顯示「加到主畫面」提示（不顯示「安裝 App」按鈕）：

1. 點分享按鈕（底部工具列中間的分享圖示）
2. 往下捲選「**加到主畫面**」
3. 確認名稱「票價趨勢」→ 加入 → 主畫面出現圖示（使用 `apple-touch-icon` 180px）

之後從主畫面開啟即為 standalone 模式；未安裝（瀏覽器內開啟）時每次都會顯示「加到主畫面」提示。

### standalone 模式差異

- 無瀏覽器網址列／工具列，App 般全螢幕體驗
- 「安裝 App」按鈕與「加到主畫面」提示**皆隱藏**（已安裝語意，P1-C）
- 離線能力照常：SW 兜 app shell + IndexedDB 快取繪圖（與瀏覽器內開啟行為一致）

| 檔案 | 說明 |
|------|------|
| `web/manifest.webmanifest` | PWA manifest（`start_url`/`scope` 皆 `./`，GitHub Pages 子路徑 `/AirTicketsPrice/web/` 自動解析，S2） |
| `web/icons/` | 圖示四枚：192 / 512 / 512-maskable / apple-touch-icon(180) |
| `web/pwa.js` | 安裝狀態機 + 訂閱狀態機 + iOS 判定 + 通知純函式（UMD，可單元測試） |
| `web/app.js` / `web/sw.js` | `beforeinstallprompt` 整合 + 訂閱 toggle（T4/T9）；shell precache 含 `pwa.js`（cache v3）；`push`/`notificationclick`/`notificationclose` handler（T9） |
| `scripts/gen_icons.py` | 圖示產生腳本（dev-only；`--check` 尺寸驗證） |
| `tests/e2e_pwa.py` | PWA E2E（Phase 1 安裝 + Phase 2 訂閱/通知/離線並存 + CDP installability 稽核） |
| `tests/unit/pwa.test.js` | PWA 純函式與靜態規格單元測試（node:test） |
| `worker/` | Cloudflare Worker 推播服務（`/vapid-public-key`、`/subscribe`、`/notify`）+ 單元測試 + 部署文件 |

### PWA 驗證

```bash
node --test tests/unit/*.test.js   # 純函式單元測試（含 pwa.test.js）
node --test worker/src/index.test.js   # Worker 三端點單元（HDL-01~11）
python -m unittest discover -s tests/unit   # 爬蟲 Python 單元（含 notify drop 偵測）
python tests/e2e_smoke.py          # 既有 E2E 冒煙（回歸門檻，不得退步）
python tests/e2e_offline.py        # 既有離線情境（回歸門檻，不得退步）
python tests/e2e_pwa.py            # PWA E2E（安裝 + 訂閱/通知/離線並存 + CDP installability）
```

> 安裝性稽核：Lighthouse 13 起已移除 PWA 安裝稽核（installable / works-offline），改以 CDP `Page.getAppManifest` + `Page.getInstallabilityErrors` 驗證（errors 為空 = 可安裝）；Lighthouse 列為 MAN 手動驗證項。

---

## 🔔 票價下降通知（選用，PWA Phase 2）

每週五爬蟲完成並 commit 後，若任一航班票價較**上次抓取**下降（`drop_last`），系統會以 Web Push 主動推播**單則摘要通知**（最多 3 條、取下降幅度最大者）；點通知直接跳到該航線。

> 未部署通知功能**不影響任何既有功能**：爬蟲照常、資料照常 commit，workflow 只會跳過通知 step。

### 運作流程

```
GitHub Actions（每週五爬蟲 + commit 後）
    │  python fetch_prices.py --notify（Bearer PUSH_API_TOKEN）
    ▼
Cloudflare Worker /notify（驗證 token → VAPID Web Push 廣播）
    ▼
瀏覽器／裝置推播通知（點擊 → 開啟頁面並跳到該航線）
```

### 前置：部署 Cloudflare Worker + VAPID 金鑰

依 `worker/README.md`「部署步驟」完成（一次性）：

1. `wrangler kv namespace create SUBSCRIPTIONS` → binding id 填入 `worker/wrangler.toml`
2. `node worker/spike/gen-vapid-keys.mjs` 產生 VAPID 金鑰對（公鑰貼到 wrangler.toml `[vars]`）
3. `wrangler secret put VAPID_PRIVATE_KEY`（VAPID 私鑰）
4. `wrangler secret put PUSH_API_TOKEN`（與 GitHub repo secret **同值**）
5. `wrangler deploy` → 取得 `https://airtickets-price-push.<account>.workers.dev`

部署後把實際網域更新到三處：本 workflow 的 `PUSH_NOTIFY_URL`、`web/pwa.js` 的 `CONFIG.PUSH_WORKER_URL`（備援 `config.py` 的 `PUSH_NOTIFY_URL`）。

### GitHub repo secret 設定

Settings → Secrets and variables → Actions → **New repository secret**：

| Secret | 值 |
|--------|-----|
| `PUSH_API_TOKEN` | 與 Worker 的 `PUSH_API_TOKEN` secret **相同**的值（兩端共用 token，D6） |

> 外洩時兩端同時輪換（`wrangler secret put PUSH_API_TOKEN` 重新設定 + GitHub secret 更新）。

### 未設定 secret 的行為

workflow 的「Detect drops & notify」step 有 `if:` 守衛：`secrets.PUSH_API_TOKEN != ''` 才執行。未設定 secret → 該 step **整體跳過、不報錯**，爬蟲與資料 commit 照常完成（workflow 顯示成功）——維持「無 secret 也能跑」的既有彈性。

### 使用者端：開啟票價提醒

- 頁面出現「**開啟票價提醒**」按鈕 → 同意通知權限 → 訂閱成功後變為「關閉票價提醒」
- 需 **HTTPS**（Web Push 僅允許安全來源；GitHub Pages 已是 HTTPS）
- **iOS**：需先「加到主畫面」（以 standalone 開啟）且 **iOS 16.4+** 才支援 Web Push；直接在 Safari 瀏覽器內開啟不會收到通知（會顯示引導提示）
- 收到通知即為摘要內容；點通知開啟頁面並自動切到該航線

### 驗證通知（選用）

```bash
# 本機 dry-run：不實際發送，只輸出將送出的 payload
PUSH_API_TOKEN=<token> PUSH_NOTIFY_URL=<worker>/notify python fetch_prices.py --notify --dry-run
```

也可對 Worker 直接 smoke：`curl` 呼叫 `/vapid-public-key` / `/notify`（見 `worker/README.md`「部署後 smoke」）。

---

## 📈 之後可擴充

- [x] 趨勢圖視覺化（把 data/*.json 畫成折線圖）
- [x] PWA 可安裝（manifest + 圖示 + 安裝按鈕 + iOS「加到主畫面」提示 + standalone）
- [x] 票價下降推播通知（Cloudflare Worker 自建 Web Push；每週五 drop_last 偵測 → 單則摘要；iOS 需加到主畫面且 16.4+）
- [ ] 同一趟旅程的歷史走勢（需資料累積數週後）
- [ ] 商務艙追蹤
- [ ] 多航線並排比較
- [ ] 通知觸發條件擴充（below_avg / new_low / 週摘要——架構已預留，只需改爬蟲端偵測與 payload）
