# 華航 bookingportal：`_abck` 狀態機與「偽裝真人」可行性診斷

> 診斷日期：2026-10-31（台北時間 12:2x–12:5x）
> 環境：Playwright（sync API）＋ `/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`、`xvfb-run` 有頭模式、單一出口 IP（機房）、**未使用任何代理／反偵測套件**
> 前置證據：[ci-automation-alternatives.md](ci-automation-alternatives.md) §B（4 種客戶端全 403、Rule ID `18.b13edc3d`）、[ci-graphql-feasibility.md](ci-graphql-feasibility.md)
> 本次性質：**診斷**，不是繞過工具。未產出可重用的 stealth／指紋偽造程式碼；暫存腳本全部在 `/tmp`，未進 repo。
> 未修改任何既有檔案。

---

## 〇、結論摘要（一句話）

**「像真人」確實能把 403 推過去 —— 但推過去之後拿不到資料（503），所以答案是「進得去門、拿不到東西」。**
更精確地說：本輪實測中，同一個 `Booking/Search` POST 會依客戶端狀態給 **三種不同回應**（403 → Akamai `sec-cpt` 挑戰頁 → 挑戰通過後 503），因此這個 403 **不是**與客戶端無關的「路徑＋方法級死規則」，而是**依客戶端驗證狀態分級**的 Bot Manager 決策；但**放行 = 挑戰通過 ≠ 拿到報價**。

---

## 一、`_abck` 到底在說什麼？（查證後）

### 1.1 狀態機（共識讀法，非 Akamai 官方文件）

`_abck` 的長字串是簽章過的不透明內容，只有**倒數幾個 `~` 分隔的數字**是人類可讀的「狀態尾碼」：

| 尾碼 | 意義 | 來源 |
|---|---|---|
| `~-1~` | **挑戰懸置**：edge 還沒收到（或還沒接受）任何一次 sensor 上報 | Crawlex《_abck: structure, sensor data, and the validation handshake》§"What the value actually looks like"；Roolink《Sensor data》同表 |
| `~0~-1~` | **中間態**：sensor 已收到，但尚未判定足夠 | 同上 |
| `~0~` | **結構上已滿足**：可以停止再送 sensor（依站台設定而異） | 同上；Hyper Solutions《Getting Started》：「`~0~` 代表可停止送 sensor，但**不是所有站台都用這個指示**」 |
| `~1~` | 本輪在**另一個 cookie** 上看到（見 §2.4 `sec_cpt ~1~`），不是 `_abck` 的文件化狀態；不要把兩者混用 | 本輪實測 |

同時要記住三件事（同來源）：

1. **`~0~` 不是「你是真人」的判決**，只是「handshake 在結構上完成了」。真正的放行是 edge 拿 bot score ＋ TLS/HTTP2/IP 指紋與 cookie 內容交叉比對後的**規則決策**。同一份文件原文：「一個說 `~0~` 的 cookie 配上 Akamai 沒預期的 TLS 指紋，不是有效 cookie，是有效 cookie 的鬼魂。」
2. **狀態會過期**：cookie 存一年，但裡面編碼的信任只活幾分鐘到幾小時。
3. 第二層挑戰（`sec-cpt`, `sbsd`）獨立於 `_abck`：`sec_cpt` 尾碼到 `~3~` 才算清關；`_abck` 完美也可能照樣吃 428/挑戰。

### 1.2 sensor 的上報與「成功」的假象

- 上報是 `POST {"sensor_data": "..."}`（v3 格式：先以「腳本檔雜湊」為種子洗牌、再以 `bm_sz` 衍生的雜湊逐字替換）。因此**靜態重放一定失效**：換版 → 檔案雜湊變；換 session → `bm_sz` 變。
- 挑戰端點常見路徑：`/_sec/cp_challenge/verify`、`/<隨機路徑>/...`（本輪實測到的是 `/_sec/verify?provider=crypto` 與兩條隨機路徑）。
- **重要陷阱**：sensor 端點對「正確 payload」「`garbage`」「被截斷的 body」一律回 **`201 {"success": true}`**（proofofbots《Oracles》實測）。**這個 201 不是判決**，讀它當成功訊號等於讀一個常數。

### 1.3 「停在 `~-1~`」與「指紋不夠像真人」是兩種失敗模式（本輪修正了既有文件的讀法）

| 失敗模式 | 觀測特徵 | 這次 ¿ 哪一種 |
|---|---|---|
| **A. handshake 根本沒跑**（頁面沒載到 sensor、只載到 pixel；或頁面是錯誤頁） | `_abck ~-1~` 且**沒有任何 sensor POST**；`window.bmak` 不存在 | bookingportal 根路徑就是這種（見 §2.1） |
| **B. handshake 跑了但沒被判過**（指紋／IP／行為分數低） | 有 sensor POST（甚至 201）、`_abck` 仍 `-1` 或只到 `~0~-1~` | 本輪 www 主站就是這種 |
| **C. 這個部署的儀表不是 `_abck`** | `_abck` 永遠 `-1`，但 `sec_cpt` 從 `~1~` → `~3~`，edge 行為確實改變（403→挑戰→放行） | **本輪證實 bookingportal 屬於這種** |

→ 既有文件 §B.4 把「`_abck` 始終 `~-1~`」直接讀成「Bot Manager 沒把真瀏覽器認證成合法 → 指紋失敗」。**這個推論在本輪被推翻**：同一個真瀏覽器在 `sec_cpt` 走到 `~3~`（文件記載的清關成功值）、並且 edge 回應從 403 變成「挑戰已清、請求被重新送出」之後，`_abck` **仍然是 `~-1~`**。所以 `_abck` 在這套華航 Akamai 設定上**不是一個可用的判讀儀表**，先前「`~-1~` ⇒ 指紋失敗」的因果連結不成立（詳見 §3）。

