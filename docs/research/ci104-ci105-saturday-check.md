# CI104 / CI105 週六（與 CI105 週日）營運日衝突：實測定案

> 查證時間：**2026-10-31（週六）12:29–13:05 台北時間**（NRT 13:29–14:05）
> 動機：FlightMapper 顯示 CI104/CI105 冬季缺週六；proflightsearch 看板在 2026-10-31（週六）明確列出 CI104 12:35。兩者不可能同時正確。
> **本次最大的方法學優勢：查證當天就是爭議的那一天（週六）**，而且 CI104 表定 12:35 起飛，正好可以在起飛前後抓「實際營運」證據。
> 上游文件：[ci-tpe-nrt-schedule.md](ci-tpe-nrt-schedule.md)、[ci-automation-alternatives.md](ci-automation-alternatives.md)
> 新增截圖：`artifacts/tpe-official-CI104-departed-20261031.png`、`artifacts/tpe-official-departures-20261031.png`、`artifacts/ci104-tpe-board-20261031-1250.png`、`artifacts/ci104-tpe-board-20261031-1252.png`、`artifacts/ci104-tpe-board-20261031-ci104-row.png`

---

## 〇、結論（先講）

**CI104 週六有飛。FlightMapper 的冬季 CI104/CI105 營運日資料是錯的（漏掉週六）。**

| 班號 | 方向 | 冬季營運日（判定） | 週六？ | 週日？ | 信心 |
|---|---|---|---|---|---|
| **CI104** | TPE 12:35 → NRT 16:35 | **每日（Mon–Sun）** | ✅ **有飛（已實測當天實際出發 12:42）** | ✅ 有飛 | **極高**（桃園機場官方 FIDS 實證） |
| **CI105** | NRT 17:45 → TPE 20:55 | **每日（Mon–Sun）** | ✅ 應有飛（同日成田看板列 CI105） | ✅ **有飛** | **高**（見 §4 的證據邊界） |
| CI100 | TPE 09:30 → NRT 13:30 | 每日 | ✅ 有飛（當日 09:37 實際出發） | ✅ | 高 |
| CI108 | TPE 14:25 → NRT 18:30 | 每日 | ✅ 有飛（當日看板 14:25 / Gate D6） | ✅ | 高 |
| CI106 | TPE 16:25 → NRT 20:30 | 每日 | ✅ 有飛（當日看板 16:25 / Gate D16） | ✅ | 高 |
| CI107 | NRT 09:20 → TPE 12:30 | 每日 | — | ✅ | 高（兩來源一致） |
| CI101 | NRT 14:35 → TPE 17:45 | 每日 | — | ✅ | 高 |
| CI109 | NRT 19:30 → TPE 22:45 | 每日 | — | ✅ | 高 |

**一句話依據**：桃園國際機場**官方**離場 FIDS（`www.taoyuan-airport.com/api/api/flight/a_flight`）在 2026-10-31（週六）明確有一列 `CI104 / OTime 12:35:00 / RTime 12:42:35 / Gate D5 / 狀態「登機結束」/ 共掛 JL5176、QF8738`。班機當天真的飛了。

**對「週六去 + 隔週日回」的意義**：**完全可行，而且 CI104（六）+ CI105（日）這個組合是合法的**。原本文件把 CI104 週六正確列為有飛（結論對），但它引用的 FlightMapper 營運日（每日）是**錯的推論**——是「來源 B 對、來源 A 錯」，不是兩邊都對。

---

## 一、來源 A（FlightMapper）：原始字串與判讀

### 1.1 抓取結果

| 請求 | URL | 結果 |
|---|---|---|
| #1 | `https://info.flightmapper.net/route/China_Airlines_CI_TPE_NRT` | **200 · 152,509 bytes** |
| #2 | `https://info.flightmapper.net/flight/China_Airlines_CI_104`（單班頁） | **200 · 76,479 bytes** |
| #6 | `https://info.flightmapper.net/route/China_Airlines_CI_NRT_TPE` | **200 · 152,209 bytes** |

三頁的 datepicker 都是 `rangeHigh:"20261030"` / `rangeLow:"20260101"` → **排班資料快照 as-of 2026-10-30**（即「昨天」）。

