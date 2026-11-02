# 華航 bookingportal 的 503：成因分離實驗（用真實 UI 走一次完整查詢）

> 實驗日期：2026-10-31（台北時間 17:35–17:36）
> 環境：Playwright 1.62（python）＋ `/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`（`Google Chrome for Testing 153.0.8010.12`）、`xvfb-run` 有頭模式、**住宅 HiNet（AS3462）單一出口、無代理**、持久化 profile `/tmp/ci503-profile`
> 手法：**像真人一樣操作官網**（滑鼠軌跡、逐字鍵盤輸入、真實日曆點選、單次送出）。**沒有** stealth patch、**沒有**偽造指紋、**沒有**反偵測服務、**沒有**換 IP。
> 前置文件：[ci-bot-detection-diagnosis.md](ci-bot-detection-diagnosis.md)（含「附錄：現狀速查」）、[ci-automation-alternatives.md](ci-automation-alternatives.md)、[ci-graphql-feasibility.md](ci-graphql-feasibility.md)
> 新增證據：`artifacts/ci503-01-form-filled-real-ui-20261031.png`、`ci503-02-calendar-real-click-20261031.png`、`ci503-03-flight-selection-fares-20261031.png`、`ci503-04-www-warmup-20261031.png`
> 未修改任何既有檔案；暫存腳本全部在 `/tmp`（`ci503_flow6.py` 等），未進 repo。

---

## 〇、結論（一句話）

**503 可解 —— 而且它不是「擋住真人的牆」，是「我們走錯門」：用官網真實 UI 送出查詢，這台機器（SwiftShader + Chrome for Testing + Playwright）**一次就**進到 `航班選擇` 頁，看到 CI 100／CI 104 的艙等與票價（TWD 17,107 / 12,450），全程沒有 403、沒有 Akamai 挑戰、沒有 503。**

更精確的三句話：

1. **503 只出現在「手工組 5 欄位 POST 到 `Booking.svc/Booking/Search`」這條路上**；官網現在**不是這樣送出查詢的**。真實 UI 產生的是帶簽章 token（`emcid`）的 **deep link**，落點先遇到的是 **Imperva（Incapsula）過場頁**，過場自動清關後 302 到 **`des-portal.china-airlines.com`**（新的 Angular 訂票前台），直接列出航班與報價。
2. 因此「origin 因參數不完整而拒絕（假設 c）」與「走錯端點／走錯協定形狀」**是同一件事的兩面，而且已被證實**；「連完整操作都會被 tarpit（假設 b）」**被否證**（至少在這一次的完整操作下）。
3. 但「**真人手動查票**」與「**可排程的自動化資料源**」要分開講：真人手動查票 = **沒問題**（本次即為完整操作，只是由腳本代打）；可排程自動化 = **技術上做得到、但不建議產品化**（ToS、脆弱性、每輪 ~40 秒瀏覽器成本），細節見 §五、§六。

---

## 一、實驗過程（一次完整查詢流程，實際做了什麼）

### 1.1 流程與時間軸（單次嘗試，無重試）

| 時間 | 動作 | 觀察 |
|---|---|---|
| 1.3s | GET `www.china-airlines.com/tw/zh` | **200**「首頁 - 中華航空」 |
| 3–10s | 在首頁移動滑鼠、捲動 2 次、停留 | 正常瀏覽（暖身，不是必要條件，但模擬真人動線） |
| 5.8s | GET `flights.china-airlines.com/zh-tw` | **200**，落點 `/zh-tw/flights`（此子網域在 **Cloudflare**） |
| 8–19s | 點「選擇出發地」→ **逐字鍵盤輸入 `TPE`**；點「選擇目的地」→ 輸入 `NRT` | autocomplete 各回 1 筆：`臺北(桃園)-TPE-臺灣`、`東京(成田)-NRT-日本`，**點選下拉選項**（非塞值） |
| 21–26.5s | 點「出發日期」→ 在日曆 popover 點 `2026/11/20`；點「回程日期」→ 點 `2026/11/24` | 按鈕文字由 `YYYY/MM/DD` 變成 `2026/11/20` / `2026/11/24`（由 UI 讀回驗證） |
| 27.5s | 點「**現在預訂**」（表單送出，只按一次） | 導航至 `bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search?emcid=…&_gl=…&_ga=…` |
| 32.5s | 該次導航的回應 | **Imperva / Incapsula 過場頁**：`<noscript><title>Pardon Our Interruption</title>`、`Please stand by`、資源路徑 `/_Incapsula_Resource?...`、`window.reeseSkipExpirationCheck`、`X-SPA`（**不是** Akamai `sec-cpt`、**不是** 503） |
| ~33–38s | 過場頁自行完成（沒有任何人為介入、沒有破解） | 自動導向 `des-portal.china-airlines.com/booking?lang=zh-Hant` |
| 39.3s | 最終落點 | `des-portal.china-airlines.com/booking/availability/0`，標題「**航班選擇**」 |