---

## 二、四種情境實測

### 2.1 場景與計數

- 計數方式：**主文件導航 ＋ 我方主動發出的端點呼叫**（本輪 14 次）。⚠️ **不含** `sec-cpt` 挑戰頁由瀏覽器自己發出的子請求 —— 那部分單次嘗試就高達 **55 個**（其中 `POST /_sec/verify?provider=crypto` 41 次）。這點必須誠實揭露：一次「真人瀏覽器過挑戰」在網路層的請求量遠大於本文件宣稱的次數，我無法在不阻擋挑戰的前提下壓低它。
- 每次情境之間 sleep 3–4 秒；全程單一 IP、無代理、無第三方反偵測服務；只碰 `flights/www.china-airlines.com` 與 `bookingportal.china-airlines.com`。
- 額外發現（共同前提）：兩個子網域載入的是**同一支 Akamai 腳本 id `akam/13/f9bd522`** → 同一個 Bot Manager 設定，這解釋了為何 Rule ID 一致。

### 2.2 結果表

| # | 情境 | `_abck` 尾碼 | 回應 status | Rule ID | 卡在哪 |
|---|---|---|---|---|---|
| **A** | **全新 context（等同無痕）**：無 cookie／無歷史，直接開 `bookingportal/eRetailPortal/` 再 POST | `~-1~` | 導航 200（但被打到 `/maintain/page-400.html`「Not Found」）／**POST 403** | `18.b13edc3d` | **edge 硬擋**。該錯誤頁上 `bmak` 不存在、只載到 Akamai **pixel**（`/akam/13/pixel_f9bd522`，POST 後 `_abck` 值變了但仍 `~-1~`）→ 連 handshake 都沒開始（失敗模式 A） |
| **B** | **持久化 profile（回頭客）**：先開 `www.china-airlines.com/tw/zh`（真 Akamai 站、載入 `bmak`、pixel POST 200）、捲動＋停留 10s，再開 bookingportal 再 POST | `~-1~` | 導航 200 → POST **200** | —（不是 403） | **403 消失，換成 Akamai `sec-cpt` 挑戰頁**（`<title>Challenge Validation</title>`、iframe `provider="crypto"`、`sessionStorage data-duration=30`）→ 卡在「未清挑戰」 |
| **C** | **B ＋ 真人輸入**：滑鼠分段軌跡（6 段×12 step）、6 次 wheel 捲動、每次 1.1s、隨機停留；並檢查洩漏 | `~-1~`（www 主站跑完 `bmak` 後仍是） | POST **200，body 空** | — | **不是 403、也不是挑戰頁**：拿到一個空 body 的 200（Akamai 對可疑客戶端給的曖昧回應／tarpit 行為之一，見 §2.3）。洩漏檢查：`navigator.webdriver=false`、無 `cdc_`/`__playwright` 痕跡、`window.chrome` 存在、plugins=5、`Runtime.enable` 成功（CDP 未洩漏），**但 WebGL renderer = `SwiftShader`（軟體渲染，本身就是強烈的自動化訊號，我沒有偽造它）** |
| **D** | **B 的 profile ＋ 真表單導航 POST**，讓瀏覽器自己去跑挑戰 | `~-1~`（全程） | POST 200（挑戰頁）→ 挑戰自行完成 → 重送 → **503 Service Unavailable** | — | **挑戰其實過了，但拿不到資料**（詳 §2.4） |
| **對照組** | 全新 profile ＋ 只做 www 暖身（**無**滑鼠／捲動），再 POST | `~-1~` | **POST 403** | `18.b13edc3d` | 重現 A：**純暖身不足以離開 403**，差別在「有無互動 + 是否回頭客 profile」 |

### 2.3 一句話讀這張表

> 403（冷客戶端）→ **空 body 200**（有互動、可疑）→ **`sec-cpt` 挑戰頁**（回頭客 profile）→ **503**（挑戰清關後）。
> **「像真人」買到的是「從硬擋升級為挑戰」，再買到「挑戰通過」，買不到「報價」。**

### 2.4 D 場景細節：挑戰真的跑完了，然後呢？

挑戰頁原始 HTML（節錄，實測抓取）：

```html
<title>Challenge Validation</title>
<script>function cp_clge_done(){document.sec_chlge_form.submit(document.location.href)}</script>
<script src="/pYA2AYfSy/UIWbFd/ZZQ/mf9c/LiUSAg/dEE/rKSwdSi8t" async defer></script>
<script>sessionStorage.setItem('data-duration', 30);</script>
...
<iframe id="sec-cpt-if" provider="crypto" challenge="eyJ0b2tlbiI6..."
```

沒有做任何破解、沒有改寫 payload，只是**讓瀏覽器照它自己的流程跑**，觀測到：

| 觀測 | 值 |
|---|---|
| `sec_cpt` cookie 尾碼 | `~1~`（挑戰中）→ **`~3~`（清關成功，與 Crawlex 記載的成功值一致）** |
| 網路層 | `POST /_sec/verify?provider=crypto` × **41**（全部 200，屬 PoW 重試）；`GET /_sec/cp_challenge/verify?formPost=true` → 200；隨機路徑 sensor POST × 3 → `201 {"success": true}` |
| 清關後導向 | `.../Booking/Search?akamai-challenge-resubmit=true` |
| 重送結果 | **HTTP 503，`<title>Service Unavailable</title>`**（console: `503 Service Unavailable`） |
| `_abck` | **全程 `~-1~`，一次都沒動** |
| 有無票價 | 無（`CI\d{3}` 命中 0 次） |