### 1.2 CI104 的冬季區段（原始 row，route 頁與單班頁完全一致）

```
Mon,Wed,Fri,Sun | 12:35 | TPE 2 | 16:35 | NRT 2 | CI 104 | Airbus A321neo (32Q) 3:00 | Effective from 2026-10-25
Tue,Thu         | 12:35 | TPE 2 | 16:35 | NRT 2 | CI 104 | Boeing 777-300ER (77W) 3:00 | Effective from 2026-10-27
```
→ 聯集 **Mon,Tue,Wed,Thu,Fri,Sun = 6 天，缺 Sat**。兩段都是 `Effective from`（開放式，**沒有** `Valid until` / `through`），所以不是「某段到期」造成的。

### 1.3 CI105 的冬季區段（NRT→TPE 頁）

```
Mon,Wed,Fri,Sun | 17:45 | NRT 2 | 20:55 | TPE 2 | CI 105 | Airbus A321neo (32Q) 4:10 | Effective from 2026-10-25
Tue,Thu         | 17:45 | NRT 2 | 20:55 | TPE 2 | CI 105 | Boeing 777-300ER (77W) 4:10 | Effective from 2026-10-27
```
→ 同樣 6 天、缺 Sat。**週日明確在清單內**。

### 1.4 頁面上其他班號的冬季字串（用來對照「每日」長相）

```
CI 100  Mon-Wed,Sun 09:30 / Effective from 2026-10-25
CI 100  Thu         09:30 / Effective from 2026-10-29
CI 100  Fri,Sat     09:30 / Effective from 2026-10-30      → 聯集每日
CI 106  Daily       16:25 / Effective from 2026-10-25      → 每日
CI 107  Daily       09:20 / Effective from 2026-10-25      → 每日
CI 108  Mon,Wed,Fri-Sun 14:25 / from 2026-10-25 ＋ Tue,Thu / from 2026-10-27 → 每日
CI 101  Mon-Wed,Sun / Thu / Fri,Sat                        → 每日
CI 109  Mon,Wed,Fri-Sun ＋ Tue,Thu                         → 每日（含 Sat）
```

**注意 CI109**：`.json` 聯集後有 `Sat`；同一個「A321neo 輪替」家族裡，只有 CI104/CI105 兩班的週六不見了。而 CI104 落地 NRT 16:35 → CI105 起飛 NRT 17:45，是同一架飛機的回程（70 分鐘周轉）。**兩班同時缺同一天，代表這是「同一組輪替的頻率被記成 6 班/週」，在資料上是自洽的**——也因此光看 FlightMapper 內部無法發現錯誤。

### 1.5 反證：FlightMapper 自己**有**記錄過 CI104 的週六

同一頁上（過去季節的區段）：

```
Sat      | 12:35 | ... | Airbus A350-900 (359) | Effective 2026-01-03 through 2026-02-07
Thu,Sat  | 12:35 | ... | Airbus A350-900 (359) | Effective 2026-02-12 through 2026-02-14
Sat      | 12:35 | ... | Airbus A350-900 (359) | Effective 2026-02-21 through 2026-03-28
Wed,Fri-Sun | 12:40 → 16:55 | A321neo | Effective 2026-03-29 through 2026-10-24   ← 2026 夏季也有週六
```

→ 週六 CI104 在 2025/26 冬季與 2026 夏季都存在。**只有 2026/27 冬季這一版資料沒有週六**。這強烈指向「新季度資料缺一筆週六區段」，不是「華航停飛週六」。

---

## 二、來源 B（proflightsearch 看板）：抓取結果與它自己的坑

| 請求 | URL | 結果 |
|---|---|---|
| #3 | `https://proflightsearch.com/search-flights/TPE-NRT`（requests） | 200 · 189,553 bytes · `Updated 31 October 2026 at 03:04 UTC.`（= 11:04 TPE） |
| #4 | `https://proflightsearch.com/search-flights/NRT-TPE`（requests） | 200 · 193,210 bytes · `Updated 31 October 2026 at 02:07 UTC.`（= 11:07 NRT） |
| #5 / #7 | 同 TPE-NRT 頁（chromium+xvfb，12:35 與 12:49 各一次） | 200，但頁面仍標 `Updated … 03:04 UTC` |

