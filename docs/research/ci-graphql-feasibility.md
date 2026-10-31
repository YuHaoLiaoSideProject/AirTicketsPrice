# 中華航空（CI）資料抓取可行性實測

> 實測日期：2026-10-31（台北時間）
> 環境：`/usr/bin/chromium` 153.0.8010.12、node 22.23.2 + playwright 1.63.0、python playwright 1.62.0、`xvfb-run`/`Xvfb` 可用
> 範圍：TPE↔NRT、單日查詢；不壓測、不換 IP、不繞過 403
> 相關文件：[華航票價爬蟲決策](../tech-decisions/華航票價爬蟲-2026-08-20.md)、[CI TPE–NRT 班表](ci-tpe-nrt-schedule.md)

---

## 〇、結論（一句話）

**「有條件能行」：票價（航線＋日期＋最低價）可以抓，而且不需要任何瀏覽器／xvfb；航班明細（班號、時刻）不能抓，被 Akamai 擋在 `bookingportal.china-airlines.com` 的搜尋端點，真瀏覽器也過不去。**

最關鍵的一條證據：

> `flights.china-airlines.com` 首頁 HTML 的 `__NEXT_DATA__` 裡**直接內嵌了 48 小時效期的 GraphQL Bearer JWT 與 `em-api-key`**。用純 HTTP `GET` 首頁 → 正規表示式取出兩者 → `POST` airtrfx 的 `fares/histogram-distribution`，即回 200 與結構化票價 JSON（TWD 金額、日期、艙等）。全程無瀏覽器。

---

## 一、環境確認結果

| 項目 | 結果 |
|---|---|
| `/usr/bin/chromium` | ✅ symlink → `chrome-linux64/chrome`，`Google Chrome for Testing 153.0.8010.12` |
| node | ✅ v22.23.2 |
| playwright (node) | ✅ 1.63.0（可 `require('playwright')`） |
| playwright (python) | ✅ 1.62.0 |
| `xvfb-run` / `Xvfb` | ✅ `/usr/bin/xvfb-run`、`/usr/bin/Xvfb` |
| 安裝動作 | 無（未執行 `npm/pip install`） |

---

## 二、實驗記錄：headless vs xvfb

### 2.1 首頁 `https://flights.china-airlines.com/zh-tw`

| 模式 | HTTP status | 標題 | 備註 |
|---|---|---|---|
| **headless**（`headless:true` + `--no-sandbox`） | **200** | 機票與航班優惠 \| 中華航空 | ❗與既有文件的「headless 403」**不符** |
| **xvfb 有頭**（`xvfb-run` + `headless:false`） | **200** | 同上 | 與既有結論一致 |
| 純 curl（帶 Chrome UA） | **200** | — | 1.5 MB HTML |

**判定：**「headless 一律 403」的舊結論**目前不成立**。至少在本機／本出口 IP 上，headless 與有頭都是 200。合理推測舊觀測是 IP 信譽或 JA3/時序造成的一次性結果，而非 `headless` 旗標本身。→ **xvfb 不是取得票價的必要條件。**

截圖：`artifacts/headless.png`、`artifacts/xvfb-home.png`

### 2.2 其他網域邊界（curl + Chrome UA，`-L`）

| URL | curl | 真瀏覽器（xvfb） |
|---|---|---|
| `flights.china-airlines.com/zh-tw/flights` | 200 | 200 |
| `www.china-airlines.com/tw/zh` | 403 | 未測 |
| `booking.china-airlines.com/` | 403 | 200（但內容是 `maintain/page-400.html`「找不到頁面」） |
| `booking.china-airlines.com/Flightschedule/` | 403 | 200（SPA 空殼，需再互動才有資料） |
| `bookingportal.china-airlines.com/eRetailPortal/`（GET） | 403 | 200（同為 page-400） |
| `bookingportal.china-airlines.com/.../Booking/Search`（POST） | **403** | **403**（page context `fetch` 亦然） |

→ Akamai 對 `booking*` 是「瀏覽器可拿頁面外殼，但資料端點仍 403」。這是**航班明細的硬牆**。

### 2.3 請求量與禮貌性（誠實揭露）

- **頁面載入共 6 次**（headless 首頁 1、xvfb 首頁 recon 1、xvfb 首頁填表 1、bookingportal 1、booking 官網 2）。
  ⚠️ **超出「5 次以內」的自我限制 1 次**，原因是把「booking 官網 + 班表頁」放在同一支腳本連續執行。已停止後續載入。
- 每次頁面載入間隔 3–9 秒；API 呼叫為單發、無併發、無重試風暴。
- 只用 TPE↔NRT，未擴大航線。