→ **「停在哪一步」有了精確答案：不是 403、不是挑戰，是「挑戰通過後的 origin 回應」。**

---

## 三、403 的性質判定：規則級還是指紋級？

### 3.1 判定

**它是「依客戶端驗證狀態分級的規則」，兩者都不是 —— 精確講：不是「路徑+方法」的靜態死規則（既有文件的結論需要修正），也不是單純的「指紋像不像真人」（指紋只是決定你被丟到哪一級）。**

證據（皆為本輪實測）：

| 觀察 | 支持的解釋 | 排除的解釋 |
|---|---|---|
| 同一 URL＋同一 POST body，在**冷客戶端**得到 403 `18.b13edc3d`，在**回頭客 profile** 得到 `sec-cpt` 挑戰頁 | 決策**依客戶端狀態**而變 → 不是靜態路徑規則 | 「`Booking/Search`+POST 一律 403」 |
| 冷客戶端即使是**真 Chromium**（對照組）也 403；但有互動的 C 得到空 200 | 客戶端品質會影響**分級** → 有指紋成分 | 「跟客戶端完全無關」 |
| 挑戰真的清關（`sec_cpt ~3~`）、請求真的被重送 | 這條路徑的 Bot Manager **是可通過的**，不是死路 | 「無論誰都擋」 |
| 重送後是 **503**，且 `_abck` 全程 `-1` | 通過 Bot Manager **≠** 拿到資料；`_abck` 在此部署不是儀表 | 「拿到 `~0~` 就能抓」 |

**信心程度**：
- 「POST 回應會依客戶端狀態在 403／空 200／挑戰頁／503 之間變化」→ **高**（四次獨立重現，含對照組）。
- 「403 不是靜態路徑規則」→ **高**。
- 「真人環境可以穩定通過並拿到報價」→ **低**（本輪唯一到達 origin 的一次是 503，見 §5 邊界）。

### 3.2 對既有文件的修正（記錄，不修改原檔）

| 既有文件說法 | 本輪證據 | 處置 |
|---|---|---|
| 「`_abck` 始終 `~-1~` ⇒ Bot Manager 沒認證我們」 | 挑戰已清關（`sec_cpt ~3~`）後 `_abck` 仍 `~-1~` | 該推論**降級為未證實**；此部署的儀表是 `sec_cpt` |
| 「換更真的瀏覽器也未必有幫助（真瀏覽器 ×3 全滅）」 | 真瀏覽器**可以**清 `sec-cpt`；但換來 503 | 前半修正為「真瀏覽器可通過挑戰」，結論方向（不可用）不變 |
| 「4 種客戶端全部 403，同一 Rule ID」 | 只要**回頭客 profile** 就不再是 403 | 「全部 403」只對「冷客戶端」成立 |

---

## 四、ROI：值不值得？

### 4.1 現在已經拿得到什麼

| 資料 | 來源 | 狀態 |
|---|---|---|
| 班表（班號／起降時刻／營運日／有效期間） | `info.flightmapper.net`（純 `requests`，200、SSR HTML） | ✅ 穩定、零反爬（[ci-automation-alternatives.md](ci-automation-alternatives.md) §A） |
| 實際營運佐證（今日看板、近 3 日實際起降） | `proflightsearch.com`（純 `requests`，200） | ✅ 可用，僅需避開其 403 的內部 API |
| 航線最低票價 | airtrfx `histogram-distribution` | ⚠️ **覆蓋率 38%**（[ci-coverage-probe.md](ci-coverage-probe.md)） |

### 4.2 bookingportal **理論上**能多拿到什麼

只有一種東西是現有來源給不了的：**flight-level 報價組合**（特定班號 × 艙等 × 剩位 × 含稅總價），而且是查詢當下的即時值。這是「訂票」語意，不是「看板」語意。

### 4.3 務實判斷：**不值得**

| 面向 | 評估 |
|---|---|
| 技術可行性 | 本輪證明「連挑戰都能過」，但**過完是 503**。要往下走必須逆向真正的表單參數集（數十個 hidden field／艙等／幣別／旅客結構），這已經不是「像真人」，是**逆向應用協定** |
| 穩定性 | Akamai 的腳本、路徑、挑戰 provider 會輪替（v2→v3、crypto/behavioural/adaptive）。任何一次輪替就全滅；本輪 41 次 verify 重試也顯示它在**主動對抗**自動化，不是靜態頁面 |
| 成本 | 每輪要開真瀏覽器、跑 30 秒 PoW 等待、處理隨機路徑 → 單位成本是 requests 方案的 3–4 個數量級 |
| 風險 | 見 §6（ToS／存取控制）。這是**明確違反服務條款**的行為，且需要持續加大對抗力度 |
| 替代性 | 若真的需要 flight-level 報價，**付費資料源（OAG／Amadeus／GDS）或旅行社聯盟 API** 是合法且穩定的路；或**人工查詢**（真人操作完全不受影響） |

→ **建議把 bookingportal 正式列為「不自動化」，並把「再確認真人可否通過」降級為『人工一次性驗證』**：需要報價時，人開瀏覽器看一眼。這比維持一條隨時會斷、且踩 ToS 的自動化管線便宜太多。

---

## 五、如果要繼續：正規路線

