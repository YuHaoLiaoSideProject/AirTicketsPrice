# 中華航空自動化：替代來源與 bookingportal 403 診斷（實測）

> 實測日期：2026-10-31（台北時間 11:35–11:45）
> 環境：`requests` 2.32.3、python playwright 1.62.0 + `/root/.cache/ms-playwright/chromium-1243`（xvfb 有頭）
> 範圍：TPE↔NRT 單一航線；**總請求數 20 次（達自訂上限，已停止）**，每次間隔 2.5–4 秒
> 前置文件：[ci-graphql-feasibility.md](ci-graphql-feasibility.md)、[ci-coverage-probe.md](ci-coverage-probe.md)、[ci-tpe-nrt-schedule.md](ci-tpe-nrt-schedule.md)
> 新增證據檔：`artifacts/ci-403-akamai-body.html`、`artifacts/ci-portal-formpost.png`、`artifacts/ci-alternatives-evidence.json`

---

## 〇、結論（先講）

**「華航自動化完全無望」這個結論是錯的。** 無望的只有一件事：**bookingportal 的報價搜尋端點**。班表（班號＋起降時刻＋營運日）**可以穩定自動取得**，而且不需要任何瀏覽器。

| 項目 | 能自動化嗎 | 走哪個來源 | 成本 | 信心 |
|---|---|---|---|---|
| **班表**（班號／起降時刻／營運日／有效期間） | ✅ **能**，純 `requests` | `info.flightmapper.net/route/China_Airlines_CI_TPE_NRT`（＋反向 `..._CI_NRT_TPE`） | 2 個 GET／週，伺服器端渲染 HTML，需 1 個 parser（~60 行） | **高**（實測 200、資料與既有班表文件一致） |
| **班表（官方）** | ❌ 不能 | `booking.china-airlines.com/Flightschedule` | 頁面 200 但查詢需 **Cloudflare Turnstile** token | 高（HTML 內含 `hdnTurnstileToken`，實測無 token 的 POST 取不到任何航班） |
| **即時／實際營運狀態**（今日看板、近 3 日實際起降） | ✅ 能，純 `requests` | `proflightsearch.com/search-flights/TPE-NRT`（＋`/flight/CIxxx`） | 1–2 個 GET，HTML 解析（其 `/api/*` 反而 403） | 中高（可用，但有過期區塊的雜訊） |
| **票價（航線最低價）** | ✅ 有條件能（38% 覆蓋） | airtrfx `histogram-distribution`（見 ci-graphql-feasibility.md） | 1 首頁 GET + 1 API POST／輪 | 已知（38%） |
| **票價／艙等（報價搜尋、含班號的 combo）** | ❌ **不能**，且**真瀏覽器也不行** | `bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search` | Akamai 對「該路徑 + POST」固定回 403 | **高**（4 種客戶端全部 403，同一 Rule ID） |

**關於「用真瀏覽器操作查看」**：

- 若意思是「**用真瀏覽器去開華航訂票頁、親眼把數字看下來**」→ **可行**（那是人在操作，不是爬蟲）。本機的 403 是**機器指紋／規則**擋的，不是你的網路不通；真人開瀏覽器在一般網路環境下幾乎不受影響（此為推論，見 §B.5）。
- 若意思是「**用自動化的真瀏覽器去抓**」→ **不行**。實測中「真 Chromium + xvfb + 真 cookies + 真實表單導航」三種方式全部 403，且 Akamai Bot Manager 即使在真瀏覽器上也沒把我方標記為合法（`_abck` 始終 `~-1~`）。

---

## 一、問題 A：班表能不能自動化？（能）

### A.1 先找出既有文件實際用的來源

讀 `ci-tpe-nrt-schedule.md` 第五節「資料來源清單」，實際承載班號與時刻的是這兩個（不是華航官網）：

| # | 來源 URL | 文件中的角色 |
|---|---|---|
| S1/S2 | `https://proflightsearch.com/search-flights/TPE-NRT`、`.../NRT-TPE` | 機場即時看板彙整，提供「實際有飛」的證據（起飛時刻、Gate、狀態） |
| S3/S4 | `https://info.flightmapper.net/route/China_Airlines_CI_TPE_NRT`、`..._CI_NRT_TPE`、`/flight/China_Airlines_CI_107` 等 | OAG 系排班資料，提供營運日、機型、**effective 起訖日** |

