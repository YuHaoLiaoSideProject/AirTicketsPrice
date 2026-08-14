# 開發方案決策文件：機票價格爬蟲

## 📌 決策摘要

| 項目 | 內容 |
|------|------|
| **最終方案** | 星宇航空 STARLUX 官方訂票 API 直爬（免費、免註冊、免 token） |
| **決策日期** | 2026-08-14 |
| **參與討論** | 使用者（個人專案） |
| **共識程度** | ✅ 單人決策確認 |
| **驗證狀態** | ✅ 已實測成功（TPE⇄NRT、TPE⇄KIX 皆回傳真實票價） |

---

## 1. 需求回顧

| 項目 | 共識 |
|------|------|
| 核心用途 | 價格趨勢追蹤（每週記錄歷史價格） |
| 航線 | 台北⇄東京（TPE→NRT）、台北⇄大阪（TPE→KIX），去程週六、回程下週日 |
| 來源 | 只要**一個**航空公司，以「最容易爬」為原則，排除廉航 |
| 頻率 | 每週一次（GitHub Actions cron） |
| 規模 | 個人自用 |
| 部署 | GitHub Actions（public repo，免費無限量） |
| 儲存 | **JSON**：每次執行存 `data/YYYYMMDD.json`（例 `20260814.json`），累積歷史 |
| 記錄粒度 | 每個航線/日期組合存**所有航班**（JX 800/802/804...） |
| 查詢範圍 | 未來 **10 週**（先驗證，之後可擴到 40 週） |
| 幣別 | TWD（新台幣） |

**關鍵限制**：
- Amadeus Self-Service 已退役（2026/7 停新註冊），Enterprise 需業務合約
- Kiwi Tequila API 已轉合作夥伴制，自助免費註冊停用（實測 portal 無法註冊）

---

## 2. 候選方案（最終輪）

### 🟢 方案 A：星宇 STARLUX 官方 API 直爬（**當選**）
- 逆向自官網的 `ecapi.starlux-airlines.com/searchFlight/v2/flights/search`
- **實測結果**：POST + `jx-lang` header，回傳航班、時間、各艙等來回總價（TWD）
- 免費、免註冊、免 token、零反爬蟲（無 bot 防護）
- 缺點：只涵蓋星宇自家航班（符合「只要一個航空」）

### 🔵 方案 B：Kiwi Tequila API
- 註冊失敗（portal 已轉合作夥伴制）→ 放棄

### 🟡 方案 C：SerpAPI Google Flights
- 涵蓋所有航空（含長榮），但每月 ~$50（免費額度 100 次/月）
- 個人每週 8 次查詢免費額度勉強夠，但付費風險與依賴第三方 → 暫緩，作為日後多航空需求時的選項

### 🟡 方案 D：其他航空直爬
- 長榮 EVA = Imperva 防護 ❌；中華航空 = Akamai 防護 ❌ → 排除

---

## 3. 權衡評估

| 維度 | 🟢 A: 星宇直爬 | 🔵 B: Tequila | 🟡 C: SerpAPI |
|------|:---:|:---:|:---:|
| 🎯 需求符合度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐（註冊卡關） | ⭐⭐⭐⭐⭐ |
| ⚡ 開發速度 | ⭐⭐⭐⭐⭐（已上線） | — | ⭐⭐⭐⭐ |
| 🔧 維護成本 | ⭐⭐⭐⭐ | — | ⭐⭐⭐⭐ |
| 💰 成本 | ⭐⭐⭐⭐⭐（$0） | — | ⭐⭐（$50/月） |
| 🔒 穩定性 | ⭐⭐⭐⭐ | — | ⭐⭐⭐⭐ |
| ⚖️ 法規/倫理 | ⭐⭐⭐（灰區但低頻低風險） | — | ⭐⭐⭐⭐ |

---

## 4. 決策理由

