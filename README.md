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

> 不需要任何 Secrets — API 免費且無需認證。

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

# 2. 單元測試（聚合純函式）
node --test "tests/unit/*.test.js"

# 3. E2E 冒煙（真實資料 + mocked 邊界 + 375px mobile）
python tests/e2e_smoke.py   # 全部綠才 commit
```

> 注意：前端純消費既有靜態 API（`api/index.json` / `api/trips/*.json`），不需登入；新增航線只需更新 `config.py` 並等爬蟲產出資料，前端 `web/aggregate.js` 的 `CONFIG.ROUTES` 同步加上即可。

---

## 📈 之後可擴充

- [x] 趨勢圖視覺化（把 data/*.json 畫成折線圖）
- [ ] 同一趟旅程的歷史走勢（需資料累積數週後）
- [ ] 商務艙追蹤
- [ ] 多航線並排比較