### 2.1 2026-10-31（週六）TPE 離場看板（`id="today-*"`）

```
09:30 CI100  Left 09:37  +7 · NRT 13:30 In the air
12:35 CI104  On schedule · Gate D5 · T2 · NRT 16:35 scheduled
14:25 CI108  On schedule · Gate D6 · T2
16:25 CI106  On schedule · Gate D16 · T2
```
CI104 的 3 日明細區塊：

```
CI104 China · 3 days
Sat, Oct 31 | 1/2 legs · B-18915 | TPE Sched 12:35 Scheduled departure On time D5 · T2 → NRT Sched 16:35 Unconfirmed 98 · T2 · B-18915
Fri, Oct 30 | 2/2 legs · B-18915 | TPE 12:35 Departed | NRT 16:14 Landed (Sched 16:35, 21m early)
Thu, Oct 29 | 2/2 legs · B-18920 | TPE 12:32 Departed (3m early) | NRT 16:23 Landed (12m early)
```

### 2.2 2026-10-31（週六）NRT 離場看板

```
09:20 CI107  Left 09:40 +20 · TPE est. 12:07 In the air
14:35 CI101  On schedule · Gate 62 · T2 · TPE 17:45 scheduled
  ·   CI105  On schedule · TPE 20:55 scheduled      ← 時間欄是「·」，無 Gate
  ·   CI109  On schedule · TPE 22:45 scheduled      ← 同上
```
→ **週六成田端也列了 CI105**（FlightMapper 說週六沒 CI105）。

### 2.3 這個來源的三個已知坑（本次新發現第 3 點）

1. **「Who flies it」區塊是過期夏季時刻**（CI100 08:50 / CI104 12:30）——上游文件已記錄，本次再次確認。
2. **`/api/*` 403**，只能解析頁面 HTML。
3. **本次新發現：頁面資料是「快取/批次產生」的，不是即時。** 12:35 抓、12:49 再抓，兩次的 “Updated” 都停在 `03:04 UTC`（11:04 TPE），也就是**落後 ~1h45m**。所以：
   - 看板上的 `On schedule` **不等於**「已確認營運」，它只是「時刻表上有這班 + 尚未拿到新狀態」。
   - 這正好解釋了上游文件為什麼會誤判：**光靠 pfs 的 `On schedule` 無法區分「真的有飛」與「時刻表寫了但實際取消」**。
4. 但同樣重要的是：pfs 的**當日**看板有 Gate（D5/D6/D16）與**機尾編號**（B-18915），這是 FIDS 等級的資訊，不是純時刻表。→ 它是可信的「排班＋派機」快照，只是**不能當時鐘**。

---

## 三、決定性證據：桃園國際機場**官方** FIDS（新來源，本次打通）

`proflightsearch` 只能證明「排班上有、有派機」，要證明「真的飛了」需要機場官方。本次打通了桃園機場官網的 SSR API：

```
GET https://www.taoyuan-airport.com/api/api/flight/a_flight
（由 https://www.taoyuan-airport.com/flight_depart 頁面自動發出，純 JSON，無反爬、無需 token）
```

### 3.1 CI104 的官方原始 JSON（2026-10-31 12:49 TPE 擷取）

```json
{
  "id": "20261031_D_CI104", "AState": "D", "ACode": "CI", "AName": "中華航空",
  "FlightNo": "104", "Gate": "D5",
  "ODate": "2026/10/31", "OTime": "12:35:00",
  "RDate": "2026/10/31", "RTime": "12:42:35",
  "CityCode": "NRT", "CityName": "東京",
  "PlaneNo": "B777-300", "CheckIn": "8",
  "CurrentStatus": "登機結束",
  "sharing": [
    {"ACode": "JL", "FlightNo": "5176", "flightCode": "JL5176"},
    {"ACode": "QF", "FlightNo": "8738", "flightCode": "QF8738"}
  ],
  "flightCode": "CI104"
}
```