1. **票價覆蓋率優先，不是 bookingportal 優先。** 現有痛點是 airtrfx 只有 38% 覆蓋，不是「沒有 flight-level 報價」。可先評估：
   - 其他**公開**票價彙整（Kiwi/Trip.com 等的公開頁面 —— 但同樣要逐一確認 ToS 與反爬），
   - 或**接受 38% 並在 UI 標示「僅部分日期有報價」**（產品層解法，零風險）。
2. **需要正式 flight-level 報價 → 走商業授權**：OAG / Amadeus Self-Service / Travelport 等，或本地旅行社的 B2B 接口。成本可預期、有 SLA、不會因為 Akamai 改版而爆炸。
3. **不要**做的事：把 Playwright 偽裝成真人去過 Bot Manager。技術上本輪已證明「能過挑戰」，但這正是 ToS 禁止的規避存取控制行為，且後續每一步都要加大投入（PoW、隨機路徑、TLS 指紋），是典型的負 ROI 軍備競賽。
4. 若只是「要親眼確認某天的價格」→ **人工查詢**（可截圖留存）。這完全合法，也不脆弱。

---

## 六、風險聲明

- **服務條款**：華航官網／bookingportal 的使用條款禁止以自動化手段存取、爬取或繞過其安全機制。本輪在**診斷目的**下做了有限度、極低頻的實測（單一航線、單一使用者情境、無商業利用），但**任何把此路徑產品化的行為都會構成明確的 ToS 違反**。
- **存取控制**：`sec-cpt`／Bot Manager 是**技術性存取控制措施**。刻意規避它（偽造指紋、破解 PoW、代解挑戰）在多數司法管轄區可能觸及未經授權存取的法規風險，不只是民事違約。
- **本文件不是繞過指南**：內容止於「觀測到什麼、卡在哪、值不值得」。沒有提供可重用的 sensor 產生器、指紋偽造 patch、或挑戰求解程式；也沒有建議這麼做。
- **對第三方站台的禮貌**：本輪對 `info.flightmapper.net` / `proflightsearch.com` 的低頻抓取同樣應在正式排程前確認其 robots.txt／ToS。

---

## 七、誠實邊界（測到哪裡為止）

**已測（有原始輸出）**
1. 冷 context 直開 bookingportal + POST → 403 `18.b13edc3d`；該錯誤頁只載 Akamai pixel、無 `bmak`。
2. 持久化 profile 暖身 www 主站後 POST → 200 `sec-cpt` 挑戰頁（非 403）。
3. 加上真人滑鼠／捲動 → 200（**空 body**）。
4. 真表單導航 POST → 挑戰自動清關（`sec_cpt ~3~`、41 次 verify、sensor 201）→ 重送 **503**。
5. 對照組（只暖身、無互動）→ 403 重現。
6. `_abck` 在上述**每一個**情境（含挑戰清關後）都停在 `~-1~`。

**沒測 / 測不到（不敢當結論）**
- **住宅 IP × 真人環境**：依紀律不換 IP、不用代理，所以「家用網路能不能拿到報價」**仍未實測**。這是唯一真正的缺口。
- **真實表單參數集**：我只送了 5 個欄位（`B_LOCATION_1/E_LOCATION_1/B_DATE_1/B_DATE_2/TRIP_TYPE`）。**503 究竟是「挑戰後的 tarpit」還是「origin 因參數不完整而報錯」，本輪無法區分**。要區分就得逆向 portal 的完整表單，那已超出診斷範圍，我沒有做。
- **請求量揭露**：我方主動請求 14 次，但 D 場景的挑戰頁自行發出 **55 個子請求**（41 次 verify 重試）。嚴格說本輪網路負載高於「25 次」的字面額度 —— 這不是我額外發起的探索，而是通過挑戰的必然成本，但必須說明白。
- 未擷取螢幕截圖（為了不再增加請求），因此 `docs/research/artifacts/` 本次沒有新增檔案；挑戰頁與 503 的證據以 HTML 節錄形式留在 §2.4。
- 未測 `sbsd`（本輪站台用的是 `sec-cpt` crypto provider）。
- 未測其他航線／其他日期參數：403 的分級行為是否與 TPE–NRT／日期有關，未知（判斷為不太可能，但未驗證）。

**推論（明確標記）**
- 「家用網路 ＋ 長期使用的 Chrome profile ＋ 真人操作，大概率能通過挑戰並看到報價」—— 由「這是官網唯一訂票入口、每天有真人使用」＋ 本輪「真瀏覽器確實能清關」反推。**但本輪最後一哩是 503，所以連這條推論都還差一步驗證。**
- 「503 主因是參數不完整或邊緣 tarpit」—— 兩種都可能，未隔離變因。

---

## 八、給使用者的一句話回答

> **「不要無痕」這個直覺是對的**：無痕（冷 context）就是拿到 403 的那一種；回頭客 profile ＋ 互動確實把 403 變成「挑戰」甚至「挑戰通過」。**但「像真人」不是通關密語，只是把你從『門口被擋』送到『門內 503』。**
> 真正卡住的是：這個端點需要通過 Akamai 的動態挑戰（每次 30 秒 PoW ＋ 數十次 verify），通過後還要送對一整套你必須逆向的訂票參數 —— 而且隨時會被下一版偵測改掉。**資料面完全不值得，班表與大部分票價都已經有合法、穩定的來源。**
> 唯一乾淨的驗證方式：**請人在家用網路開一次華航訂票頁看價格**。那不叫繞過，那叫使用。

---

*文件性質：一次性診斷快照。樣本為 2026-10-31 單一時點、單一航線、單一出口 IP。Akamai 的規則、挑戰 provider、腳本 id 與第三方站台結構隨時可能變動；`_abck`／`sec_cpt` 尾碼語意屬社群共識而非官方文件，本文件已標註來源與信心程度。*

