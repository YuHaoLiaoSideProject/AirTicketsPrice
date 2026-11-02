# 華航 flight-level 票價：能不能用純 HTTP（不開瀏覽器）拿到？

> 實測日期：2026-11-01（台北時間 12:0x–12:3x）
> 環境：Python 3.13 + `requests` 2.32.3；Playwright 1.62（僅用 2 次，見 §三、§四）；`/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`；`xvfb-run`；**單一住宅出口（HiNet AS3462）、無代理、無換 IP、無指紋偽造、無 stealth patch**
> 範圍：**只查 TPE→NRT**，日期 2026-11-20→11-24（外加 11-21→11-25、11-22→11-26 各一次，用於驗證「同一個 cookie 能不能換日期」）
> 前置文件：[ci-503-root-cause.md](ci-503-root-cause.md)（§五 #1 待驗證項）、[ci-bot-detection-diagnosis.md](ci-bot-detection-diagnosis.md)、[ci-graphql-feasibility.md](ci-graphql-feasibility.md)、[ci-coverage-probe.md](ci-coverage-probe.md)
> 新增證據：`artifacts/ci-des-portal-fares-from-http-form-20261101.png`
> **未修改任何既有檔案**；暫存腳本全部在 `/tmp/cidl/`，未進 repo。

---

## 〇、結論（一句話）

**「純 HTTP」不可行，「需混合」可行且成本極低 —— 每次查詢只要 2 個 HTTP 請求（< 2 秒），但需要一個「cookie 武裝」步驟：用瀏覽器過一次 Imperva/DataDome，把 cookie 與 `x-d-token` 取出來重用；之後完全不必再開瀏覽器就能拿到逐班票價。**

三句話版本：

1. **deeplinks API 打得到（HTTP 200），但不是這條路**：它回的是「目標 URL + 空 `formData`」，**不回 `emcid`、不回簽章**；而且真實 UI 流程**根本沒有呼叫它**（完整 UI 流程的 134 筆網路請求裡，deeplink-service 出現 0 次）。
2. **真正的門在後面**：bookingportal 的表單 POST **純 HTTP 就 200**（不需要 `emcid`、不需要 cookie），回應裡直接附一個自動送出的 `<form action="https://des-portal.china-airlines.com/booking?lang=zh-Hant">`，內含 `search` / `portalFacts` / **`privateFacts`（JWE）**。把這個 form 用 HTTP POST 出去，**沒有 cookie 會撞 Imperva「Pardon Our Interruption」**；**帶上瀏覽器拿到的 cookie 則 200**。
3. **票價 API 是 Amadeus 型 REST（`api-des.china-airlines.com`）**：`POST /v1/security/oauth2/token/initialization`（client_id/secret 就寫在前端）+ `POST /v2/search/air-bounds` → **逐班 × 艙等 × 艙等代碼 × 剩位 × 含稅總價**的乾淨 JSON。**純 `requests` 100% 打得動**（實測 3 個不同日期都 200），而且 **`fact` 可以自己組**（換日期不必回頭找 bookingportal）。

> 交叉驗證：純 HTTP 拿到的 `SEG-CI100-...-0930` 經濟艙 **TWD 17,107**、`SEG-CI104-...-1235` **TWD 12,451**，與 10/31 真實 UI 截圖上的 CI100／CI104 價格**完全一致**。

---

## 一、Q1：deeplinks API 能否純 HTTP 打通？

### 1.1 規格（從 `__NEXT_DATA__` 直讀，非猜測）

`GET https://flights.china-airlines.com/zh-tw`（302 → `/zh-tw/flights`，**200**、1.5 MB）後，HTML 內可直接取出完整設定：

```json
"deeplink": {
  "trackingParameters": { "utm_source": "", "utm_medium": "", …, "channel": "", "networkName": "", "darksite": "" },
  "config": {
    "endPoint": "https://openair-california.airtrfx.com/deeplink-service/v2/%%airline_code%%/deeplinks",
    "options": {},
    "axiosConfig": { "headers": { "EM-API-Key": "<與 histogram 同一把 64 字元公開 key，遮罩為 HeQp…3pah>" } }
  }
}
```