**補充事實**：
- 全程 `403 / 429 / 503` 的回應數 = **0**（以 `page.on("response")` 過濾統計）。
- 全程**沒有**出現 `Challenge Validation`（Akamai `sec-cpt`）；cookie `sec_cpt` 不存在（`null`），`_abck` 尾碼為 `0`。
- 送出搜尋 = **1 次**；失敗重試 = **0 次**。

### 1.2 最後看到的東西（這就是先前拿不到的 flight-level 報價）

`artifacts/ci503-03-flight-selection-fares-20261031.png`（節錄文字）：

```
請選擇您的出發航班   台北 到 東京   2026年11月20日 星期五
票價日曆：17日 TWD 7,085 / 18日 7,085 / 19日 7,085 / [20日] 7,085 / 21日 8,892 / 22日 7,085 / 23日 12,450
CI 100  09:30 TPE → 13:30 NRT  3h 0m   經濟艙 單程 TWD 17,107（剩餘1個席位）｜商務艙 單程 TWD 27,631
CI 104  12:35 TPE → 16:35 NRT  3h 0m   經濟艙 單程 TWD 12,450（剩餘2個席位）｜商務艙 單程 TWD 18,777
（DOM 內另有 CI 106、CI 108；價格以 data-amount="17107" 等形式存在）
```

→ 這正是既有文件說「只有 bookingportal 能給」的東西：**班號 × 艙等 × 剩位 × 含稅票價**。本次拿到了（畫面上可直接讀，DOM 內可解析）。

### 1.3 關鍵發現：真實 UI 的「門」換了

本次觀測到的機制與 10/31 上午的診斷**不同**，必須寫清楚：

| 項目 | 上午（5 欄位 POST） | 本次（真實 UI） |
|---|---|---|
| 觸發方式 | JS 動態組 `<form method=POST>` 直接送出 | 點「現在預訂」，由前端產生 **deep link** |
| URL 形狀 | `/Booking.svc/Booking/Search`（無 query） | `/Booking.svc/Booking/Search?**emcid=…**&_gl=…&_ga=…`（**判為 GET**；主框架導航的 method 未被日誌單獨攔到，依 query 形狀判定，標記為「判為」） |
| 額外憑證 | **無**（只有 5 個表單欄位） | `emcid` token，由第三方 frame `https://em-frame.securitytrfx.com` 以 `postMessage({key:"emcid"})` 提供（頁面 HTML 內可見此段腳本） |
| 第一道防線 | Akamai `sec-cpt` 挑戰（`_sec/verify` ×41） | **Imperva/Incapsula 過場頁**（`Please stand by`，自動清關） |
| 清關後 | 自動重送回 POST → **503** | 302 → `des-portal.china-airlines.com`（新 Angular 前台）→ 航班/票價頁 |
| 是否要人為破解 | 不需要（但需等待 ~30s PoW） | **不需要**（過場頁自行完成，約 5 秒） |

另外，`flights` 頁的 `__NEXT_DATA__` 內可直接看到 deep link 服務的設定（本次**未呼叫**，僅記錄）：

```json
"deeplink":{"config":{"endPoint":"https://openair-california.airtrfx.com/deeplink-service/v2/%%airline_code%%/deeplinks",
                      "axiosConfig":{"headers":{"EM-API-Key":"<頁面內公開的 tenant key>"}}}}
```