---

## 附錄：測試網路環境判定 —— 「未測住宅 IP」這個 caveat 是否成立？

> 查證日期：2026-10-31 12:5x（台北時間，與原診斷同一時段內完成）
> 查證方式：**只讀環境**（出口 IP echo 服務、ASN／組織查詢、路由與 VPN／代理設定、10/31 診斷腳本與網路設定時序）。**未對華航任何端點發出請求**，未更換 IP、未使用代理。
> 隱私：本節只記錄 ASN 與組織層級資訊，**不記錄完整對外 IP**。

### A.1 判定結果

| 項目 | 值 |
|---|---|
| ASN | **AS3462** |
| 組織 | `Data Communication Business Group` / **Chunghwa Telecom Co., Ltd.（HiNet）** |
| 反向 DNS 網域 | `hinet-ip.hinet.net`（HiNet 配發線路的命名，非雲端業者網域） |
| 地理 | 台灣（台北／新北板橋一帶；多個來源不一致屬正常 GeoIP 誤差） |
| `hosting` / `proxy` / `mobile` 標記 | **false / false / false**（ip-api 逐項回報） |
| 出口路徑 | 無線網卡 `wlp3s0`（192.168.0.x）→ 家用路由器 `192.168.0.1` 做 NAT → HiNet |
| 代理 | **無**。`http_proxy` / `https_proxy` / `all_proxy` 環境變數全空，shell rc／`/etc/environment`／git config 皆無 proxy |
| VPN／exit node | **無**。Tailscale 僅作 mesh（`ExitNode: false`、`RouteAll: false`、`ExitNodeID` 空）；系統中沒有 wireguard／openvpn／clash／sing-box 等服務在跑 |
| 其他 | Cloudflare trace 顯示 `colo=TPE`（台灣本地出口），與 GeoIP 一致 |

**判定：本機出口是住宅 ISP（中華電信 HiNet）的一般用戶線路，不是機房／雲端／資料中心 IP。**

**判定依據與信心程度**：三個獨立來源（ipinfo.io、ip-api.com、ipwho.is）在 ASN 與 ISP 名上完全一致為 AS3462／Chunghwa Telecom；`hosting=false`、`proxy=false` 的明確標記與反向 DNS 的 `hinet-ip.hinet.net` 彼此獨立地指向同一結論；再以 `ip route`（家用路由器 NAT）、無 proxy 環境變數、Tailscale exit node 關閉三重交叉排除「實際 egress 被繞去別處」。→ **信心：高**。

### A.2 10/31 測試當時，出口也是住宅嗎？

這一題必須回答，否則「今天是住宅」不等於「當時是住宅」。

| 證據 | 內容 |
|---|---|
| 主機同一台 | 診斷腳本 `/tmp/ci_diag_{a,b2,bc,ctrl,d,d2}.py` 的 mtime 為 **10/31 12:43–12:48**，與文件記載的測試時段（12:2x–12:5x）完全吻合；文件引用的瀏覽器路徑 `/root/.cache/ms-playwright/chromium-1243/...` 就是本機路徑 |
| 腳本內無代理 | `grep -i "proxy|vpn|socks|tunnel"` 對 6 支診斷腳本 → **零命中** |
| 網路設定未變動 | NetworkManager 只有一條連線 `tplink-wifi`（家用 WiFi），建立於 10/3，10/30–10/31 的日誌中沒有 VPN／PPP 啟用、也沒有切換連線的紀錄；tailscaled 同期日誌只有 control plane 重連，無 exit node 變更 |
| 環境變數無代理 | 現在與當時皆無 proxy 變數（無從改動的痕跡） |

→ **推論：10/31 的四種情境（403／空 200／`sec-cpt` 挑戰／503）全部是從住宅 HiNet IP 發出的。信心：高。**

唯一殘餘不確定性（誠實標記）：10/31 當下**沒有留存出口 IP 的日誌**，所以嚴格說是「同主機 ＋ 同網路設定 ＋ 無任何改路徑機制」的推論，而不是「當時 IP 的逐筆紀錄」。另外，若日後 IP 因 PPPoE 重新連線而變動，**變動前後仍會落在 AS3462 HiNet 的住宅位址池內**，不影響結論。

### A.3 對既有結論的影響：**撤銷 caveat**

- 原文 §七「住宅 IP × 真人環境 ……**仍未實測**。這是唯一真正的缺口」以及 [ci-automation-alternatives.md](ci-automation-alternatives.md) §B.5 的同款 caveat：**應予撤銷**。那個缺口不存在，因為測試從來就不是從機房發出的。
- 原文開頭環境註記的「**單一出口 IP（機房）**」、以及 alternatives §B.4「我們是機房 IP」：**為未經量測的假設，事實錯誤**。（依本次任務邊界，未改寫原句，僅在此更正；閱讀時請以本附錄為準。）
- **結論強度提升**：既然四種回應都是住宅 IP 拿到的，「換成真實使用者的網路環境也拿不到 bookingportal 報價」不再是推論，而是**已被觀測覆蓋的情境**。原本「IP 這一維未測」的變因，現在**已測且結果為住宅**。
- **但不要把撤銷過度延伸**：撤銷的是「IP 變因未測」，**不是**「真人環境已測」。10/31 的客戶端仍帶著其他非 IP 的自動化特徵（見 A.5），所以「真人＋真 Chrome＋有 GPU 的桌機」這個組合**仍未實測**。正確的新說法是：
  > IP 這一維不再是不確定項 —— 住宅 IP 已測，且拿不到報價。仍未測的是「非自動化指紋」那一維。