| 項目 | 值 |
|---|---|
| Method / URL | `POST https://openair-california.airtrfx.com/deeplink-service/v2/ci/deeplinks` |
| 必要 header | `EM-API-Key: HeQp…3pah`、`content-type: application/json`（另加 `origin`/`referer: https://flights.china-airlines.com` 較自然） |
| 認證 | **無需登入**，key 公開內嵌於頁面（與 histogram 同一把） |

### 1.2 實測結果

| 請求 | payload | status | 回應 |
|---|---|---|---|
| #2 | `{"site":"ci","origin":"TPE","destination":"NRT","journeyType":"rt","departureDate":"2026-11-20","returnDate":"2026-11-24","adults":1,"travelClass":"ECONOMY","language":"zh-TW","siteEdition":"zh-tw"}` | **200** | `{"id":null,"url":"https://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search","method":"POST","formData":{},"headers":null}` |
| #3 | 改成 portal 原生欄名（`B_LOCATION_1`/`E_LOCATION_1`/`B_DATE_1`/`TRIP_TYPE`/`CABIN`/`LANG`/`EBA`/`channel`…） | **200** | **同上，`formData` 仍是空的** |

**判定：能打通，但打不通「我們要的東西」。**

- 回應**沒有 `emcid`、沒有簽章、沒有可用 deep link**；它只是把「目標端點」回給你，`formData` 需要一份我們沒能命中的 payload schema。
- **更重要的是**：真實 UI 流程**沒有呼叫這個 API**（見下）。所以就算把 payload 逆向出來，也只是繞遠路。

### 1.3 反證：真實 UI 沒有用 deeplinks，而 `emcid` 也不是必要的

用 10/31 那次完整 UI 流程的網路日誌（`/tmp/ci503_run5.json`，134 筆請求）交叉檢查：

- `deeplink-service` 出現 **0** 次、`securitytrfx`/`em-frame` **0** 次。
- 送出查詢時的請求是 **document POST**：
  `POST https://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search?emcid=T-8jVrGrF9u&_gl=…`
  body（214 B，**完整 19 欄位**，不是先前那份只有 5 欄位的裸 POST）：
  ```
  B_LOCATION_1=TPE&E_LOCATION_1=NRT&B_DATE_1=202611200000&ADULTS=1&CHILDS=0&INFANTS=0
  &LABORS=0&STUDENTS=0&LANG=TW&EBA=SEA&CABIN=Y&channel=EVERYMUNDO&TRIP_TYPE=R
  &B_DATE_2=202611240000&B_LOCATION_2=NRT&E_LOCATION_2=TPE
  ```
- **關鍵新發現（推翻 10/31 文件的假設）**：`emcid` 其實**不是必要條件**。本次用**同樣的 19 欄位 body、拿掉 `emcid`、不帶任何 cookie** 送出 → **HTTP 200**，且回應內容與帶 `emcid` 那次**同樣有效**（見 §二 第 1 步）。
  → 10/31 的 503 不是「缺 emcid」，而是「那份只有 5 欄位的 body + 舊協定形狀」（與 `ci-503-root-cause.md` 的判定方向一致，此處把 `emcid` 這一項排除）。

---

## 二、Q2：拿到「deep link / 表單」之後，純 HTTP 打 bookingportal 可行嗎？

### 2.1 真正的流程（實測逐段，附 status）

```
[HTTP]  ① GET  flights.china-airlines.com/zh-tw                    200  取 EM-API-Key（本輪其實可以省略）
[HTTP]  ② POST bookingportal …/Booking/Search  (19 欄位, 無 emcid)  200  回一份自動送出的 <form>，內含 privateFacts(JWE)
[HTTP]  ③ POST des-portal …/booking?lang=zh-Hant                    200  ✗ = Imperva「Pardon Our Interruption」（無 cookie）
[瀏覽器]③' 同一份 form 由瀏覽器送出                                 200  ✓ Imperva/DataDome 自行清關 → Angular 前台
[HTTP]  ④ POST api-des.china-airlines.com/v1/security/oauth2/token/initialization   200  → Bearer
[HTTP]  ⑤ POST api-des.china-airlines.com/v2/search/air-bounds                     200  → 逐班票價 JSON
```