### 為什麼選擇星宇官方 API
1. **唯一實測成功且 $0 成本**的路徑（Tequila 註冊失敗、SerpAPI 要錢、EVA/華航有 bot 防護）
2. 星宇同時飛 TPE⇄NRT（東京）與 TPE⇄KIX（大阪），全服務航空，一次滿足兩條航線
3. 每週一次的個人用途，低頻查詢風險極低，不需要付費方案

### 放棄其他方案
- **Tequila**：自助註冊已停，需合作夥伴審核
- **SerpAPI**：付費，等有多航空需求再評估
- **EVA / 華航直爬**：Imperva / Akamai 機器人防護
- **Duffel**：台灣籍航空涵蓋不明、生產審核不確定

---

## 5. 行動計畫（已完成 ✅）

### 技術棧

| 層級 | 技術 | 備註 |
|------|------|------|
| 語言 | Python 3.12 | `requests` 即可 |
| API | 星宇 searchFlight/v2 | POST JSON，`jx-lang: zh-TW` |
| 排程 | GitHub Actions cron | 每週五 09:00 UTC+8 + 手動觸發 |
| 儲存 | JSON | `data/YYYYMMDD.json`，去重合併 |
| 機密 | 無 | API 免費免認證 |

### 已交付檔案

```
fetch_prices.py                         # 主程式（已實測：20/20 查詢成功）
config.py                               # 航線/週數/艙等設定
requirements.txt
.github/workflows/weekly-crawl.yml      # 每週排程 + 自動 commit data/
data/20260814.json                      # 首批 50 筆真實票價
README.md                               # 使用與部署說明
```

### API 規格（實測紀錄）

```
POST https://ecapi.starlux-airlines.com/searchFlight/v2/flights/search
Headers: Content-Type: application/json | jx-lang: zh-TW
Body: {
  "atHome": false, "trip": "round-trip",
  "itineraries": [{"departure":"TPE","arrival":"NRT","departureDate":"2026-08-22"},
                   {"departure":"NRT","arrival":"TPE","departureDate":"2026-08-30"}],
  "firstPickerInfo": {"index":0, "date":"2026-08-22"},
  "travelers": {"adt":1, "chd":0, "inf":0}, "cabin": "eco", "promotion": null
}
→ flights[]: flightNo, flightDetails(起降時間), priceInfo(每艙等 from + totalPrices.total)
```

> **重要發現**：`priceInfo[].totalPrices.total` = **含回程的完整來回總價**（同去程不同回程日期 → 總價不同，實測驗證）。一次查詢即得來回價，無需分開查。

### 輸出 Schema（對應使用者指定格式）

```json
{
  "route_id": "TPE-NRT", "outbound_date": "2026-08-22", "return_date": "2026-08-30",
  "outbound_flight_no": "JX 804", "outbound_departure_time": "15:00",
  "outbound_arrival_time": "19:25", "airline_code": "JX", "airline_name": "星宇航空",
  "price_total": 26008, "currency": "TWD", "status": "Available",
  "data_completeness": "Complete", "scraped_at": "2026-08-14T09:30:00.000Z",
  "source": "starlux_official_api"
}
```

---

## 6. 風險登錄

| 風險 | 可能性 | 影響 | 緩解措施 |
|------|--------|------|---------|
| 星宇網站改版致 API 異動 | 中 | 中 | 定期驗證；endpoint 與 payload 集中在 config/單一函式 |
| API 灰區法律風險 | 低 | 低 | 個人低頻查詢；僅讀取公開票價 |
| 日期範圍過遠查無票 | 中 | 低 | 10 週內皆正常；40 週擴充時觀察 |
| 航班售罄無報價 | 中 | 低 | 該航班跳過，其他航班照常 |

---

## 📝 決策後續

- ✅ 已完成 P0~P2 實作並實測成功（首批 50 筆票價已產生）
- 待辦：推上 GitHub（public repo）啟用自動排程
- 若 10 週驗證順暢 → 擴到 40 週、加航線
- 若需多航空比價 → 評估 SerpAPI（付費）