→ 也就是說，**存在一條可能完全不需要瀏覽器的正規 deep link API**（airtrfx 的 deeplink-service，與已經在用的 histogram 同一家、同一把 key）。本輪依「1 次查詢流程」紀律**沒有測**，列為下一步候選（§五）。

---

## 二、與前次「只送 5 欄位」的做法對照

| 維度 | 前次（`ci-bot-detection-diagnosis.md` §2.4 / 附錄 B.2） | 本次 |
|---|---|---|
| 送出內容 | `B_LOCATION_1=TPE&E_LOCATION_1=NRT&B_DATE_1=202611140000&B_DATE_2=202611150000&TRIP_TYPE=R`（**5 欄位**） | 由真實 UI 產生：完整搜尋條件（TPE/NRT、來回、`2026/11/20`–`2026/11/24`、1 成人經濟艙）＋ **`emcid` 簽章 token**＋瀏覽器自身的 `_gl/_ga` 參數 |
| 缺了什麼 | `CABIN`、`LANG`、`EBA`、`channel`、`B_LOCATION_2/E_LOCATION_2`、旅客結構、以及**最關鍵的 `emcid`** | 全部由頁面自己補齊（人不用知道） |
| 來源 | 逆向 `/tmp` 腳本自己組 | 點按鈕，前端自己組 |
| 結果 | 403（冷）→ 挑戰 → **503** | 200（Imperva 過場）→ 302 → **航班＋票價** |
| 對 503 的意義 | 先前無法分辨「tarpit」與「origin 因參數不全而拒絕」 | **分離成功**：同一台機器、同一出口 IP、同一天，只把「送出方式」換成真實 UI，503 就不再出現 |

**判定**：503 的成因**不是**「Akamai 對這個客戶端永久 tarpit」，也**不是**「這個端點對誰都 503」。最符合觀測的解釋是：

> **`Booking/Search` 的裸 POST（無 `emcid`、非完整表單、且是舊版協定形狀）已經不被現在的前台接受**；edge 那一層先給 Akamai 挑戰，清關後 origin/新前台對這個「舊形狀 + 不完整參數」的回應就是 503。真實前台走的是「帶 `emcid` 的 GET deep link → Imperva 過場 → des-portal」，這條路上沒有任何 503。

**這也修正了前一份文件的一個措辭**：不是「進得去門、拿不到東西」，而是「**走錯門** —— 舊門（裸 POST）後面是 503，新門（`emcid` deep link）後面是報價」。原文件的資料面結論（不要為了這件事做指紋偽造）不變。

---

## 三、為什麼這一輪特別有說服力（以及沒有的地方）

**有說服力的部分**

1. **同一個「可疑」客戶端**：`SwiftShader` 軟體渲染、Chrome for Testing、xvfb、Playwright 啟動 —— 前一份文件把這些列為「強烈自動化訊號」。**它們全部還在**，結果照樣拿到票價。→ 「指紋不夠像真人」不是這條路上的阻礙。
2. **住宅 IP**（附錄 A 已確認為 AS3462 HiNet）＋ 無代理 → IP 這一維仍被排除。
3. **沒有做任何對抗**：沒有改 UA、沒有改 WebGL、沒有 stealth plugin、沒有解 PoW。唯一做的事是「照 UI 操作」。
4. **單次送出就成功**：沒有靠重試刷過去的樣本。

**不能過度延伸的部分（重要）**

- **n = 1**：單一航線（TPE–NRT）、單一日期、單一次嘗試。不能宣稱「每次都行」。
- 本次**沒有**再現 503（因為沒走舊路徑），所以嚴格說我們是**繞開了** 503 的觸發條件，而不是「把 503 修好」。這對使用者的意義是好的（真人不走那條路），但對「舊腳本能不能救」的答案是：**不要救，改走真實 UI／deep link**。
- `emcid` 的**有效期、可重用性、是否綁 session/referrer** 未測（依紀律不重複觸發查詢）。
- 我們沒有在「全新 profile」下重跑。本次 profile 已含先前瀏覽留下的 cookie（`_abck ~0~`、`bm_sv`、Imperva cookie 等）。**冷 profile 是否也能一次過，未測**（前一份文件的資料顯示冷客戶端容易吃 403）。