### 2.2 逐步記錄

| # | 請求 | 環境 | status | 關鍵回應 |
|---|---|---|---|---|
| 4 | `POST bookingportal …/Booking/Search?emcid=T-8jVrGrF9u`（19 欄位） | requests，無 cookie | **200** | 7,863 B HTML，**無** Imperva、**無** 挑戰。`Set-Cookie: ASP.NET_SessionId, calRefxLan, CCNETsession, _abck~-1~, bm_mi, bm_sz`。內含 `<form name='book' action='https://des-portal…/booking?lang=zh-Hant' method='post'>` |
| 5 | `POST des-portal /booking`（form 的 5 個 hidden field） | requests，**無** cookie | **200** | `<noscript><title>Pardon Our Interruption</title>` = **Imperva 過場頁**；`Set-Cookie: incap-resubmit-token(Max-Age=60), visid_incap_*, incap_ses_*`；`X-Iinfo: …` |
| 6 | 同上（換成瀏覽器拿到的 cookie） | requests，**有** cookie | **200** | 139 KB Angular 前台 HTML，**無** Imperva；`Set-Cookie` 更新 `prxCookie / visid_incap / nlbi / incap_ses / datadome`。此頁為 CSR 骨架（**票價不在 HTML 內**） |
| — | `POST api-des …/v2/search/air-bounds` | requests，**無** cookie（僅 Bearer + facts） | **403** | `{"url":"https://geo.captcha-delivery.com/interstitial/?…"}` ← **DataDome 攔截** |
| 7 | `POST api-des …/token/initialization`（完整瀏覽器 header，含 `x-d-token`） | requests，有 cookie | **200** | `{"token_type":"Bearer","access_token":"…"}` |
| 8 | `POST api-des …/v2/search/air-bounds` | requests，有 cookie | **200** | 26 KB（gzip）逐班票價 JSON（第一次因照抄 `accept-encoding: br` 而拿到未解壓位元組，屬我方實作瑕疵，非站方問題） |
| 9 | 同上（精簡 header、拿掉 `x-d-token`） | requests | **403** | token init 直接 403 → **`x-d-token`/`x-spa`/`sec-fetch-*` 這組 header 是必要的** |
| 10–11 | token + air-bounds（完整 header、不送 `accept-encoding`） | requests | **200 / 200** | 4 個 bound、每個 3–6 個票種（見 §五） |
| 14–15 | token + air-bounds，**自己組 `fact` 換日期（11-21→11-25）** | requests | **200 / 200** | 5 個 bound，全部有價 |
| 17–18 | token + air-bounds，**再換日期（11-22→11-26），距取得 cookie 已約 25 分鐘** | requests | **200 / 200** | 5 個 bound，全部有價 → **cookie 至少可重用 25 分鐘** |
| 16 | `POST bookingportal …/Booking/Search`（**拿掉 emcid**、無 cookie） | requests | **200** | 與 #4 完全相同的 form → **`emcid` 非必要** |

### 2.3 「純 HTTP 過不了 Imperva」的判定（明確）

- Imperva 的過場頁（`Pardon Our Interruption` + `window.reeseSkipExpirationCheck` + `incap-resubmit-token`）需要**執行其 JS 並帶 `x-spa` / `x-incap-spa-info` 重新送出**；本輪**沒有**嘗試逆推或代解（也**不建議**）。
- 但實測證明**不需要解它**：讓**瀏覽器自己**跑一次（本輪用「把 HTTP 拿到的 form 丟進瀏覽器送出」，比完整 UI 流程快得多），cookie 就能被 `requests` **完整重用**，而且用於 `api-des` 的第 3 個不同日期仍 200。
- 同時 `api-des` 後面還有 **DataDome**（403 → `geo.captcha-delivery.com`）。同樣地，**沒有 cookie 就是硬牆**，有 cookie + `x-d-token` 就 200。

→ **結論：純 HTTP 不可行；「一次瀏覽器武裝 + 之後純 HTTP」可行。**

---

## 三、票價 API 規格（本次最有價值的產出）