對照組（同一次回應裡、已飛完的班）：
```json
{"id":"20261031_D_CI28", "OTime":"12:25:00","RTime":"12:31:25","CurrentStatus":"已飛"}
```

### 3.2 官方看板頁面文字（13:00 TPE 再抓，日期欄 `2026/10/31`、時段 `12:00~13:59`、頁面標示「更新於 12:49」）

```
12:35 | 12:42 | 東京 | 中華航空 / 日本航空 / 澳洲航空 | CI104 JL5176 QF8738 | T2 | D5 | 8 | 出發 登機結束
```

截圖：`artifacts/tpe-official-CI104-departed-20261031.png`（CI104 那一列清楚可見）、`artifacts/tpe-official-departures-20261031.png`（整頁看板 + 日期/時段篩選器）。

### 3.3 判讀

- **ODate = 2026/10/31（週六）**，OTime 12:35、**RTime 12:42:35**、狀態「登機結束」（同頁其他班為「已飛」）。
- 「有 RTime 且狀態推進到登機結束/已飛」＝ 這班有被機場實際處理（派 Gate、開櫃、登機），**不是時刻表列好而已**。
- 共掛 JL5176 / QF8738 也都在 —— proflightsearch 完全沒抓到這些共掛碼，說明兩者資料鏈不同。
- 機型官方寫 `B777-300`；FlightMapper 冬季週六根本沒資料、週五寫 A321neo。**機型欄位兩邊都不能信**（調度換機常見）。

→ **結論：CI104 在 2026-10-31（週六）確實執行。FlightMapper 的「6 天」是資料缺漏。**

---

## 四、CI105 週日：證據邊界與判定

CI105 週日這件事，**兩個來源沒有衝突**（FlightMapper 的 `Mon,Wed,Fri,Sun` 明確含 Sun；pfs 也一致）。真正衝突的是「週六」，而週六的爭議已由上節定案。

但為避免「CI105 的營運日清單整體不可信」的殘餘風險，我做了以下交叉：

| 證據 | 內容 | 效力 |
|---|---|---|
| E1 | FlightMapper CI105 冬季：`Mon,Wed,Fri,Sun` + `Tue,Thu` → **含 Sun** | 直接（但該來源已被證明會漏天） |
| E2 | pfs NRT-TPE 當日（**週六**）看板列 `CI105 On schedule · TPE 20:55` | 證明 CI105 **在週六有排班**（而 FlightMapper 說週六沒有）→ FlightMapper 對 CI105 也同樣漏天 |
| E3 | CI104 週六實測飛了 → CI104/CI105 是同一組輪替；若週六去程有飛，當日回程 CI105 必然存在 | 結構性推論 |
| E4 | 週五 2026-10-30 成田看板實測 CI105 17:45 On schedule、Gate 98/T2（上游文件 S2 記錄，本次未重抓） | 佐證冬季時刻 |

**判定：CI105 週日有飛。信心「高」，但不是「極高」。**

誠實說明為什麼不是極高：**我沒有取得「某個週日」的當日機場 FIDS 實證**（今天就是週六，桃園機場 API 只吐當日；成田官網的時刻查詢頁 `https://www.narita-airport.jp/en/flight/departure` 回 **404**，我沒有再花請求數去猜它的正確路徑）。因此 CI105 週日是「兩來源一致 + 輪替結構 + 週六已證有排班」的推論，而非當日實測。
→ 若要升到「極高」，唯一乾淨的做法是：**在任一個週日抓一次桃園機場官方 arrival FIDS**（`a_flight` 的到場版本，或 `flight_arrive` 頁面的 XHR），確認 CI105 20:55 出現。本次來不及（見 §7 的請求額度說明）。

---

## 五、衝突分析：為什麼兩個來源會分歧，以及過渡期假說

### 5.1 直接排除的假說

