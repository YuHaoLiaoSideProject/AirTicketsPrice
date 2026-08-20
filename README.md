# ✈️ 星宇航空機票價格追蹤

[![Weekly Crawl](https://github.com/YuHaoLiaoSideProject/AirTicketsPrice/actions/workflows/weekly-crawl.yml/badge.svg)](https://github.com/YuHaoLiaoSideProject/AirTicketsPrice/actions/workflows/weekly-crawl.yml)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live-brightgreen)](https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#授權條款)

每週自動抓取**星宇航空（STARLUX, JX）**指定航線的來回票價，搭配 PWA 趨勢圖儀表板與票價下降推播通知。

## 🎯 核心功能

- **🔍 自動爬蟲**：每週五透過 GitHub Actions 自動抓取 7 條航線 40 週票價，免人工、免 API Key
- **📊 趨勢圖儀表板**：純靜態 PWA 前端，每週最低價折線圖 + 整體平均 + 旺季區塊標記
- **📴 離線瀏覽**：IndexedDB 快取 + Service Worker，離線也能查看上次載入的趨勢圖
- **📱 PWA 安裝**：可安裝到手機主畫面，像 App 一樣使用（Android / iOS 16.4+）
- **🔔 票價下降通知**：Web Push 推播，偵測到票價下降即時通知（Cloudflare Worker）
- **📡 公開 API**：靜態 JSON API，CORS 全開、免認證，任何程式皆可抓取
- **🗓️ 旺季自動計算**：農曆過年旺季以曆法推算，無需每年手動更新

## 🖥️ Demo

**線上體驗**：https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/

支援 7 條航線切換（東京 / 大阪 / 福岡 / 札幌 / 名古屋 / 釜山 / 胡志明）、航班切換、hover 查看單週票價、Summary 三卡（最便宜出發週 / 全域平均 / 旺季提醒）。

## 📋 環境需求

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 爬蟲 + build_api |
| Node.js | 18+ | 前端單元測試（`node --test`） |
| pip | — | 安裝 Python 依賴 |
| Playwright | — | E2E 測試（`pip install playwright`） |
| Wrangler | — | Worker 部署（選用） |

## ⚡ 安裝與執行

### 1. Clone 專案

```bash
git clone https://github.com/YuHaoLiaoSideProject/AirTicketsPrice.git
cd AirTicketsPrice
```

### 2. 安裝依賴

```bash
pip install -r requirements.txt
```

### 3. 執行爬蟲

```bash
# 正常執行（用今天日期，抓未來 40 週）
python fetch_prices.py

# 指定參考日期（測試用）
python fetch_prices.py --date 2026-08-14

# 票價下降偵測 + 通知（需設定環境變數，見下方）
python fetch_prices.py --notify

# Dry-run：不實際發送，只輸出通知 payload
python fetch_prices.py --notify --dry-run
```

### 4. 編譯靜態 API

```bash
python build_api.py
# 產出 api/index.json、api/latest.json、api/trips/
```

### 5. 啟動前端（本機開發）

```bash
python -m http.server 8000
# 瀏覽器開啟 http://localhost:8000/web/
```

## 🔧 環境變數

本專案核心爬蟲**不需要任何 API Key**（星宇官方 API 免費、免認證）。

> 以下為選用設定，僅「票價下降通知」功能需要：

| 變數 | 說明 | 必要 |
|------|------|------|
| `PUSH_API_TOKEN` | Cloudflare Worker 認證 token（與 Worker secret 同值） | 選用 |
| `PUSH_NOTIFY_URL` | Worker 通知端點 URL（預設 `https://airtickets-price-push.h770320.workers.dev/notify`） | 選用 |

### GitHub Actions 設定

在 repo 的 **Settings → Secrets and variables → Actions** 中設定：

| Secret | 值 |
|--------|-----|
| `PUSH_API_TOKEN` | 與 Cloudflare Worker 的 `PUSH_API_TOKEN` secret **相同** |

> 未設定 `PUSH_API_TOKEN` → workflow 仍正常執行，僅跳過通知步驟。

## ⚙️ 設定（config.py）

| 參數 | 說明 | 預設 |
|------|------|------|
| `ROUTES` | 追蹤的航線 | TPE-NRT（東京）、TPE-KIX（大阪）、TPE-FUK（福岡）、TPE-CTS（札幌）、TPE-NGO（名古屋）、TPE-PUS（釜山）、TPE-SGN（胡志明） |
| `NUM_WEEKS` | 查詢未來幾週 | 40 |
| `RETURN_AFTER_DAYS` | 回程 = 去程 + 天數（下週日） | 8 |
| `CABIN` | 艙等 | eco |

**可用航線**（星宇直飛）：TPE-NRT 東京 / TPE-KIX 大阪 / TPE-FUK 福岡 / TPE-CTS 札幌 / TPE-NGO 名古屋 / TPE-PUS 釜山 / TPE-SGN 胡志明

想加航線就在 `ROUTES` 加一行：

```python
{"route_id": "TPE-FUK", "origin": "TPE", "destination": "FUK"},  # 福岡
```

## 📁 專案結構

```
├── fetch_prices.py              # 主程式（爬蟲 + 通知偵測）
├── config.py                    # 設定（航線、週數、艙等）
├── build_api.py                 # 把 data/*.json 編譯成靜態 API 成品（api/）
├── holidays.py                  # 農曆過年旺季區間計算（lunardate，取代手動更新）
├── requirements.txt             # requests + lunardate
├── .github/workflows/
│   └── weekly-crawl.yml         # 每週排程（爬蟲 → build → commit → Pages 部署）
├── data/                        # 歷史價格 JSON（自動產生）
├── api/                         # 靜態 API 成品（build_api.py 產出，Pages 部署）
├── web/                         # 純靜態前端（PWA 單頁儀表板）
│   ├── index.html               # 單頁結構（語意化標籤 + aria）
│   ├── styles.css               # 設計 token + RWD + 無障礙
│   ├── app.js                   # 資料層 + 圖表層 + 互動層 + 狀態處理
│   ├── aggregate.js             # 聚合純函式（可單元測試）
│   ├── cache.js                 # IndexedDB 快取層（UMD，可單元測試）
│   ├── pwa.js                   # 安裝狀態機 + 訂閱狀態機 + 通知純函式
│   ├── sw.js                    # Service Worker（app shell precache）
│   ├── manifest.webmanifest     # PWA manifest
│   └── icons/                   # 圖示四枚（192 / 512 / 512-maskable / apple-touch-icon）
├── worker/                      # Cloudflare Worker 推播服務
├── tests/
│   ├── unit/                    # 單元測試（Node.js + Python）
│   ├── e2e_smoke.py             # Playwright E2E 冒煙
│   ├── e2e_offline.py           # 離線情境 E2E
│   └── e2e_pwa.py               # PWA E2E
└── docs/tech-decisions/         # 技術決策文件
```

## 🏗️ 系統架構

```
GitHub Actions（每週五 09:00 UTC+8 自動執行）
    │
    ▼
fetch_prices.py（爬蟲）──── build_api.py（API 編譯）──── Deploy Pages
    │                            │
    ├─ 星宇搜尋 API              ├─ api/index.json（目錄）
    ├─ 解析來回總價（TWD）        ├─ api/latest.json（最新快照）
    └─ 寫入 data/YYYYMMDD.json    └─ api/trips/（價格歷史）
    │
    ▼
fetch_prices.py --notify（偵測下降 → Cloudflare Worker Web Push）
```

## 📡 公開 API

資料每週自動更新，公開給任何程式抓取（CORS 全開、免認證）。

### 進入點

| 路徑 | 網址 | 說明 |
|------|------|------|
| GitHub Pages | `https://yuhaoliaosideproject.github.io/AirTicketsPrice/api/index.json` | 正式使用 |

> `api/` 為 CI 產物不進 repo，如需原始資料可用 `data/*.json`（已 commit）。

### API 結構

```
api/
├── index.json       # 目錄：來源檔清單、航線、產生時間（從這裡開始）
├── latest.json      # 最新快照：每 (航線,去程,回程,班號) 保留最新票價
└── trips/           # 每趟旅程的價格歷史（畫趨勢圖直接用）
    └── TPE-NRT/
        └── 2026-08-22_2026-08-30.json
```

### 消費端建議流程

1. `GET api/index.json` 取得檔案清單
2. 依序抓 `data/*.json` upsert 進 DB（key = `route_id + outbound_date + return_date + outbound_flight_no`，同 key 保留 `scraped_at` 最新者）
3. 畫趨勢圖直接抓 `api/trips/*.json`（每航班一條價格歷史序列）

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

### 輸出資料範例（data/YYYYMMDD.json）

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

## 📊 票價趨勢圖（前端）

純靜態前端單頁儀表板（零建置、零依賴），顯示各航線未來 40 週來回票價趨勢：

- 40 週每週最低價折線 + 全域平均虛線 + 最低價標記 + 旺季區塊（過年／櫻花季）
- 航線切換（東京／大阪／福岡／札幌／名古屋／釜山／胡志明）、航班切換、hover tooltip
- Summary 三卡（最便宜出發週／全域平均／旺季提醒）



## 📴 離線功能

純前端「快取優先 + 增量更新」：再次開啟頁面即刻載入；有網路時只補載變更資料，沒網路時直接瀏覽上次載入的快取。

- **快取優先 + 增量更新**：開啟時先以 IndexedDB 快取繪製圖表，再於背景比對 `generated_at`——相同 → 不發出請求；伺服器較新 → 以 ETag 條件式請求（`If-None-Match`）只補載變更的 trip 檔（304 回應無 body）；伺服器較舊 → 顯示「資料可能過時」警示，不覆寫本地新資料
- **離線瀏覽**：Service Worker 快取 app shell（7 檔）→ 離線也能開啟頁面；資料存 IndexedDB → 離線顯示上次載入的趨勢圖（航班切換 / 日期篩選 / hover / Summary 三卡皆可用），頁首顯示離線橫幅「離線模式 · 顯示上次資料（HH:MM）」
- **手動更新**：連網時可點「手動更新」強制重新驗證；離線時按鈕停用顯示「離線中，無法更新」
- **限制**：首次訪問需連網；離線僅限上次載入過的航線，未載入航線顯示「此航線尚未下載，需連網」；快取以瀏覽器為單位，無痕視窗等同首次訪問

## 📱 安裝為 App（PWA）

純靜態頁面可安裝成主畫面 App，對應行為：

- **Android / 桌面 Chrome**：頁首出現「安裝 App」按鈕，點擊後確認安裝即可
- **iOS Safari**：點分享按鈕 → 「加到主畫面」（需 16.4+ 才支援 Web Push）

## 🔔 票價下降通知

每週五爬蟲完成後，若任一航班票價較上次下降，系統以 Web Push 推播通知（最多 3 條，取下降幅度最大者），點擊直接跳到該航線。

### 運作流程

```
GitHub Actions（每週五爬蟲 + commit 後）
    │  python fetch_prices.py --notify（Bearer PUSH_API_TOKEN）
    ▼
Cloudflare Worker /notify（驗證 token → VAPID Web Push 廣播）
    ▼
瀏覽器／裝置推播通知（點擊 → 開啟頁面並跳到該航線）
```

### 部署 Worker

依 `worker/README.md` 完成一次性部署。

## 🧪 測試

### 單元測試

```bash
# JavaScript（聚合 + 快取 + PWA 純函式）
node --test tests/unit/*.test.js

# Python（農曆旺季 + 票價下降偵測）
python -m unittest discover -s tests/unit

# Worker 三端點（HDL-01~11）
node --test worker/src/index.test.js
```

### E2E 測試

```bash
# 冒煙測試（真實資料 + mocked 邊界 + 375px mobile）
python tests/e2e_smoke.py

# 離線情境（Playwright setOffline + 條件式 304/200/404 mock）
python tests/e2e_offline.py

# PWA 安裝 + 訂閱/通知/離線並存 + CDP installability
python tests/e2e_pwa.py
```

## 📈 之後可擴充


- [ ] 同一趟旅程的歷史走勢（需資料累積數週後）
- [ ] 商務艙追蹤
- [ ] 多航線並排比較
- [ ] 通知觸發條件擴充（below_avg / new_low / 週摘要）

## 🤝 貢獻指南

歡迎提交 Issue 與 Pull Request！

1. Fork 此專案
2. 建立功能分支：`git checkout -b feature/your-feature`
3. 提交變更：`git commit -m "feat: add your feature"`
4. 推送分支：`git push origin feature/your-feature`
5. 建立 Pull Request

### 開發流程

1. 修改 `config.py`（加航線 / 調參數）
2. 本地測試：`python fetch_prices.py --date 2026-08-14`
3. 編譯 API：`python build_api.py`
4. 跑測試：`node --test tests/unit/*.test.js && python tests/e2e_smoke.py`
5. Commit & Push → GitHub Actions 自動部署

## 📄 授權條款

本專案採用 [MIT License](LICENSE) 授權。

## 🙏 致謝

- [星宇航空](https://www.starlux-airlines.com/) 官方訂票 API（免費、免認證）
- [Cloudflare Workers](https://workers.cloudflare.com/) 推播服務
- [GitHub Pages](https://pages.github.com/) 靜態託管
- [Playwright](https://playwright.dev/) E2E 測試框架