### 3.1 兩段式：先取 token，再查 availability

**① 取 Bearer**

```http
POST https://api-des.china-airlines.com/v1/security/oauth2/token/initialization
content-type: application/x-www-form-urlencoded
origin: https://des-portal.china-airlines.com
referer: https://des-portal.china-airlines.com/
x-spa: 1
x-d-token: <從瀏覽器取得的 DataDome token，1246 字元>
x-incap-spa-info: <visid_incap_2933525=…; incap_ses_724_2933525=…>
cookie: <瀏覽器 cookie jar（含 reese84 / datadome / visid_incap / incap_ses）>

client_id=r7UuPcZTW4PK5XWFt2swQtoSC1HBGhxc
&client_secret=7il3cPVgbyttXH3a
&fact=<URL-encoded JSON: {"keyValuePairs":[{"key":"originLocationCode1","value":"TPE"},…]}>
&grant_type=client_credentials
```

回應（973 B）：

```json
{"type":"amadeusOAuth2Token","username":"CI_AE_RefXBooking",
 "application_name":"CI_AE_RefXBooking","client_id":"r7Uu…Ghxc",
 "token_type":"Bearer","access_token":"eyJhbGciOiJSUzI1NiIs…"}
```

> `client_id` / `client_secret` **就是前端明碼**（頁面 JS 直接送出），不需要帳號。`fact` 的 `keyValuePairs` 由**我自己組**（實測換日期仍 200），內容含 `pointOfSale=SEA`、`officeId=TPECI08AB`、`device=SITE-DESKTOP-EVERYMUNDO-SEA/TW`、`ip`、`originLocationCode1/2`、`departureDateTime1/2`。

**② 查逐班票價**

```http
POST https://api-des.china-airlines.com/v2/search/air-bounds
content-type: application/json
authorization: Bearer <上一步的 access_token>
ama-client-facts: <base64url({"alg":"none","typ":"JWT"})>.<base64url(fact 的 public claims)>. , <privateFacts JWE>
ama-client-ref: 081c1460-b2cc-434b-a4a5-7f891b7037e3:0
x-spa: 1
x-d-token: <同上>
x-incap-spa-info: <同上>
cookie: <同上>

{
  "commercialFareFamilies": ["ECONEW","PREMIUMECO","BZCLASS"],
  "itineraries": [
    {"originLocationCode":"TPE","destinationLocationCode":"NRT",
     "departureDateTime":"2026-11-21T00:00:00.000","isRequestedBound":true},
    {"originLocationCode":"NRT","destinationLocationCode":"TPE",
     "departureDateTime":"2026-11-25T00:00:00.000","isRequestedBound":false}
  ],
  "travelers": [{"passengerTypeCode":"ADT"}],
  "searchPreferences": {"showMilesPrice": false}
}
```

> `ama-client-facts` 的格式很怪但很關鍵：**`<header>.<payload>.` 逗號空白 `<privateFacts>`** —— 前半是 `alg:none` 的未簽章 JWT（public claims 與 `fact` 相同），後半就是 bookingportal 回傳的 `privateFacts`（RSA-OAEP-256 + A256GCM 的 JWE，1,198 字元）。`privateFacts` 在多次獨立請求間**完全相同**（#4 與瀏覽器那次 byte-identical），且**不受日期影響**（用它查 11-21／11-22 都成功）。

回應樣本（節錄，2026-11-20 去程）：