（S6 華航官方時刻表在該文件中被標為「⚠️ 未能成功擷取」，正是本次要複驗的對象。）

### A.2 實驗：對兩個來源發純 `requests`

**A-1｜`proflightsearch.com/search-flights/TPE-NRT`（請求 #1）**

```
GET 200 · 200,641 bytes · text/html;charset=UTF-8
```

純 `requests` 就拿到**完整伺服器端渲染的內容**（無需 JS、無 `__NEXT_DATA__`）。可直接解析到的資料：

| 資料 | HTML 位置 | 樣本 |
|---|---|---|
| 今日離場看板 | `<li class="td-row" id="today-CI100">` | `09:30` / `CI100` / `On schedule` / `Gate D7 · T2` |
| 近 3 日逐班歷史（含**表定**與**實際**） | `<details class="fids-rt-flight">` | `Sat, Oct 31 … Sched 09:30 On time D7 · T2`；`Fri, Oct 30 … Departed 09:24 (Sched 09:30, 6m early)` |
| JSON-LD `@type: Flight` | `<script type="application/ld+json">` | `flightNumber: CI100/CI104/CI108/CI106`（**無時刻**） |

→ 今日看板四班與文件記載完全一致（CI100 09:30、CI104 12:35、CI108 14:25、CI106 16:25）。

**⚠️ 資料品質雜訊（實測發現，既有文件未記錄）**：同一頁的「Who flies it」區塊寫 `CI100 08:50 daily · CI104 12:30 daily`，那是**夏季班表時刻**（見該文件 C5），與同一頁今日看板的 09:30 自相矛盾。→ 解析時**不可使用 `arow-flights` 區塊**，只能用 `today-*` 看板與 `fids-rt-flight` 歷史區塊。

**A-2｜`info.flightmapper.net/route/China_Airlines_CI_TPE_NRT`（請求 #3）**

```
GET 200 · 152,509 bytes · text/html; charset=utf-8
```

**這是本次最重要的發現**：FlightMapper 完全沒有反爬，純 `requests` 拿到整份排班表，而且是**前瞻性**的（有 `Effective from` 生效日），結構固定到可以用 3 個 regex 拆完：

```html
<div class="m-scbox"> <a href="/flight/China_Airlines_CI_100">CI100</a> … </div>
<div class="m-time">09:30</div> … <div class="m-time">13:30</div>
<div class="m-infobox"> Effective from 2026-10-25<br> </div>
<div class="m-weekday">Mon<br><font color="#408502"><i class="icon-ok"></i></font></div>   <!-- 有飛 -->
<div class="m-weekday">Tue<br>-</div>                                                      <!-- 沒飛 -->
```

解析結果（2026/27 冬季，`Effective from 2026-10-25` 起的所有區段）：

| 方向 | 班號 | 時刻 | 分段營運日（同一班號會有多段） | 聯集 | 與既有文件 |
|---|---|---|---|---|---|
| TPE→NRT | CI100 | 09:30 / 13:30 | MTWS（10-25 起）＋ T（10-29 起）＋ FS（10-30 起） | **每日** | ✅ 一致 |
| TPE→NRT | CI104 | 12:35 / 16:35 | MWFS（10-25）＋ TT（10-27） | **每日** | ✅ 一致 |
| TPE→NRT | CI106 | 16:25 / 20:30 | MTWTFSS（10-25） | **每日** | ✅ 一致 |
| TPE→NRT | CI108 | 14:25 / 18:30 | MWFSS（10-25）＋ TT（10-27） | **每日** | ✅ 一致 |
| NRT→TPE | CI107 | 09:20 / 12:30 | MTWTFSS（10-25） | **每日** | ✅ 一致 |
| NRT→TPE | CI101 | 14:35 / 17:45 | MTWS（10-25）＋ T（10-29）＋ FS（10-30） | **每日** | ✅ 一致 |
| NRT→TPE | CI105 | 17:45 / 20:55 | MWFS（10-25）＋ TT（10-27） | **每日** | ✅ 一致 |
| NRT→TPE | CI109 | 19:30 / 22:45 | MWFSS（10-25）＋ TT（10-27） | **每日** | ✅ 一致 |