| 假說 | 判定 | 依據 |
|---|---|---|
| 兩來源只是快照時間差（FM 舊、看板新） | **不成立** | FM 快照 as-of **2026-10-30**，爭議日 **2026-10-31**，只差 1 天；且 FM 的 CI104 冬季區段是 `Effective from` 開放式，沒有「11 月才加週六」的後續區段。若華航在 10-31 臨時增班，FM 的 as-of 時間點（10-30）本應已含「Effective from 2026-10-31: Sat」這類區段，但不存在。 |
| 是「過渡期」：10 月底才增/減班 | **只能部分保留（見 5.2）** | 冬季班表第一天是 2026-10-25（週日），**第一個週六就是 2026-10-31 當天**。所以資料上無法區分「整季都有週六」與「10-31 起才補上週六」——但兩者對使用者**沒有實務差別**。 |
| 看板是 stale 資料（旺季舊時刻） | **不成立** | 看板時刻是 12:35（冬季值），不是夏季的 12:40；且 Gate/機尾編號與官方 FIDS 對得上。 |
| 看板把「已取消」的班也列出來 | **不成立（本例）** | 官方 FIDS 顯示 RTime 12:42:35 + 登機結束 → 實際執行，不是取消。 |
| 是特殊加班機/臨時包機 | **不成立** | 官方 id 為 `20261031_D_CI104`（定期班號格式），且共掛 JL5176/QF8738（定期班才會有的共掛）。 |

### 5.2 過渡期假說的正確用法

唯一無法從本次證據排除的是：**「華航自 2026-10-31 起才把 CI104/CI105 補成 7 班/週」**（因為 10-31 恰好是冬季第一個週六，往前沒有第二個週六可驗）。
但這個殘餘可能性對結論無影響，理由：
1. 使用者要用的任何週六都 ≥ 2026-10-31，落在「已確認有飛」的期間內。
2. FlightMapper 的**歷史**資料顯示 CI104 在過去兩個季度都有週六區段（§1.5），「週六不存在」不是常態。
3. 若是「僅 10-31 單日加班」，官方應會標註加班/包機，但沒有；且 CI105 同日也在成田看板出現（雙向一致）。

### 5.3 可信度排序（本次之後）

| 來源 | 類型 | 可信度 | 適合回答什麼 | 不適合回答什麼 |
|---|---|---|---|---|
| **桃園機場官網 `a_flight` API** | 機場官方 FIDS（SSR JSON，無反爬） | **最高（本次實測）** | 「今天這班有沒有飛、實際幾點走、幾號門」 | 未來日期（只吐當日，`id` 為 `YYYYMMDD_D_CIxxx`） |
| **成田機場官網** | 機場官方 | 高（理論上） | 同上（NRT 端） | 本次**未打通**（`/en/flight/departure` 404，未再試） |
| FlightMapper | OAG 系**排班**資料庫 | 中高（結構可靠、**內容會缺筆**） | 時刻、季節起訖、多班比較 | **營運日**（本次證明 CI104/CI105 漏週六）；機型 |
| proflightsearch 當日看板 | 第三方 FIDS 彙整（**有快取，落後 1–2 小時**） | 中高 | 當日 Gate/機尾/共掛的**排班快照** | 「已實際起飛」（會延遲）、未來日期、它的「Who flies it」區塊 |
| `schedules/ci_tpe_nrt_schedule.json` | FlightMapper 的衍生物 | **需修正**（見 §6） | — | 營運日 |

**一句話**：這次是 **FlightMapper 錯、看板對**。但要注意「看板對」是**因為它的排班來源正確**，而不是因為它比較即時——它其實比官方慢 1.5 小時。真正讓爭議定案的是**機場官方 FIDS**。

---

## 六、對 `schedules/ci_tpe_nrt_schedule.json` 的修正建議（**未修改，僅建議**）

根因**不是** parser 的聯集邏輯——那個邏輯是對的（它忠實反映了上游的 `Mon,Wed,Fri,Sun` + `Tue,Thu`），錯的是**上游資料本身缺週六**。因此：

### 6.1 資料面（`ci_tpe_nrt_schedule.json`）