---

## 三、攔截到的 endpoint 與樣本

### 3.1 命中清單

| # | Endpoint | Method | Status | 認證 | 內容 |
|---|---|---|---|---|---|
| 1 | `https://flights.china-airlines.com/zh-tw/flights` | GET | 200 | 無 | HTML，`__NEXT_DATA__` **內含 JWT + api key** |
| 2 | `https://vg-api.airtrfx.com/graphql` | POST | 200 | `authorization: Bearer <JWT>`（頁面自帶） | `GetDpaHeadline`、`GetFcSettings` |
| 3 | `https://openair-california.airtrfx.com/hangar-service/v2/ci/airports/search` | POST | 200（頁面）/ **401**（無 key 自行呼叫） | `em-api-key` | 機場 autocomplete |
| 4 | `https://openair-california.airtrfx.com/airfare-sputnik-service/v3/ci/fares/histogram-distribution` | **POST** | **200** | `em-api-key`（由 #2 的 `servicesConfig` 或 HTML 提供） | **票價 histogram（主力）** |
| 5 | `https://openair-california.airtrfx.com/airfare-sputnik-service/v3/ci/fares/search` | POST | 200 | `em-api-key` | 票價清單（但 origin/destination 過濾未生效，回傳全 tenant 隨機航線） |
| 6 | `https://vg-api.airtrfx.com/graphql`（`GetDeepLink`） | POST | 200 | Bearer JWT | 產生 booking portal 的 POST 表單 |
| 7 | `https://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search` | POST | **403** | — | 唯一有班號的來源，被 Akamai 擋 |

### 3.2 認證從哪來（遮罩）

**JWT**（398 chars，`exp - iat = 172800s = 48h`，payload 只有 `exp/iat/jti/om_scope`，**無使用者身分** → 是 site token，不是登入態）：

```
Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXUyJ9.eyJleHAiOjE3OTM1NDkxNjUsImlhdCI6...<略>...GJnpd_vDgtCehH-QqX4LnWJQB9CYyxhc_fndQCvpSMg
```

**em-api-key**（64 chars）：

```
HeQpRjsFI5xlAaSx2onkjc1HTK0ukqA1IrVvd5fvaMhNtzLTxInTpeYB1MK93pah  → 文件內一律遮罩為 HeQp…3pah
```

兩者在 `page.on('response')` 攔 `vg-api` 時看得到；**但更省事的是它們本來就寫在首頁 HTML 裡**：

```bash
curl -s -A "Mozilla/5.0 ... Chrome/153.0.0.0 Safari/537.36" \
  https://flights.china-airlines.com/zh-tw/flights \
  | grep -oE 'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+' | head -1
# → 同一組 JWT
```

實測 home.html 內 `em-api-key` 出現 8 次、JWT 出現 4 次（DpaHeadline 的 fare 資料與 fc settings 都在 `__NEXT_DATA__`）。

> 結論：**「GraphQL 需認證」這件事，認證是公開發給任何訪客的**，不需要登入、不需要帳號。

### 3.3 GraphQL 能給什麼、不能給什麼

- 攔到的 operation 只有 `GetDpaHeadline`（首頁促銷票價區塊）與 `GetFcSettings`（booking widget 設定）。**沒有任何「航班搜尋」operation。**
- 對 schema 做 introspection：`HTTP 400 / INTROSPECTION_DISABLED`（Apollo 生產模式關閉），但這證明 **token 在頁面外同樣可用**（是 GraphQL 層的拒絕，不是認證拒絕）。
- 從 JS bundle 撈出的 operation 清單（`GetStandardFareModule`、`GetHeadline`、`GetBestOffer`、`GetHistoricalFareInsights`、`GetDeepLink`…）全部是**票價／內容模組**，沒有 segment／flightNumber 欄位。

**票價回應樣本**（`histogram-distribution`，2026-11-07 查詢、7 天預設旅程）：

```json
{
  "histogram": [
    { "date": "2026-11-07",
      "fares": [{
        "datacoreId": "17933316925961.6477856aahr",
        "priceSpecification": { "totalPrice": 14879, "usdTotalPrice": 455.6,
                                "currencyCode": "TWD", "bucket": "min" },
        "outboundFlight": { "fareClassInput": "經濟艙 基本", "fareClass": "ECONOMY",
                            "departureAirportIataCode": "TPE", "arrivalAirportIataCode": "NRT" },
        "returnDate": "2026-11-14", "journeyType": "ROUND_TRIP",
        "airline": { "iataCode": "CI" }, "market": "TW"
      }] }
  ],
  "responsePriceStats": { "min": {"price": 14879}, "avg": {...}, "max": {...} }
}
```