（反向頁 `..._CI_NRT_TPE` 請求 #10：`200 · 152,209 bytes`，同樣可解析，50 筆時刻區段。）

**解析必踩的坑（實測歸納，實作時一定要注意）**：

1. **同一班號會出現多段重疊的 `Effective from` 區段**（例：CI100 = MTWS + T + FS）。必須**取聯集**，不能只取第一段，否則會誤判「週二、週五 CI100 沒飛」。
2. 頁面上還有**夏季時刻**（CI100 08:55/13:15、CI108 14:40/19:00、CI106 16:30/20:45，`Effective 2026-03-29 through 2026-10-24`）。用 `Effective from <date>` / `through <date>` 篩選即可排除。
3. 頁面混入**非定期航班**噪音：`CI2100`（2026-03-04 包機）、`CI2108`（2026-03-10 包機）、`CI9902`（10:00/14:25 定期貨/加班）——需用 `This is a Non-scheduled Passenger flight` 與「聯營/貨機班號」規則過濾。
4. **資料快照時間**：該頁 datepicker 的 `rangeHigh:"20261030"` → 這份排班資料的 as-of 約為 2026-10-30。下一次換班表（2027-03-28 夏季）不會自動出現，需定期重抓比對。
5. `Valid until YYYY-MM-DD` 這種標記代表「該時刻僅有效到此日」，與 `Effective from` 語意相反，寫 parser 時不可混用。

**A-3｜反爬差異比較（有沒有必要出動 chromium）**

| 來源 | 純 `requests` | 真瀏覽器 | 結論 |
|---|---|---|---|
| `info.flightmapper.net` | **200，完整資料** | 未測（無必要） | 不需瀏覽器 |
| `proflightsearch.com` 頁面 | **200，完整資料** | 未測（無必要） | 不需瀏覽器 |
| `proflightsearch.com/api/route-today/TPE/NRT` | **403 `{"error":"Forbidden"}`**（請求 #18） | 未測 | 內部 API 有保護 → **繞過 API，直接解析頁面 HTML** |

→ 兩個可用來源都**沒有**反爬，`requests` 就夠。這也解釋了既有文件「用工具抓到資料」不是運氣。

### A.4 判定：**華航 TPE–NRT 班表可以穩定自動取得。**

- **來源**：`info.flightmapper.net/route/China_Airlines_CI_{TPE_NRT,NRT_TPE}`（排班／營運日／有效期間）＋ `proflightsearch.com/search-flights/{TPE-NRT,NRT-TPE}`（實際營運佐證、今日看板）。
- **成本**：每輪 **2 個 GET**（正反各一；加即時佐證最多 4 個），單次 < 200 KB，無需 token／cookie／瀏覽器。工程成本 ≈ 一支 60–80 行的 HTML parser（selector 已在上表列出）。每週 1 次即可（班表季切換才是變動點）。
- **風險**：兩個都是**第三方彙整站**，非官方；FlightMapper 可能隨時收掉或改版（本次無任何反爬，短期風險低）。**建議保留 proflightsearch 作為交叉驗證的第二來源**，兩者時刻不一致時以「機場看板實測」為準（這正是既有文件 C1 用來判定 CI107 為 09:20 的方法）。

---

## 二、問題 B：bookingportal 的 403 是「瀏覽器指紋」還是「IP／地區」？

### B.1 403 的完整證據（請求 #5）

```
POST https://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search
Content-Type: application/x-www-form-urlencoded
body: B_LOCATION_1=TPE&E_LOCATION_1=NRT&B_DATE_1=202611140000&B_DATE_2=202611150000&…&TRIP_TYPE=R

HTTP/1.1 403 Forbidden   (body 444 bytes)
Server: AkamaiGHost
Content-Type: text/html
Strict-Transport-Security: max-age=31536000 ; includeSubDomains ; preload
Set-Cookie: _abck=A337F439…~-1~YAAQsT7cPcpuWIOhAQAA6NIHkBB+fi5/KvLbfPPKU6+…~-1~-1~-1~-1~-1
Set-Cookie: bm_sz=55BA5684…~3228229~4469060
```

Body（原文，另存 `artifacts/ci-403-akamai-body.html`）：

```html
<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>
<H1>Access Denied</H1>
You don't have permission to access "http://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search" on this server.
<P>Reference #18.b13edc3d.1793417794.5b9b63d
<P>https://errors.edgesuite.net/18.b13edc3d.1793417794.5b9b63d
</BODY></HTML>
```