### A.4 對「503 是 tarpit 還是參數不足」推論的更新

**先講強度：住宅 IP 這個事實會讓「IP 信譽導致 503」這個解釋顯著下降，但不足以定案。**

| 解釋 | 住宅 IP 事實的影響 |
|---|---|
| (a) 資料中心 ASN 被 Akamai 降權 → 503 | **大幅下降**。沒有資料中心 ASN 可被降權 |
| (b) 挑戰雖清關，但指紋／行為分數仍低 → edge tarpit（回 503 或空 body） | **仍成立，且相對上升**。住宅 IP 只解決網路層，`SwiftShader` 軟體渲染等訊號仍在（A.5） |
| (c) origin 端因表單參數不完整而拒絕（只送 5 個欄位） | **仍成立**。與 IP 無關，未被本次查證影響 |
| (d) origin 當時真的有服務異常 | 無法排除，但與「挑戰後才出現」的時序不太吻合 |

**目前的排序（中等信心）**：(c) ≈ (b) > (d) > (a)。也就是說，503 **比原本的判斷更像「origin 端拒絕／邊緣 tarpit」，而不是 IP 信譽問題**，但 (b) 與 (c) 仍無法分離。

**要真正定案，需要下列證據（任一即可大幅收斂）**：

1. **完整表單導航**：讓瀏覽器自己在 portal 表單填齊所有 hidden field／艙等／旅客結構後導航送出。若 503 消失並出現票價 → (c) 成立；若仍 503 → (c) 被排除，(b) 上升。（此為逆向應用協定，已超出診斷範圍，本輪未做。）
2. **同住宅網路、真人手動、實體桌機＋品牌版 Chrome＋有 GPU**：這是黃金標準，且完全合法（那就是「使用」）。若人能拿到報價 → 排除 (d) 與 (b) 的多數成分，坐實 (c)／自動化指紋；若人也是 503 → 該航線／日期在 origin 端真的有問題。
3. **回應特徵分析**（不需逆向表單）：比較 503 與 200 的 response headers／是否有 Akamai `Reference #`、回應延遲（tarpit 通常有延遲）、以及同 profile 對 bookingportal 其他 **GET** 端點是否也 503（整體不可用 → origin；(d)／(b) 上升）。
4. **edge vs origin 定位**：503 若由 Akamai 邊緣產生，通常不帶 origin 的 server header／會有 Akamai 專屬標頭；若帶 origin 特徵則指向 (c)／(d)。

在沒有上述證據前，**不要**把 503 寫成「住宅 IP 也被 origin 拒絕」的定論。

### A.5 瀏覽器與指紋：一個需要更正的事實

| 項目 | 實情 |
|---|---|
| 「`/usr/bin/chromium`」 | 它是 **symlink** → `/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`。版本字串為 **`Google Chrome for Testing 153.0.8010.12`** |
| 是 Debian chromium 嗎？ | **不是** |
| 是品牌版 Google Chrome 嗎？ | **不是**。Chrome for Testing 是 Chromium 專案為測試發佈的官方 build，缺少品牌版的一些元件與 `Google Chrome` brand token |
| 有頭模式？ | **是**，`xvfb-run` ＋ `headless=False`。實測其 UA 為 `Mozilla/5.0 (X11; Linux x86_64) ... Chrome/153.0.0.0 Safari/537.36`（**不是** `HeadlessChrome`），`navigator.webdriver=false` |
| 剩餘的自動化訊號 | ① **WebGL renderer = `SwiftShader`（軟體渲染）** —— 因為 xvfb 無 GPU，這是強訊號，且原診斷明確表示沒有偽造它；② Chrome for Testing 的 brand 集合與品牌版 Chrome 不同；③ Linux X11 桌面（比例低但非罕見）；④ 即使 CDP 未洩漏，程序仍是自動化啟動、無真實使用歷史 |

**「改用真實 Google Chrome ＋ 持久化 profile」還有未測空間嗎？**
- **有，但只剩「最後一哩」的驗證價值，不是自動化路線的可行性。** 真正未測的組合是：**品牌版 Google Chrome（非 for-Testing build）＋ 持久化 profile ＋ 有 GPU 的實體桌機（非 xvfb/SwiftShader）＋ 真人手動操作**。
- 這個組合的意義是**判別 (b) 與 (c)**（見 A.4），而不是「找到可排程的抓法」。原文件 §四／§五 的 ROI 結論（不自動化、走商業授權或人工查詢）**不因本附錄改變**：即使真人能拿到報價，也證明不了自動化能穩定拿到，反而更清楚顯示「通過與否取決於難以複製的真人／硬體特徵」。

### A.6 一句話

> **本機是住宅 HiNet（AS3462）網路，不是機房。** 因此原文件「住宅 IP 未測」的 caveat **應予撤銷** —— 403／空 200／挑戰／503 本來就都是從住宅 IP 拿到的，這讓「換到真實使用者網路也拿不到報價」成為**已觀測**而非推論。**但**仍未測的是「非自動化指紋」（品牌版 Chrome ＋ GPU ＋ 真人），所以 503 的成因（tarpit vs 參數不足）依然未定案。

---

## 附錄：現狀速查 —— 「開瀏覽器會 503」正確嗎？

> 本節只做**既有證據的重新切片**，**未對華航任何端點發出新請求**（本輪補測次數 = **0**）。
> 主要原始證據：`/tmp/d2.json`（55 筆網路日誌＋console）、`/tmp/b.json`、`/tmp/c.json`（情境 B／C 的原始輸出）、[ci-automation-alternatives.md](ci-automation-alternatives.md) §B.1／§B.3／§B.4。