```json
{"data":{"airBoundGroups":[
 {"boundDetails":{"originLocationCode":"TPE","destinationLocationCode":"NRT","duration":11100,
   "segments":[{"flightId":"SEG-CI108-TPENRT-2026-11-20-1425"}]},
  "airBounds":[
   {"airBoundId":"BC1-2-OFR-853573898488525-1-1-1","fareFamilyCode":"TWSHECB","isCheapestOffer":true,
    "availabilityDetails":[{"flightId":"SEG-CI108-TPENRT-2026-11-20-1425",
      "cabin":"eco","bookingClass":"N","statusCode":"HK","quota":3}],
    "fareInfos":[{"fareType":"unifares","fareClass":"NLNF","travelerIds":["ADT-1"]}],
    "prices":{"totalPrices":[{"base":4577,"total":7085,"currencyCode":"TWD",
      "taxes":[{"value":1758,"currencyCode":"TWD","code":"YQYR"}],"totalTaxes":2508}]}},
   {"fareFamilyCode":"TWSHECS", … "total":10171 …},
   {"fareFamilyCode":"TWSHECF", … "total":14972 …},
   {"fareFamilyCode":"TWSHBZB", … "total":17298, "cabin":"business","bookingClass":"D" …}]}]},
 "meta":{"office":{"officeId":"TPECI08AB"}},
 "dictionaries":{"location":{"TPE":{…"airportName":"TAIWAN TAOYUAN INTL"…},"NRT":{…}}}}
```

→ 這就是先前只有 bookingportal 能給的東西：**flightId（含班號/日期/起飛時刻）× 艙等 × 艙等代碼 × `quota`（剩位）× 含稅總價 × 稅金明細**。

**③ 另一個可用端點（瀏覽器內看到，本輪未用 HTTP 重放）**

`POST /v2/search/air-calendars` → `{"data":[{"departureDate":"2026-11-17","returnDate":"2026-11-24","prices":{…total 14691…}}]}` = 票價日曆（與 histogram 同一種資訊，但這是官方前台的即時版本）。

---

## 四、Q3：最終架構建議（排程友善）

### 4.1 請求數與瀏覽器啟動次數

| 情境 | HTTP 請求 | 瀏覽器啟動 | 實測耗時 |
|---|---|---|---|
| **冷啟動（武裝 cookie）** | 1（bookingportal form）+ 1（把 form 送進瀏覽器）＝ 頁面 2 次導航 | **1 次**（~11 秒；本輪 `flow_c` 6.6 秒就看到票價 DOM） | ~11 s |
| **每次查詢（暖）** | **2**（token + air-bounds） | **0** | **< 2 s** |
| 每天 N 組日期 | 2N（可共用 cookie） | 0 | — |
| cookie 過期後 | 同上 + 重新武裝 | 1 次 | ~11 s |

> 對照 10/31 的完整 UI 流程：~40 秒 + 4 次主框架導航。**本方案每次查詢成本降 1 個數量級，且不需要 xvfb 常駐。**

### 4.2 快取策略

| 項目 | 可快取？ | 觀察到的有效期 | 建議 |
|---|---|---|---|
| `EM-API-Key`（airtrfx） | 可 | 每次頁面載入都一樣；會輪替 | 只在用到 airtrfx 時才取；**本方案其實不需要它** |
| `privateFacts`（JWE） | **可**（多次請求 byte-identical、跨日期可用） | ≥25 分鐘實測；未知上限 | 存本地檔，失敗（401/403）才回 bookingportal 換一份 |
| Bearer `access_token` | 可，但便宜 | 未測 TTL | 每次執行取一次即可（1 個請求），不必冒過期風險 |
| `reese84` / `datadome` / `visid_incap` / `incap_ses` | **必須** | **≥25 分鐘實測**；`datadome`/`visid_incap` cookie 標示 1 年但**信任不會活那麼久**；`incap-resubmit-token` 只有 60 秒 | 存 cookie jar；**每輪排程先試 1 次輕量呼叫**，403 就重新武裝 |
| `x-d-token` | 可（同一值連用 3 個日期皆 200） | 未知 | 與 cookie 一起存；失效時必須重跑瀏覽器 |

### 4.3 延遲與重試（沿用專案慣例）

- **請求間隔 2–6 秒**（與星宇 `fetch_prices.py` 一致；華航 probe 目前 2–4 秒）。本方案的 2 個請求屬於同一組查詢，可連發；**不同日期之間**間隔 2–6 秒。
- **重試 3 次、退避 3 s / 6 s**（同 `config.RETRY_BACKOFF` 慣例）。
- **403 / 429 = 終止條件**（本次實測無 cookie 時的 403 就是 DataDome 捕獲頁）：**立刻停止整條路徑、記錄、等下一個排程週期**；不要在同一次執行內重試到觸發 captcha。
- Imperva/DataDome 的行為會在**請求量上升時變嚴**（10/31 文件已註明）。建議**每日一輪、單輪 ≤ 8 組日期**，並把「被挑戰」當成正常事件（重新武裝），而不是異常。