---

## 四、對「真人手動查票」的含義

- **真人手動查票：可解，且本次已間接證明。** 本次流程與真人手動的差異只有「誰動滑鼠」：欄位是逐字打的、日期是日曆點的、按鈕是按的；沒有任何一步是真人才做得到的（沒有 CAPTCHA、沒有簡訊、沒有 3D 驗證）。
- 因此正確的說法是：**「華航官網訂票查價」對真人是正常可用的**，`flights.china-airlines.com` 與 `des-portal` 都沒有把我們當機器人擋掉。
- 前一份文件說「真人手動查票從未被實測（n=0）」——這一格現在**仍然沒有「人類」樣本**，但**已經有「完整 UI 流程」樣本且成功**，這比「未測」強得多，也把「origin 當時真的壞了（假設 d）」大幅壓低（如果 origin 壞了，這條路也會壞）。

---

## 五、如果目標是「可排程的資料源」：剩下的路徑與成本

> 前提：本輪證明「503 不是死路」，但**不等於**「明天就能排程抓」。下面是依成本排序的三條路。

| # | 路徑 | 做法 | 成本 | 風險／不確定性 | 建議 |
|---|---|---|---|---|---|
| **1** | **airtrfx deeplink-service（純 HTTP，最低成本）** | 從 `flights` 頁 `__NEXT_DATA__` 取 `EM-API-Key`（與已在用的 histogram 同一把）→ `POST/GET openair-california.airtrfx.com/deeplink-service/v2/ci/deeplinks` → 取得真實 deep link（含 `emcid`）→ 追導航至 des-portal 的 availability API | **極低**：1 個 GET + 1–2 個 API 呼叫，無瀏覽器 | **未測**（本輪紀律不追加查詢）。需確認：deeplink-service 是否需要額外 session、`emcid` 是否綁 referrer、des-portal 的 availability API 有沒有自己的保護 | **下一步優先測這一條**（若通，就完全不需要瀏覽器，也不需要碰 `Booking/Search` 的舊 POST） |
| **2** | **瀏覽器 UI 自動化（本輪已驗證可行）** | 本輪腳本：真實鍵盤輸入 + 日曆點選 + 單次送出，~40 秒/輪 | 中：每輪要開 xvfb + Chromium；selector 一改就壞；需持久化 profile | ToS（自動化存取）；Imperva/Akamai 規則會輪替；無法保證長期穩定 | 可作為**一次性／低頻**取數，不建議當排程 |
| **3** | **人工查詢** | 人開瀏覽器看（可截圖留存） | 人工 1–2 分鐘 | 無技術風險，完全合法 | **需要「今天/某天價格」時，這是最務實的選擇** |
| — | 商業授權（OAG／Amadeus／Travelport／旅行社 B2B） | 付費 API | 高（合約） | 低（有 SLA） | 若要做成產品、需要 flight-level 報價的**長期穩定**來源，仍是唯一正解 |
| ✗ | 重做「裸 5 欄位 POST」 | — | — | 已證實會 503 | **不要做** |
| ✗ | 偽造指紋／破解挑戰 | — | — | 法遵風險 | **不要做**，本輪證明也不需要（UI 路徑不需要對抗） |

**成本對照（本輪實測）**：UI 路徑 = 1 次查詢約 **40 秒**、4 次主框架導航、以及頁面自己發的一堆 analytics beacon（Google/Facebook/Yahoo/Taboola 等，非我方發起）。純 HTTP 路徑（若 #1 成立）= **1–2 個請求、< 1 秒**。→ **若要做產品，#1 值得先花半天驗證。**

---

## 六、風險聲明（ToS 與法遵）