### B.1 一句話

**「開瀏覽器會 503」這個說法不精確 —— 精確的是「對訂票搜尋端點送出 POST、被挑戰頁接手、挑戰清關後由瀏覽器自動重送的那一次」會 503。**
在已測的所有情境裡，**沒有一次 GET（開頁面）出現 503**：GET 只出現過 200、200→302、405。

### B.2 503 精確到請求層級

| 項目 | 值 |
|---|---|
| URL（503 那一次） | `https://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search**?akamai-challenge-resubmit=true**` |
| HTTP 方法 | **未在網路日誌中單獨記錄 method**。機制上是 `document.sec_chlge_form.submit(document.location.href)` 的重送；原始請求是**文件級 POST 表單導航** → 判為 **POST 的重送**（此處標記為「判為」，不是直讀） |
| payload 形狀 | `application/x-www-form-urlencoded`，**只有 5 個欄位**：`B_LOCATION_1=TPE&E_LOCATION_1=NRT&B_DATE_1=202611140000&B_DATE_2=202611150000&TRIP_TYPE=R`（≠ portal 真實表單的完整欄位集） |
| 是第幾次送出 | **第 2 次**。第 1 次 POST → `200` ＋ `sec-cpt` 挑戰頁（`<title>Challenge Validation</title>`）；挑戰自行跑完（`sec_cpt` `~1~`→**`~3~`**、`/_sec/verify?provider=crypto` **41 次 200**、sensor POST 3 次 201、`GET /_sec/cp_challenge/verify?formPost=true` 1 次 200，共 **55** 個子請求）→ 瀏覽器自動重送（第 2 次）→ **503** |
| 503 的形狀 | 回應 body 為 `<title>Service Unavailable</title>` 錯誤頁；console：`error: Failed to load resource: the server responded with a status of 503 (Service Unavailable)` |
| 有無票價 | 無（`CI\d{3}` 命中 0 次） |
| 重試次數 | **1 次就停**（沒有第 3 次重送） |
| 來源 | `/tmp/d2.json`（`net` 55 筆、`console`、`after_35s.title = "Service Unavailable"`、`final.url` 帶 `akamai-challenge-resubmit=true`、`final.sec_cpt = "3"`）；文件 §2.4 |

**關鍵限定**：503 不是「每個 POST 都會」，也不是「清關後的每個請求都會」——
- 情境 C（暖身＋真人滑鼠／捲動、**沒有**觸發挑戰）的同一個 POST → `200`、body 空，**不是 503**。
- 情境 A／對照組（冷客戶端／只暖身無互動）的同一個 POST → `403` `18.b13edc3d`，**不是 503**。
→ **503 只在「已經進入挑戰流程、且挑戰已清關、請求被自動重送」這一條腿上被觀測到（n=1）。**

### B.3 「開瀏覽器」要拆成四個不同動作

| 使用者可能的意思 | 實際會發生什麼 | 證據狀態 |
|---|---|---|
| ① 人開 `flights.china-airlines.com` | 這台子網域在 **Cloudflare**（不是 Akamai），GET 200 | 自動化 requests 實測 **200**（#7）。**真人未測** |
| ② 人開 `www.china-airlines.com/tw/zh` | 200，正常主站 | 自動化 requests **200**（#8）＋ 自動化真 Chromium **200**「首頁 - 中華航空」（#13、`/tmp/b.json` `www_status=200`）。**真人未測** |
| ③ 人開 `bookingportal.china-airlines.com/eRetailPortal/` | 導航 200，但**落點是 `/maintain/page-400.html`（「Not Found」錯誤頁）**，不是 503、也不是可查票的表單 | 自動化 requests（#4）與自動化真 Chromium（A／B／C 三情境全部）都是「200 → page-400」。**真人未測** |
| ④ 人**填表送出搜尋**（＝真正查票的動作） | **未測**。自動化的等價動作在情境 D 拿到 503（見 B.2），但那是自動化客戶端 | **未測**（從來沒有人實測過真人手動查票） |
| ⑤ 人**直接把 `/Booking.svc/Booking/Search` 貼進網址列** | 這是 POST-only 端點，GET 會是 405 | origin 層 `GET → 405 Method Not Allowed, Allow: POST` 已由 requests 實測（#9）。**瀏覽器導航那一格未留存輸出**（`ci_diag_d.py` 曾 goto，該次 JSON 未保存）→ **標「未測」** |

### B.4 速查表（請求層級）