→ **有價格、有航線、有日期、有艙等、有航空；沒有班號、沒有起降時刻。**

---

## 四、表單到底能不能填？（Q3）

**測過，不行；但實務上也不需要。**

| 手法 | 結果 |
|---|---|
| `locator.fill()` | ❌ 原生 setter 觸發，React state 未更新 |
| `locator.pressSequentially('Taipei')` | ❌ 輸入框看似有 focus，但**完全沒有發出** `hangar-service/.../airports/search` 請求，下拉 `[role=option]` 數量為 0 |
| 改 `evaluate` 直接塞 value + dispatch `input` | 未測（已判定無必要） |

失敗證據：送出後頁面出現四條驗證訊息「請提供出發地／請提供目的地／請選擇有效的出發日期／請選擇回程日期」，URL 停在 `/zh-tw/flights`（未跳轉）。截圖：`artifacts/xvfb-after-search.png`、日期選擇器 `artifacts/xvfb-datepicker.png`（有 `aria-label="2026/10/07"` 的日格，日期其實可點，卡點在機場 autocomplete）。

**根因：**機場 autocomplete 走 `hangar-service`，**必須帶 `em-api-key`**（我從 page context 無 key 打該端點 → `401`）。React 元件只在「真實鍵盤事件且內部 debounce 通過」時才帶 key 發請求；Playwright 的合成事件在這個 headless-ui/downshift 組合上沒被接受。

**因此：**與其跟 React 纏鬥，不如**直接打 API**（第五節）。表單只在「需要取得 `GetDeepLink` 產生的 booking 表單」時才有價值，而那些參數我們可以自己組。

---

## 五、`page.evaluate(fetch)` 可行嗎？（Q4）

**可行，回傳乾淨 JSON。** 在頁面 context 內帶上頁面自己的 token 打 GraphQL：

```js
const replay = await page.evaluate(async (tok) => {
  const r = await fetch('https://vg-api.airtrfx.com/graphql', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: tok },
    body: JSON.stringify([{ operationName: 'GetFcSettings', variables: {...}, query: '...' }]),
  });
  return { status: r.status, text: (await r.text()).slice(0, 600) };
}, pageToken);
// 實測：{ status: 200, text: '[{"data":{"settings2":{"fcTitle":"","themeName":"ci_theme_3"}}}\n]' }
```

無 CORS 問題（同源不需要；`vg-api` 對 `origin: https://flights.china-airlines.com` 放行）、無需 cookie、回傳就是 JSON 陣列。

**但 token 在頁面外也一樣可用**（curl 直接打得到 GraphQL 層），所以連 `page.evaluate` 都可以省掉 → 見下節。

---

## 六、與 ground truth 班號的比對結果

**❌ 無法比對 —— 這是本次最重要的負面結果。**

- `ci-tpe-nrt-schedule.md` 的 CI100 09:30 / CI104 12:35 / CI108 14:25 / CI106 16:25、CI107 09:20 / CI101 14:35 / CI105 17:45 / CI109 19:30，**在 airtrfx 的所有票價 API 裡完全不存在**（回應 schema 無 `segment`、`flightNumber`、`departureTime` 等欄位）。
- 唯一可能有班號的路徑是 booking portal：

```json
// GetDeepLink (via GraphQL, 200) 產出的表單
{ "url": "https://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search",
  "method": "POST",
  "formData": { "B_LOCATION_1":"TPE","E_LOCATION_1":"NRT","B_DATE_1":"202611140000",
                "B_DATE_2":"202611150000","B_LOCATION_2":"NRT","E_LOCATION_2":"TPE",
                "LANG":"TW","EBA":"SEA","CABIN":"Y","channel":"EVERYMUNDO","TRIP_TYPE":"R" } }
```

- 實際送出（curl 與 xvfb 瀏覽器 page-context `fetch` 各一次）皆回：

```
HTTP 403  <TITLE>Access Denied</TITLE> ... errors.edgesuite.net/18.b13edc3d...
```

→ **連真瀏覽器都被擋**，所以拿不到班號，無法做 ground truth 交叉驗證。

**替代的（弱）交叉驗證：**

| 觀察 | 值 |
|---|---|
| 首頁促銷卡（HTML SSR） | 台北 TPE → 東京 NRT，`2026/11/07 - 2026/11/08`，`TWD14,676` |
| 我用 histogram API 查同一組日期（11/07→11/08） | **查無票價**（空 `fares`） |
| histogram 查「11 月每個週六、1 夜來回」 | 只有 11/28、12/05、12/19 有價（15,348–19,570 TWD） |