三次 403 的 Reference 只有時戳／雜湊不同，**Rule/config ID 完全相同（`18.b13edc3d`）**：

| 請求 | 客戶端 | Reference # |
|---|---|---|
| #5 | 純 `requests`（Chrome UA） | `18.b13edc3d.1793417794.5b9b63d` |
| #6 | 純 `requests` ＋ `Origin`/`Referer`/`Sec-Fetch-*` | `18.b13edc3d.1793417797.5b9c1db` |
| #12 | 真 Chromium（xvfb）**同源 `fetch`** | `18.b13edc3d.1793417858.5ba5384` |
| #14 | 真 Chromium **HTML form 真實導航 POST**（帶 Akamai cookies） | `18.b13edc3d.1793417869.5ba537d` |

### B.2 線索判讀：`Reference #` 說明什麼

Akamai 的 `Access Denied … Reference #<config>.<ts>.<hash>` 是**規則命中**（edge 直接回），不是上游錯誤，也不是「整個 IP 被列黑」的空白封鎖。若是 IP/geo 黑名單，通常是整個 host 全擋（連首頁都 403），但實測不是。

### B.3 關鍵對照實驗：擋的是「bookingportal 這個子網域」，還是整個華航？

| # | 目標 | 方法 | 結果 | 意義 |
|---|---|---|---|---|
| #7 | `flights.china-airlines.com/zh-tw` | GET | **200**（`Server: cloudflare`） | 這個子網域根本在 **Cloudflare**，不是 Akamai |
| #8 | `www.china-airlines.com/tw/zh` | GET | **200**（`X-Akamai-Transformed: 0 - 0 -`、`_abck …~-1~`） | 主站在 Akamai，**GET 可以過**（⚠️ 既有文件記為 403，本次不成立） |
| #4 | `bookingportal…/eRetailPortal/` | GET | **200 → 302 `/maintain/page-400.html`**（"Not Found"） | 子網域本身**沒有被 IP 封**，仍會回應 |
| #13 | `www.china-airlines.com/tw/zh` | GET（真瀏覽器） | **200**，title「首頁 - 中華航空」，完整執行 JS | 真瀏覽器可正常瀏覽主站 |

→ **主站、子網域、同一個出口 IP 都通**。所以「IP 信譽／地區封鎖」**不是** bookingportal 403 的主因（至少對 GET 不是）。

### B.4 決定性實驗：同一個 URL，只差 HTTP method（請求 #9 / #15 / #16）

| 請求 | 方法 / 路徑 | 結果 | 回應特徵 |
|---|---|---|---|
| #9 | **GET** `/eRetailPortal/Booking.svc/Booking/Search` | **405 Method Not Allowed** | `Allow: POST`、`Server` 無、ASP.NET XML 錯誤頁 → **請求已到 origin** |
| #5 | **POST** 同一路徑（form） | **403** AkamaiGHost | edge 攔掉 |
| #15 | **POST** 同一路徑（`application/json`、body `{}`） | **403** AkamaiGHost | → 與**參數／payload 無關** |
| #16 | **POST** `/eRetailPortal/Booking.svc/Booking/NoSuchMethodXYZ` | **404 Not Found** | `Server` 無、ASP.NET 錯誤頁 → **POST 在該 host 是通的** |

**這四個請求把可能性收斂到一個**：

- 不是 IP／geo（GET 同路徑到得了 origin；別的 POST 也到得了 origin）
- 不是地區（台灣 IP 以外的問題不成立，我們是機房 IP 但 GET 全通）
- 不是缺少 `Referer`/`Origin`（#6 補上仍 403）
- 不是 payload/WAF 內容規則（#15 body 換成 `{}` 仍 403）
- **是「路徑 `/eRetailPortal/Booking.svc/Booking/Search` ＋ POST」這個組合上的 Akamai 規則**，且與客戶端是否被 Bot Manager 認證有關。

**Bot Manager 的指紋證據**：所有回應的 `_abck` cookie 第 2 段都是 **`-1`**（未通過驗證；合法瀏覽器通過後會變 `~0~`）：