| 情境 | 動作 | 是否自動化 | 觀察到的 status | 來源 |
|---|---|---|---|---|
| 暖身 | GET `www.china-airlines.com/tw/zh`（requests） | 自動化 | **200** | alternatives §B.3 #8 |
| 暖身 | GET `www.china-airlines.com/tw/zh`（真 Chromium） | 自動化 | **200** | alternatives §B.3 #13；`b.json`/`c.json` `www_status` |
| 暖身 | GET `flights.china-airlines.com/zh-tw`（Cloudflare 子網域） | 自動化 | **200** | alternatives §B.3 #7 |
| 瀏覽 | GET `bookingportal…/eRetailPortal/`（requests） | 自動化 | **200 → 302 `/maintain/page-400.html`** | alternatives §B.3 #4 |
| 瀏覽 | GET `bookingportal…/eRetailPortal/`（真 Chromium，冷／暖 profile 皆同） | 自動化 | **導航 200，落點 page-400.html**（情境 A／B／C） | §2.2；`b.json` `portal_status=200`＋`portal_url=…page-400.html` |
| 探測 | GET `…/Booking.svc/Booking/Search` | 自動化（requests） | **405**（`Allow: POST`，已到 origin） | alternatives §B.4 #9 |
| 探測 | GET `…/Booking.svc/Booking/Search`（瀏覽器導航） | 自動化 | **未測**（輸出未留存） | — |
| 探測 | POST `…/Booking/NoSuchMethodXYZ` | 自動化 | **404**（POST 在該 host 是通的） | alternatives §B.4 #16 |
| 搜尋 | POST `…/Booking/Search`（冷 context，新鮮 profile） | 自動化 | **403** `18.b13edc3d` | §2.2 A；alternatives §B.1 #5/#6/#12/#14 |
| 搜尋 | POST 同端點（暖身但**無**互動＝對照組） | 自動化 | **403** `18.b13edc3d` | §2.2 對照組 |
| 搜尋 | POST 同端點（暖身＋真人滑鼠／捲動，未觸發挑戰） | 自動化 | **200，body 空** | §2.2 C；`c.json` `post_status=200` |
| 搜尋 | POST 同端點（回頭客 profile，**fetch**） | 自動化 | **200** ＝ `sec-cpt` 挑戰頁本體 | §2.2 B；`b.json` `post_status=200` |
| 搜尋 | 真表單導航 POST → 第 1 次 | 自動化 | **200** ＝ `sec-cpt` 挑戰頁 | §2.4；`d2.json` `form_post_status=200` |
| 搜尋 | 挑戰清關後**自動重送**（第 2 次，帶 `?akamai-challenge-resubmit=true`） | 自動化 | **503 `Service Unavailable`** | §2.4；`d2.json` `console`／`after_35s`／`final` |
| 查票 | 真人手動開 bookingportal 並送出搜尋 | **人類** | **未測** | — |

### B.5 結論（分開講，不混談）

**(1) 自動化瀏覽器「開頁面」→ 不會 503。信心：高。**
在 `alternatives` 的 20 次 ＋ 診斷輪的所有 GET（www、flights、portal root、Search 的 GET）中，**沒有一次出現 503**；GET 的回應集合只有 `200`、`200→302 page-400`、`405`。

**(2) 自動化瀏覽器「送出搜尋」→ 依客戶端狀態分四種，其中一種是 503。信心：高（503 位置）／中（成因）。**
`403`（冷）→ `200 空 body`（有互動、未觸發挑戰）→ `200 挑戰頁` → **`503`（挑戰清關後的重送）**。

**(3) 真人手動查票會不會 503 → 未測。信心：對「會不會」低（n=0）。**
- 從來沒有人實測過：**真人、品牌版 Chrome、有 GPU 的實體桌機、住宅網路、手動填表送搜尋**。這一格是空的（§七「沒測」、附錄 A.3／A.5 也把「非自動化指紋」列為唯一仍未測的維度）。
- 已知的只有：**住宅 IP 這一維已測**（附錄 A.1–A.3），而 503 是從住宅 IP 拿到的 → 所以「503 是機房 IP 害的」這個解釋已被排除，但「503 是自動化指紋害的」與「503 是表單參數不完整害的」、「origin 當時真的異常」三種都還活著（附錄 A.4：`(c) ≈ (b) > (d) > (a)`）。
- **對使用者這一題最要緊的一點**：本輪送出的 payload **只有 5 個欄位**，不是 portal 真實表單的完整參數集。若 503 的成因是「origin 因參數不完整而拒絕」，那**真人用真實表單就不會遇到 503**；若成因是 tarpit／指紋，真人才可能也會遇到。這兩者**未分離**。
- 因此正確講法是：**「503 只在自動化路徑被觀測到一次，且發生在挑戰清關後的重送；真人手動查票從未被實測過，不能斷言會或不會 503。」**

**(4) 一句可引用的版本**
> 不是「開瀏覽器會 503」，而是「**自動化送出訂票搜尋、且被 Akamai 挑戰頁接手後**，重送那一次回 503」。**逛頁面不會**；**真人手動查票沒有任何人測過**。

### B.6 「未測」清單（不用推論填空）

1. **真人手動**在 bookingportal 填表送搜尋（品牌版 Chrome／實體 GPU／住宅網路）→ **未測**（本節最重要的一格）。
2. 真人手動開 `flights.china-airlines.com`、`www.china-airlines.com` → **未測**（自動化版本的 200 已測，但那不是真人證據）。
3. 真人手動直接把 `…/Booking/Search` 貼進網址列 → **未測**（origin 的 `GET → 405` 已測，但瀏覽器導航那一格無留存輸出）。
4. **完整表單參數集**下的 POST（hidden field／艙等／幣別／旅客結構）→ **未測**（本輪只送 5 個欄位）。
5. 503 的 **response headers**（是否帶 Akamai `Reference #`、是否為 edge 產生）→ **未擷取**，因此「edge tarpit vs origin 拒絕」仍未定位。
6. 503 的**重送 HTTP method** → 日誌未單獨記錄（判為 POST）。
7. 503 是否可重現（同一 profile／同一日期再送一次）→ **未測**（n=1，且補測會再觸發一次 55 個子請求的挑戰，依本輪紀律不補）。
8. `sbsd` 第二層挑戰、其他航線／日期是否同樣 → **未測**。

### B.7 本節的補測紀錄

**0 次請求。** 所有欄位都由既有證據（`/tmp/d2.json`、`/tmp/b.json`、`/tmp/c.json`＋兩份研究文件的實測節）填出；不足的格子一律標「未測」，未以推論補值。