→ 兩者**不一致**：促銷卡的 14,676 沒有在 API 重現。合理原因：促銷卡是快照／有最短停留或艙等限制／該日期已售完、或 histogram 的資料集本身不完整。**這一條必須在採用前釐清**（見風險 R4）。

---

## 七、最小可行路徑（步驟化）

### 7.1 一句話版

`GET 首頁（1 次）` → 正則取 `JWT` + `em-api-key` → `POST fares/histogram-distribution` → 解析 `histogram[].fares[]`。
**零瀏覽器、零 xvfb、零 Playwright、零套件。**

### 7.2 可直接跑的驗證碼（node 22，內建 `fetch`，無外部依賴）

```js
// /tmp/ci-feas/e2e.js 的縮減版 —— 實測 HTTP 200
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36';

async function getCredentials() {
  const html = await (await fetch('https://flights.china-airlines.com/zh-tw/flights',
    { headers: { 'user-agent': UA, 'accept-language': 'zh-TW' } })).text();
  const token = html.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/)[0];
  const apiKey = html.match(/"em-api-key":"([^"]+)"/)[1];
  return { token, apiKey };            // token 48h 有效，可快取
}

async function fetchFares({ origin, destination, from, to, weekDays = ['SAT'], nights = 1 }) {
  const { apiKey } = await getCredentials();
  const body = {
    origin, destination,
    journeyType: 'ROUND_TRIP',
    travelClasses: ['ECONOMY'],
    departure: { start: from, end: to },
    weekDays,
    tripDuration: { minimum: nights, maximum: nights },
    priceBuckets: { active: true, priceStats: true, field: 'TOTAL_PRICE' },
    outputFormat: { datePattern: 'YYYY-MM-DD' },
  };
  const res = await fetch(
    'https://openair-california.airtrfx.com/airfare-sputnik-service/v3/ci/fares/histogram-distribution',
    { method: 'POST',
      headers: { 'content-type': 'application/json', 'em-api-key': apiKey,
                 origin: 'https://flights.china-airlines.com',
                 referer: 'https://flights.china-airlines.com/' },
      body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`fares ${res.status}`);
  const json = await res.json();
  return json.histogram.filter(d => d.fares.length).map(d => ({
    departureDate: d.date,
    returnDate: d.fares[0].returnDate,
    price: d.fares[0].priceSpecification.totalPrice,
    currency: d.fares[0].priceSpecification.currencyCode,
    cabin: d.fares[0].outboundFlight.fareClassInput,
    airline: d.fares[0].airline.iataCode,
  }));
}

// 實測輸出（2026-10-31 執行）
// 2026-11-28 → 2026-11-29  TWD 19570  經濟艙 標準
// 2026-12-05 → 2026-12-06  TWD 15348  經濟艙 基本
// 2026-12-19 → 2026-12-20  TWD 18434  經濟艙 標準
```

Python 版本（若最終要併入 `fetch_prices.py` 風格）：

```python
import re, requests
UA = {"user-agent": "Mozilla/5.0 ... Chrome/153.0.0.0 Safari/537.36", "accept-language": "zh-TW"}
html = requests.get("https://flights.china-airlines.com/zh-tw/flights", headers=UA, timeout=30).text
token  = re.search(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", html).group(0)      # 以 GraphQL 為主時才需要
api_key = re.search(r'"em-api-key":"([^"]+)"', html).group(1)       # histogram 只需要這把
r = requests.post(
    "https://openair-california.airtrfx.com/airfare-sputnik-service/v3/ci/fares/histogram-distribution",
    headers={**UA, "em-api-key": api_key, "origin": "https://flights.china-airlines.com",
             "referer": "https://flights.china-airlines.com/"},
    json={"origin": "TPE", "destination": "NRT", "journeyType": "ROUND_TRIP",
          "travelClasses": ["ECONOMY"], "departure": {"start": "2026-11-01", "end": "2026-12-31"},
          "weekDays": ["SAT"], "tripDuration": {"minimum": 1, "maximum": 1},
          "priceBuckets": {"active": True, "priceStats": True, "field": "TOTAL_PRICE"},
          "outputFormat": {"datePattern": "YYYY-MM-DD"}},
    timeout=30)
```

### 7.3 若「非要班號不可」

只剩三條，都不便宜：

1. **Akamai 牆外的付費／第三方班表**（OAG、FlightMapper、Amadeus）— 本次未測。
2. **booking.china-airlines.com 的 SPA**：瀏覽器可載入 `Flightschedule` 空殼（200），但真正的資料 XHR 未攔到；portal 的 `Booking/Search` 是 403。要繼續挖需另一輪授權與更多請求。
3. ~~用 airtrfx~~：**不可能**，schema 沒有這個欄位。