| 情境 | `_abck` 分段 | `ak_bmsc` |
|---|---|---|
| 純 requests GET portal root | `DDC42CC7… ~ -1 ~ YAAQ… ~ -1` | — |
| 純 requests GET www 主站 | `B8DCC07E… ~ -1 ~ …` | 有 |
| **真 Chromium** 載入 portal root（等 6 秒） | `DDC42CC7… ~ -1 ~ YAAQ… ~ -1`（783 chars） | 有 |
| **真 Chromium** 再載入 www 主站（等 7 秒，跑完 Bot Manager 感測 JS） | `DDC42CC7… ~ -1 ~ YAAQ… ~ -1` | 有 |

→ **連真瀏覽器都沒被 Akamai 認證成合法**。所以「先建立 session（載入首頁拿 cookie）再進 bookingportal」這條常見破法，**本次實測並沒有讓它通過**（#14 的 form 導航帶著這些 cookie 仍是 403）。

### B.5 主因判定與信心程度

**判定（信心：高）**
> bookingportal 的 403 是 **Akamai 對「`Booking/Search` + POST」這條路徑的方法級規則／Bot Manager 保護**觸發，**不是** IP 信譽、不是地區封鎖、不是缺少 session cookie、不是 Referer、不是 payload。
> 換 IP 不會有幫助；換「更真的瀏覽器」也未必有幫助（本次真瀏覽器 × 3 種送法全滅）。

**仍無法確定的一段（誠實邊界，信心：中）**
> 我們**沒有**、也**無法**驗證「一個 Bot Manager 認證通過的真人瀏覽器（`_abck ~0~`）」是否會被放行。
> 反過來說：這個端點是華航官網唯一的訂票搜尋入口（`www.china-airlines.com` 首頁原始碼裡出現 4 次），**真人每天在用它**，所以規則不可能是「所有 POST 一律拒絕」，只能是「需要通過驗證的客戶端」。→ **推論**：家用網路 + 真人操作的瀏覽器大概能通過；但這是**推論，不是本次實測結果**。
> 為什麼我們的真 Chromium 沒通過？最可能是 **資料中心 IP ＋ Playwright/CDP 自動化指紋**（本機是機房 IP），這兩者在 Akamai Bot Manager 眼中都是負分。我們沒有更換 IP／代理（依請求紀律禁止），因此無法剝離這兩個變因。

**因此回答使用者原本的直覺**：

| 使用者的問法 | 答案 |
|---|---|
| 「用真瀏覽器操作查看呢？」（**人**去看） | **可行**。403 是機器指紋／規則，不是網路不通；華航官網對真人開放。**但要人工，不能排程。** |
| 「用真瀏覽器**自動化**抓呢？」 | **不行（至少在本機環境下 4 種方式全部失敗）**，且即使成功也會極脆弱——Akamai 會持續更新偵測，任何一次改版就全滅。**不建議把 CI 訂票流程的自動化押在這條路上。** |

---

## 三、替代入口調查

### 3.1 華航官方「航班時刻表」靜態頁（`booking.china-airlines.com/Flightschedule`）

既有文件 S6 記為「curl 403；工具回報 Response too large (5MB)」。本次實測（請求 #17）：

```
GET https://booking.china-airlines.com/Flightschedule/?country=tw&locale=zh
→ 200 OK · 12,716,941 bytes (~12.7 MB) · Server: Microsoft-IIS/10.0 · title「航班時刻表」
```

→ **403 消失、也不再是 SPA 空殼**，是一份 SSR 的大頁面（12.7 MB，其中絕大部分是機場下拉選單）。**但是**：

| 檢查 | 結果 |
|---|---|
| 頁面內有 `CI100`/`CI104`/`CI107`… 嗎？ | ❌ 0 次（班表不在初始 HTML 裡） |
| 查詢怎麼送出？ | `<form method="post" action="./?country=tw&locale=zh" id="form1">` |
| 有 bot 保護嗎？ | ✅ **Cloudflare Turnstile**：`<div class="cf-turnstile" data-sitekey="0x4AAAAAAAKX6ewmGqs6hTb9" data-action="verify_cf">` ＋ `<input type="hidden" name="hdnTurnstileToken">` |
| 無 token 直接 POST（請求 #20） | `200` 但**回傳同一份 12.7 MB 空殼**，`CI100` 出現 0 次，頁面仍有 19 個 `Turnstile` 字樣 → 表單被拒，什麼都沒查到 |