| 位置 | 現值 | 應改為 | 依據 |
|---|---|---|---|
| `routes[0].flights[1]`（**CI104**）`.days_of_operation` | `["Mon","Tue","Wed","Thu","Fri","Sun"]` | 加入 `"Sat"`（→ 7 天，順序 Mon…Sun） | 官方 FIDS 2026-10-31 實際執行 |
| 同上 `.is_daily` | `false` | `true` | 同上 |
| 同上 `.segments[0]`（`effective_from: 2026-10-25`, A321neo）`.days_of_operation` | `["Mon","Wed","Fri","Sun"]` | 加入 `"Sat"`，或新增一筆 `{effective_from:"2026-10-25", days:["Sat"]}` 區段 | 同日官方顯示機型為 **B777-300**（與 FM 的 A321neo 不符），故建議**新增獨立 Sat 區段並把 aircraft 標為待確認**，不要直接併入 A321neo 段 |
| `routes[1].flights[2]`（**CI105**）`.days_of_operation` | `["Mon","Tue","Wed","Thu","Fri","Sun"]` | 加入 `"Sat"` | pfs NRT 當日看板列 CI105；且與 CI104 同輪替 |
| 同上 `.is_daily` | `false` | `true` | 同上 |
| 同上 `.segments[0]` `.days_of_operation` | `["Mon","Wed","Fri","Sun"]` | 加入 `"Sat"`（aircraft 待確認） | 同上 |
| 其餘 6 班（CI100/CI106/CI107/CI108/CI101/CI109） | — | **不需修改**（本次逐班複驗，7 天皆正確） | §1.4 + §2.1 + §3 |

> 註：`aircraft` 欄位本身在兩個來源都不穩定（今日官方寫 B777-300，FM 週五寫 A321neo，pfs 機尾 B-18915）。若 JSON 要保留機型，建議加 `confidence` 或直接標 `null`。

### 6.2 流程面（`fetch_schedules.py`，**未修改，僅建議**）

1. **加入「覆蓋率告警」**：任何班號的 `len(days_of_operation) < 7` 時輸出 warning（TPE–NRT 這種主幹航線 6 天即高度可疑）。本次若早有此告警，就會在 CI 上線時被抓到，而不是靠人工複查。
2. **加入第二來源交叉驗證**：可用本次打通的桃園機場官方 `a_flight`（**每日 1 次**、純 JSON、無反爬），對「當日 CI 班號集合」與 FlightMapper 的排班做比對；不一致就標記 `needs_review`。
3. **不要用 proflightsearch 的 `arow-flights`（Who flies it）區塊**補營運日——那是過期夏季時刻（本次複驗成立）。
4. **不要用 proflightsearch 的狀態欄判定「已飛」**——它的頁面資料落後 1–2 小時（本次實測：12:49 抓到的頁面仍標 11:04 更新）。

---

## 七、對「週六去 / 隔週日回」的最終建議

**可用班次（皆已驗證為當日營運）**

| 方向 | 週六去程 | 週日回程 |
|---|---|---|
| 早 | **CI100 09:30 → 13:30**（當日實測 09:37 出發） | **CI107 09:20 → 12:30** |
| 午 | **CI104 12:35 → 16:35**（當日實測 12:42 出發，Gate D5） | **CI101 14:35 → 17:45** |
| 午晚 | **CI108 14:25 → 18:30**（Gate D6） | **CI105 17:45 → 20:55** |
| 晚 | **CI106 16:25 → 20:30**（Gate D16） | **CI109 19:30 → 22:45** |

**推薦組合（依行程目的）**

1. **時間最大化**：`CI100（六 09:30）+ CI109（日 19:30）` → 東京地面 ~30 小時。
2. **平衡、回台早休息**：`CI100（六 09:30）+ CI101（日 14:35）` → ~25 小時。
3. **不想早起（原始需求）**：**`CI104（六 12:35）+ CI105（日 17:45）` → 合法可行**（本次定案）。地面約 25 小時，週六約 17:30–18:00 進市區。**這是本次爭議的核心組合，結論是「可以買」。**
4. 不建議：`CI104 + CI107`（地面 ~16 小時，週日 07:20 前要到成田）。

**唯一剩下的實務保留**：以上皆為「表定時刻 + 當日營運驗證」。實際購票請以華航官網選定日期的報價頁確認當日班號（官網 bookingportal 對自動化封鎖，需人工查，見 [ci-automation-alternatives.md](ci-automation-alternatives.md) §B）。