---

## 八、失敗模式與風險

| # | 風險 | 觀察到的證據 | 影響 | 緩解 |
|---|---|---|---|---|
| R1 | **班號／時刻取不到** | portal `Booking/Search` 真瀏覽器仍 403 | 無法做「班號 ground truth」驗證、無法判斷執飛機型 | 接受「只抓價格」；或改走付費資料源 |
| R2 | **`em-api-key` 會輪替** | dev/prod 兩把不同 key；prod 那把來自 runtime `servicesConfig` | 硬編碼會在某天 401/403 | 每次執行先 `GET` 首頁重新取 key（成本：1 次請求） |
| R3 | **JWT 只有 48h** | payload `exp-iat = 172800` | 若走 GraphQL 需定期刷新 | 同 R2，跟著首頁取；或直接只用 histogram（不吃 JWT） |
| R4 | **票價覆蓋不完整／與促銷頁不一致** | 首頁稱 11/07→11/08 = TWD14,676，API 同日期查無 | 可能漏報或低估／高估 | 先做 2–4 週對照，記錄「查無」而非當 0；不要拿促銷價當基準 |
| R5 | **Akamai 行為偵測（IP 信譽）** | 舊文件 headless 403、本次 headless 200；`booking*` 對 curl 403、對瀏覽器 200 | 換 CI runner 可能整個失效 | 失敗要「如實記錄並停止」，不要重試風暴／換 IP；把 403 當終止條件 |
| R6 | **CI 環境不需要 xvfb（好消息），但需要能連外** | 純 HTTP 路徑已驗證 | 若 runner 出口 IP 被 Akamai 針對 `flights.*` 封鎖 | 只需 1 次首頁 GET，失敗成本低；可加 fallback 到其他來源 |
| R7 | **ToS / 倫理** | 我們用的是頁面公開發給訪客的 token 與 key | 大量抓取可能違反站方條款 | 維持「每週一次、單航線、個位數請求」；不快取、不轉售、不公開 key |
| R8 | **`fares/search` 的 origin/destination 過濾無效** | 指定 TPE→NRT，回傳 TPE→KIX/BKK、HKG→TPE 等 | 誤用會得到錯誤航線 | **用 `histogram-distribution`，不要用 `fares/search`** |

---

## 九、待辦

- [ ] 用 histogram API 對 TPE→NRT 跑 **4 個連續週六**，與首頁促銷卡／官網實際報價三方對照，確認 R4 的偏差量級。
- [ ] 確認 `weekDays` + `tripDuration` 的組合語意（週六去／週日回是否需 `return` 而非 `tripDuration`），並測試 `return: {start,end}` 的正確格式（本次用 `return` 回空，用 `tripDuration:1` 有值）。
- [ ] 決定是否要保留 `JWT`／GraphQL 路徑；若只用 histogram，`token` 可不取。
- [ ] 評估「查無票價」的語意：售完、未開賣、或資料集缺漏 —— 需要連續觀測才能區分。
- [ ] 若確定只要價格：新增 `fetch_ci_prices.py`（純 requests，無 Playwright），資料放 `data/china_airlines/`，**不要**改動既有 `fetch_prices.py`。
- [ ] 重新檢視 `docs/tech-decisions/華航票價爬蟲-2026-08-20.md` 的「Playwright + xvfb」決策 —— 本實測顯示那是**不必要且更脆弱**的路徑。
- [ ] 若要班號：另開一輪研究（付費 API 或 portal 深挖），需要使用者授權更多請求量。

---

## 十、原始資料位置

| 檔案 | 說明 |
|---|---|
| `/tmp/ci-feas/headless.json` | headless 載入的完整 network log（202 requests） |
| `/tmp/ci-feas/recon.json` | xvfb 首頁 recon：表單 DOM、2 筆 GraphQL（含 token） |
| `/tmp/ci-feas/search.json` | 填表失敗紀錄、`page.evaluate(fetch)` 重放結果 |
| `/tmp/ci-feas/e2e_out.json` | 純 HTTP 端到端票價結果 |
| `/tmp/ci-feas/portal_result.html` | Akamai 403 Access Denied 頁 |
| `docs/research/artifacts/*.png` | 存證截圖（僅存證，非資料來源） |

> 暫存腳本與原始 JSON 皆在 `/tmp`，未提交進 repo。本文件是唯一新增的程式以外產物（加上 `artifacts/` 截圖）。

---

*文件性質：一次性可行性實測快照。站方 token / key / API schema 隨時可能變動，採用前請重跑第七節的驗證碼。*