- 華航官網／訂票系統的服務條款一般禁止以自動化手段存取、爬取、或規避安全機制。**本輪是診斷性質的一次性操作**（單一航線、單一日期、單次送出、無商業利用、無繞過機制），但仍屬「以自動化程式操作其網站」。
- 本輪**沒有**做任何規避存取控制的行為：沒有偽造瀏覽器指紋、沒有破解或代解挑戰、沒有更換 IP／代理、沒有修改請求參數去騙過檢查。遇到的安全機制（Imperva 過場）是**讓瀏覽器自己跑完**的。
- 若把 §五 的 #1 或 #2 做成排程，**風險等級會從「診斷」變成「持續性的自動化存取」**，這與本輪的性質不同，需由專案方自行決定並承擔。若是產品需求，走 §五 的商業授權路線才是可持續的。
- 本文件**不是繞過指南**：沒有提供 challenge 求解器、指紋偽造 patch、或可重用的 stealth 程式碼；所有腳本留在 `/tmp`，未進 repo。

---

## 七、誠實邊界（哪些沒測、為什麼）

**已測（有截圖或日誌）**
1. 完整 UI 流程（打字選機場 → 日曆點日期 → 按「現在預訂」）→ `Booking/Search?emcid=…` → Imperva 過場（HTML 已存）→ `des-portal/booking/availability/0`「航班選擇」→ **CI 100/104/106/108 與票價**。
2. 全程 0 次 403/429/503；0 次 Akamai `sec-cpt`；`sec_cpt` cookie 不存在、`_abck ~0~`。
3. 送出僅 1 次、失敗重試 0 次；`emcid` 確實出現在導航 URL。

**沒測（不敢當結論）**
- **n=1**：沒有第二次查詢、沒有其他航線／日期，無法回答「穩定率」。
- **`Booking/Search` 那次導航的 HTTP method**：主框架導航的回應未被日誌攔到；依 URL query（`emcid`、`_gl`、`_ga`）判為 **GET**，標記為「判為」。
- **冷 profile**：本次用持久化 profile（已含先前 cookie）。全新 profile 是否同樣一次過 → 未測。
- **`emcid` 的性質**：有效期、是否綁 session／referrer／IP、能否重用 → 未測（不重複觸發查詢）。
- **deeplink-service 與 des-portal 的 availability API**：完全未呼叫（在 `__NEXT_DATA__` 看到設定而已）。這是 §五 #1 的待驗證項。
- **Imperva 過場是否會在高頻下升級**（改成 block 頁、或加重挑戰）→ 未測，且不應為了測試而加頻。
- **503 是否仍可重現**（用舊的裸 POST）→ 本輪刻意不再打那條路（避免重複觸發 55 個子請求的挑戰，也避免無意義的負載）。

**揭露**
- 本次網路負載：主框架導航 4 次（www → flights → bookingportal Search → des-portal ×2 轉址算 1 段），我方主動「查詢」送出 1 次；其餘為頁面自動發出的資產與 analytics（Google Ads／GA／Facebook／Yahoo／Taboola／Quantcast 等），非我方額外探索。
- 本輪只新增本文件與 `artifacts/ci503-*.png` 四張截圖，**未修改任何既有檔案**（含 `fetch_schedules.py`、`schedules/*`、其他研究文件、workflow）。
- 截圖中可見訂票流程與價格；`ci503-01` 為填好的搜尋表單、`ci503-02` 為日曆互動、`ci503-03` 為最終航班/票價頁、`ci503-04` 為暖身首頁。

---

## 八、給使用者的一句話

> **不是「無法解決」，是「走錯門」。** 用官網真實的訂票 UI（打字選機場、日曆點日期、按「現在預訂」）送出，這一次就進到「航班選擇」頁，看到 CI 100 / CI 104 的艙等與票價（TWD 17,107 / 12,450），**沒有 403、沒有挑戰、沒有 503**。先前那個 503，是我們自己手工組 5 個欄位 POST 到舊端點造成的 —— 現在的華航前台根本不用那個形狀，而是用帶 `emcid` token 的 deep link 進到 `des-portal`。所以：**真人查票沒問題，503 也不是無解；但若要把它變成排程資料源，請先花半天驗證 airtrfx 的 deeplink-service（純 HTTP），而不是復活那條舊 POST。**