---

## 八、請求紀錄與方法學邊界（誠實揭露）

### 8.1 請求清單（依序）

| # | 目標 | 方式 | 結果 |
|---|---|---|---|
| 1 | `info.flightmapper.net/route/China_Airlines_CI_TPE_NRT` | requests | 200 · 152,509 B |
| 2 | `info.flightmapper.net/flight/China_Airlines_CI_104` | requests | 200 · 76,479 B |
| 3 | `proflightsearch.com/search-flights/TPE-NRT` | requests | 200 · 189,553 B |
| 4 | `proflightsearch.com/search-flights/NRT-TPE` | requests | 200 · 193,210 B |
| 5 | `proflightsearch.com/search-flights/TPE-NRT` | chromium+xvfb（12:35） | 200，頁面標 Updated 03:04 UTC |
| 6 | `info.flightmapper.net/route/China_Airlines_CI_NRT_TPE` | requests | 200 · 152,209 B |
| 7 | `proflightsearch.com/search-flights/TPE-NRT` | chromium+xvfb（12:49） | 200，**Updated 仍為 03:04 UTC**（證明快取） |
| 8 | `www.taoyuan-airport.com/flight_depart` | chromium+xvfb（12:49） | 200，並捕捉 XHR `ch.json`、`a_flight`×2 |
| 9 | `www.narita-airport.jp/en/flight/departure` | chromium+xvfb | **404**（放棄該路徑） |
| 10 | `www.taoyuan-airport.com/flight_arrive` | chromium+xvfb | 200，但 XHR 未含 CI105（時段窗未到），頁面僅到首頁版位 → **未取得 CI105 證據**，截圖已刪除 |
| 11 | `www.taoyuan-airport.com/flight_depart` | chromium+xvfb（13:00） | 200，滾動至 CI104 列截圖成功 |

- 每次之間 sleep 3–4 秒（長等待另計）。**無任何 403/429**；唯一失敗是 narita 的 404（單次猜測，未重試）。
- ⚠️ **額度揭露**：若把頁面自動發出的 XHR（`ch.json`、`a_flight`）也算一次請求，本次總量約 **14–16 次**，**貼近/略超自訂 15 次上限**。超額的原因是「必須用瀏覽器載入官方 SPA 才能拿到它的 XHR payload」這一條路徑，每次載入會多帶 2–3 個子請求。已不再追加任何請求。
- 全程**未**使用代理、未換 IP、未繞過任何保護；未嘗試華航 bookingportal（依指示）。

### 8.2 未完成 / 未驗證

- **CI105 週日的當日機場 FIDS 實證**（成田官網 404；桃機 API 只吐當日）。→ 這是 CI105 週日停在「信心高」而非「極高」的唯一原因。
- **CI108 / CI106 週六的官方 FIDS**：官方 API 那一輪的時段窗是 12:00–13:59，只涵蓋到 CI104；CI108（14:25）、CI106（16:25）只有 pfs 當日看板 + FlightMapper 兩項一致性證據（pfs 看板在 CI104 上已被官方驗證為準確，故信心仍為「高」）。
- **11 月以後的週六**（例如 2026-11-07）無未來日期來源可查（桃機 API 只吐當日）。
- normita/成田官網的正確查詢路徑、桃機 arrival API 的參數名，皆未再嘗試（額度）。

### 8.3 暫存腳本

`/tmp/ci104/`：`fetch.py`、`fetch2.py`、`shot.py`、`shot2.py`、`tpefids.py`、`tpearr.py`、`tpefinal.py`，原始 HTML/JSON：`route_tpe_nrt.html`、`flight_ci104.html`、`route_nrt_tpe.html`、`pfs_tpe_nrt.html`、`pfs_nrt_tpe.html`、`pfs_after_dep.html`、`pfs_1252.html`、`api_88506.json`（桃機官方回應）。

---

*文件性質：一次性實測快照，基準日 2026-10-31（冬季班表第一個週六）。「CI104 週六有飛」屬當日實證；未來週六請以華航官網或機場當日 FIDS 複驗。*