### 4.4 建議的模組切法（不在此實作，僅建議）

```
ci_session.json   = { cookies[], x_d_token, x_incap_spa_info, private_facts, saved_at }
arm_session()     -> 用 Playwright 送一次 bookingportal form，落地 ci_session.json   # 只在失效時跑
query(route,dates) -> token + air-bounds（純 requests）                              # 排程主體
```
驗證用最小測試：**開跑時先打一次 `air-bounds` 的輕量版本；403 → 重新武裝再繼續。**

---

## 五、覆蓋率觀察：能不能解掉 histogram 的 38%？

**可以，而且樣本內是 100%。**

| 日期（去程） | 抓到航班數 | 每個航班的票種數 | 有價比例 |
|---|---|---|---|
| 2026-11-20 | 4（CI100 / CI104 / CI106 / CI108） | 3–6 | **4/4 = 100%** |
| 2026-11-21 | 5（+CI9902 共掛） | 1–6 | **5/5 = 100%** |
| 2026-11-22 | 5 | 3–6 | **5/5 = 100%** |

- 每個航班都有 `cabin`（eco/business）、`bookingClass`、**`quota`（剩位）**、含稅總價與稅金拆解。
- 與真實 UI 截圖比對：CI100 經濟艙 **TWD 17,107**、CI104 **TWD 12,451** —— **完全一致**（10/31 截圖同值）。CI104 商務 18,777、CI100 商務 27,631 亦一致。
- 對照 `ci-coverage-probe.md` 的 histogram：**只有 38% 的日期有價、且完全沒有班號**。`air-bounds` **逐班都有價**，等於同時解決「覆蓋率」與「flight-level」兩個問題。

**注意（語意）**：`quota` 是查詢當下的剩位（`quota:1` 就是「剩 1 個席位」）。這代表「現在買得到」，資料是即時值而非快照 —— 對產品的「低價提醒」是加分，但**重跑必然會有小幅跳動**（同日不同艙等 12,451 vs 12,450 這種 ±1 已在 10/31／本次各出現一次）。

---

## 六、風險

| # | 風險 | 說明 | 緩解 |
|---|---|---|---|
| R1 | **ToS / 存取控制** | 這條路用的是官網前台自己的 API、刊登在前端的 `client_id/secret`、以及**必須由瀏覽器通過的** Imperva/DataDome cookie。把它做成排程 = **持續性自動化存取**，與本輪「一次性診斷」性質不同；自行取用後端 B2B 端點（`api-des` / Amadeus 型 `/v2/search/*`）**很可能違反服務條款**。 | 由專案方明確決定並承擔；若要做成產品，商業授權（OAG/Amadeus/Travelport）仍是唯一永續解。**低頻、單航線、不快取轉售**是最起碼的自我約束。 |
| R2 | **DataDome 升級** | 無 cookie → 403 + captcha；觀測到 `ajaxListenerPath:["https://api-des.china-airlines.com/"]`，代表它**專門在盯這支 API**。行為、`x-d-token` 格式、挑戰門檻都可能輪替 | 被挑戰就**停**、重新武裝；不嘗試解 captcha、不偽造指紋 |
| R3 | **cookie / `x-d-token` TTL 不確定** | 實測 **≥25 分鐘**可重用（含 3 個不同日期）；上限未測 | 每次執行前做一次探測呼叫；失效即重新武裝；把 TTL 當「樂觀值」設計 |
| R4 | **`privateFacts` 的性質** | 1,198 字元的 JWE，本輪跨請求 byte-identical、跨日期可用。若哪天改成 per-search 綁定，需每次回 bookingportal | 快取 + 失敗即重取（成本 1 個請求） |
| R5 | **前端明碼 secret 輪替** | `client_secret=7il3…` 來自 bundle，可能換版就變 | 換版即 401/403；用「取不到 token 就停下」處理，不暴力重試 |
| R6 | **價格語意** | `total` 已含稅（`base` + `totalTaxes`）、`pointOfSale=SEA`（海外 POS）——與台灣官網顯示的一致性**只在 CI100/CI104 兩點驗證過** | 上線前多做 10–20 點對照 |
| R7 | **回程 bound** | 本次 `air-bounds` 只回去程（`isRequestedBound:true` 那一腿），回程要另發一次 | 一趟來回 = 2 次 air-bounds |