**判定**：官方班表**不能自動化**（需通過 Cloudflare Turnstile 人機驗證）。這條路徑正式關閉 —— 但它也**不重要**：FlightMapper 的資料已足夠且可解析（§A.2）。

### 3.2 `proflightsearch.com` 內部 API

`GET /api/route-today/TPE/NRT` → **403 `{"error":"Forbidden"}`**（請求 #18；注意是 JSON 403，不是 HTML 403，屬應用層授權，不是 Akamai）。
→ 不要用 API，**直接抓頁面 HTML**（頁面本身 200 且內容完整）。

### 3.3 其他查過但沒用的入口（本輪未再花請求）

- `booking.china-airlines.com/FlightStatus/`：官網航班動態，同屬需要互動查詢的 SPA。
- `www.china-airlines.com` 的 Next.js 內容 API（`prd-api.china-airlines.com`，只在 HTML 看到圖片資源）：未測，**不建議投入**——它服務的是官網內容頁，不是班表。
- `airlin.es` / `flight.info`：既有文件已記 403。

---

## 四、誠實的邊界：我測到哪、哪些沒測

**已實測（有原始證據）**
1. FlightMapper 正／反向 TPE↔NRT 排班頁，純 `requests` 200，8 班冬季時刻＋營運日＋effective 日期全部解析成功，與既有班表文件逐項一致。
2. proflightsearch 航線頁與單班頁，純 `requests` 200，今日看板與近 3 日實際起降可解析；發現其「Who flies it」區塊是過期（夏季）時刻。
3. bookingportal：4 種客戶端 × POST 全部 403，Rule ID 一致 `18.b13edc3d`；GET 同路徑 405 到 origin；POST 別的路徑 404 到 origin；POST 換 payload 仍 403。
4. `_abck` 在真瀏覽器上仍是 `~-1~`（未通過 Bot Manager 認證）。
5. 官方時刻表頁 Turnstile 保護，無 token 的 POST 取不到任何班表。

**沒測（不敢推論成結論）**
- **住宅 IP / 真實使用者環境**下的 bookingportal POST（依紀律不換 IP、不用代理）。這是判定「真人能不能用」的唯一缺口。
- **Bot Manager 認證通過後**（`_abck ~0~`）的 POST 行為。
- 2027 夏季（2027-03-28 起）班表：FlightMapper 頁面 as-of 2026-10-30，尚未收錄。
- FlightMapper / proflightsearch 的**條款**（robots.txt、ToS）未審閱。僅做單航線、低頻存取，但正式接排程前應確認。
- 其他航線（KHH–NRT、TSA–HND）是否同樣可用：**未測**，但機制上同一頁面範本，預期可用。

**推論（明確標記為推論）**
- 「真人用家用網路開 bookingportal 訂票正常」——由「這是官網唯一訂票入口、且每天有人用」反推，非實測。
- 「我們的真瀏覽器被擋，主因是機房 IP ＋ CDP 自動化指紋」——合理但**未做變因隔離**（不能換 IP）。

---

## 五、建議（下一步）

1. **班表：立刻可做**。新增 `fetch_ci_schedule.py`（純 `requests` + regex/BS4），來源 FlightMapper 正反兩頁，`proflightsearch` 當交叉驗證。輸出 `flight_no / dep_time / arr_time / days_of_operation / effective_from`。注意 §A.2 的五個坑（重疊區段取聯集、排除夏季段、過濾包機號）。
2. **票價：維持現狀**（airtrfx histogram，38% 覆蓋），並修訂 `docs/tech-decisions/華航票價爬蟲-2026-08-20.md` 的「Playwright + xvfb」決策 → 那個路徑既非必要（票價不需要）也無效（訂票端點照樣 403）。
3. **bookingportal：正式列為「不自動化」**，寫進決策文件，避免未來重複投入。若真的需要 flight-level 報價（含艙等／剩位），選項只有：付費 OAG/Amadeus、或人工查詢。
4. 若仍想再確認「真人可否通過」，唯一乾淨的實驗是**請人在家用網路開一次華航訂票頁並截圖**——這不需要寫程式，也不涉及繞過。

---

*文件性質：一次性實測快照。樣本為 2026-10-31 單一時點、單一航線、20 次請求；Akamai 規則與第三方站台結構隨時可能變動。*
