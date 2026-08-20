# 華航票價爬蟲 — Trip.com API 研究

## 📌 研究摘要

| 項目 | 狀態 |
|------|------|
| 網站 | ✅ 可訪問 |
| 網站架構 | React 框架 |
| 搜尋表單 | ⚠️ 需要正確互動（有 overlay 攔截） |
| APP API | ⚠️ 需要 mitmproxy 攔截 |

| 項目 | 狀態 |
|------|------|
| 網站 API | ⚠️ 需要正確的 endpoint |
| APP API | ⚠️ 需要 mitmproxy 攔截 |
| 認證 | ⚠️ 可能需要 cookie/token |

---

## 已發現的 API Endpoints

### 資料類
| Endpoint | 功能 |
|----------|------|
| `/restapi/soa2/21273/GetRouteInfo` | 路線資訊 |
| `/restapi/soa2/14427/getUrgentNotice` | 航班通知 |
| `/restapi/soa2/14427/getUserCoins` | 用戶積分 |

### 設定類
| Endpoint | 功能 |
|----------|------|
| `/restapi/soa2/18088/getAppConfig.json` | App 設定 |
| `/restapi/soa2/24884/json/getConfiguration` | 配置資訊 |

### 日誌類
| Endpoint | 功能 |
|----------|------|
| `/restapi/soa2/16163/saveLogInfo` | 儲存日誌 |
| `/restapi/soa2/29624/clog` | 客戶端日誌 |

---

## API 結構

### Request Head
```json
{
  "cid": "09034031218719293407",  // 用戶識別碼
  "ctok": "",                      // 可能是 token
  "cver": "1.0",
  "lang": "01",
  "sid": "",
  "syscode": "09",
  "locale": "zh-TW",
  "extension": []
}
```

### Response 結構
```json
{
  "ResponseStatus": {
    "Timestamp": "/Date(...)/",
    "Ack": "Success",
    "Errors": []
  }
}
```

---

## 缺少的 API

### 航班搜尋 API
- 未找到類似 `flightList` 或 `searchFlight` 的 endpoint
- 可能需要：
  1. 正確的 URL 格式
  2. 特定的 header
  3. Cookie 認證

---

## 網站前端技術發現

### 1. 搜尋元素（data-testid）
```javascript
[data-testid="search_city_from0"]  // 出發地
[data-testid="search_city_to0"]    // 目的地
[data-testid="search_date_depart0"]  // 出發日期
[data-testid="search_date_return0"]  // 回程日期
[data-testid="search_btn"]  // 搜尋按鈕
```

### 2. URL 格式
```
https://tw.trip.com/flights/list?dcity=TPE&acity=NRT&startdate=2027-01-09&enddate=2027-01-17&flighttype=rt&class=y&quantity=1
```

### 3. 表單互動注意事項
- 使用 React 框架，有 overlay 攔截問題
- 建議使用 `page.evaluate()` 直接操作 DOM
- 避免使用 `fill()` 方法（會被 overlay 擋住）

---

## 建議方案

### 方案 1：使用 mitmproxy 攔截 APP 請求

```bash
# 安裝 mitmproxy
pip install mitmproxy

# 啟動 proxy
mitmproxy --listen-port 8080

# 手機設定 proxy 並安裝憑證
# 打開 Trip.com APP 搜尋航班
# 分析攔截到的請求
```

### 方案 2：使用 Playwright 完成 Web 自動化

```python
# 使用 Playwright 自動化 Trip.com 網頁版
# 1. 載入航班頁面
# 2. 用 JS 填寫表單
# 3. 點擊搜尋
# 4. 提取結果
```

### 方案 3：研究 Trip.com 的公開文件

- 檢查是否有公開 API 文件
- 尋找開發者文件
- 研究 API 使用條款

---

## 結論

### 最佳方案
🟡 **使用 Playwright 完成 Web 自動化**

### 理由
1. 不需要額外工具（mitmproxy）
2. 可以直接在 GitHub Actions 執行
3. 資料來自 Trip.com，準確性高

### 下一步
1. 完成 Playwright 自動化
2. 測試完整搜尋流程
3. 建立 MVP 程式碼

---

## 相關文件
- [華航替代方案研究](華航替代方案研究-2026-08-20.md)
- [華航票價爬蟲](華航票價爬蟲-2026-08-20.md)