---

## 七、誠實邊界（哪些沒測、為什麼）

**已測（有 status 與 JSON 佐證）**
1. deeplinks API：2 組 payload 各 1 次，皆 200、`formData` 空、**無 emcid**。
2. bookingportal `Booking/Search`：**19 欄位 + 無 emcid + 無 cookie → 200**（推翻 emcid 必要性）。
3. des-portal `/booking`：無 cookie → Imperva 過場頁；有 cookie → 200。
4. `api-des` token/initialization + `/v2/search/air-bounds`：**純 requests 200**，3 個不同日期（11-20、11-21、11-22）皆成功；換日期用**自組 `fact`**。
5. 無 cookie 直打 `api-des` → **403（DataDome captcha 轉址）**。
6. 價格與 10/31 UI 截圖交叉一致（CI100 17,107／CI104 12,451）。
7. cookie/x-d-token 重用 **≥25 分鐘**。

**沒測（不敢當結論）**
- **cookie / `x-d-token` / `privateFacts` 的真實 TTL 上限**：只觀測到 25 分鐘仍有效；沒有做「放到隔天」的測試。
- **冷 profile 是否能一次過**：兩次瀏覽器都沿用 `/tmp/ci503-profile`（含 10/31 的歷史 cookie）。**全新 profile 未測**。
- **更便宜的武裝方式**：本輪是「HTTP 拿 form → 瀏覽器送出」。**「瀏覽器只 GET des-portal 首頁就足以取得 cookie + x-d-token」未測**（若能，武裝步驟可再簡化）。
- **`/v2/search/air-calendars` 的 HTTP 重放**：只在瀏覽器內看到 200 與樣本，未用 requests 重打。
- **回程查詢（NRT→TPE 為 `isRequestedBound:true`）**：未測。
- **其他艙等／其他 POS／其他航線**：`commercialFareFamilies` 只用了頁面預設三種（`ECONEW/PREMIUMECO/BZCLASS`）；豪華經濟艙（PY）在樣本中未出現票種，未確認是「沒賣」還是「沒要求」。
- **`x-d-token` 是否綁 IP/UA**：本輪 UA 全程一致、IP 未變，無法區分。
- **Imperva/DataDome 在高頻下的升級行為**：未測，且**不應為了測試而加頻**。
- **`emcid` 由誰產生**：只確認「不需要」，沒有追到產生它的那支 JS（`securitytrfx` 在日誌裡也查無）。

**揭露**
- 我方主動請求 **17 次 HTTP** + **2 次瀏覽器流程**（每次約 2 次 document 導航 + 頁面自發的資產與 analytics beacon；Imperva 過場頁自身的 `_Incapsula_Resource`／sensor 子請求另計，未逐一計數）。
- 遇到的 403 共 2 次（#9 缺 header、#12 無 cookie 的 DataDome），皆**立即停止該變體**、未重試、未繞過。
- 只新增本文件與 1 張截圖；**未修改任何既有檔案**；腳本全部留在 `/tmp/cidl/`。

---

## 八、給使用者的一句話

> **「純 HTTP」不行，「混合」很便宜。** 一次瀏覽器（~11 秒）把 Imperva/DataDome 的 cookie 與 `x-d-token` 拿到手，之後**每次查詢只要 2 個 `requests`、< 2 秒**，就能從 `api-des.china-airlines.com/v2/search/air-bounds` 拿到**逐班 × 艙等 × 剩位 × 含稅價**的乾淨 JSON，而且**三個不同日期都是 100% 有價**（CI100 17,107、CI104 12,451，與官網截圖一模一樣）—— histogram 只有 38% 覆蓋的問題**可以直接解掉**。要不要做，已經不是技術問題，而是 **ToS／風險承擔**的決定。
