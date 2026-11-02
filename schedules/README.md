# `schedules/` — 班表快照（僅供參考，未經交叉驗證）

本目錄由 `fetch_schedules.py` 產生，目前只有一條航線：

| 檔案 | 內容 |
|---|---|
| `ci_tpe_nrt_schedule.json` | 中華航空 CI 的 TPE↔NRT 冬季班表（時刻、營運日、生效期間、機型） |

> 這個目錄**不被任何既有程式或 workflow 讀取**（`build_api.py` 只掃 `data/*.json`），
> 所以放這裡不會影響票價 pipeline。

## 1. 資料來源與其已知缺陷

- **來源**：`https://info.flightmapper.net/route/China_Airlines_CI_TPE_NRT`（回程 `..._NRT_TPE`）
  —— OAG 系的**排班**資料庫，伺服器端渲染 HTML。
- **缺陷（已實證）：上游會缺筆，漏掉部分營運日，而且從資料內部看不出來。**

實例：2026/27 冬季 CI104（TPE 12:35 → NRT 16:35）與 CI105（NRT 17:45 → TPE 20:55）
在 FlightMapper 只有 6 個營運日（`Mon,Tue,Wed,Fri,Sun` + `Tue,Thu` 兩段聯集，缺 `Sat`）。
但**桃園國際機場官方 FIDS**（`https://www.taoyuan-airport.com/api/api/flight/a_flight`）
在 2026-10-31（週六）明確有一列：

```
CI104 / ODate 2026/10/31 / OTime 12:35:00 / RTime 12:42:35 / Gate D5 / 登機結束
共掛 JL5176、QF8738
```

→ 班機當天真的飛了，CI104 的營運日實為**每日**。**錯的是上游，不是 parser 的聯集邏輯。**
完整查證過程：`docs/research/ci104-ci105-saturday-check.md`。

## 2. `days_unverified` 的意義

JSON 的**檔案頂層**與**每一筆航班**都有：

| 欄位 | 值 | 意義 |
|---|---|---|
| `days_unverified` | `true` | 這個 `days_of_operation` **沒有**經過第二來源交叉驗證 |
| `unverified_reason` | 字串 | 為什麼不可信（含已知缺筆的具體案例） |

**為什麼標記「全部」航班，而不是只標已知缺筆的 CI104/CI105？**
因為缺筆在來源資料內部是**自洽**的（CI104/CI105 同時缺週六，看起來像「這組輪替每週 6 班」，
光看 FlightMapper 無法發現異常）。既然無法判斷「哪些班是乾淨的」，
只標已知個案會讓其他航班被誤讀成「已驗證」，所以一律標記。

## 3. 為什麼不自動補值

- 程式的職責是**忠實反映來源**，不是猜測或補值。補值會讓「資料缺漏」被掩蓋成「資料正確」，
  之後就再也沒有人會去查證。
- 本案的缺筆是**上游的錯**，用第二來源（官方 FIDS）覆寫等於把兩套資料混在一起，
  而且 FIDS 只提供**當日**，無法覆蓋整個季度。
- 因此輸出**保留來源原樣**：CI104/CI105 仍然是 6 天、不含 `Sat`，另外附上警語。
- `fetch_schedules.py` 也會在**終端機**印出明顯的警告區塊（不讓這件事只藏在 JSON 裡）。

## 4. 要用於正式用途時，該怎麼驗證

1. **當日交叉比對（唯一乾淨的做法）**：桃園機場官方 FIDS
   `GET https://www.taoyuan-airport.com/api/api/flight/a_flight`
   - 純 JSON、無 token、無反爬，回應含 `FlightNo`、`ODate`/`OTime`（表定）、
     `RDate`/`RTime`（實際）、`Gate`、`CurrentStatus`、`sharing`（共掛）。
   - ⚠️ **只提供當日**（`id` 形如 `20261031_D_CI104`），查不到未來日期。
   - 判讀重點：有 `RTime` 且狀態推進到「登機結束／已飛」才算實際執行；
     只有排班、狀態停在「On schedule」不足以證明。
   - 要驗證「某個未來的週六」，得在**那一天**抓一次（可用 `scripts/` 或 CI 排程，
     本專案目前刻意未接排程）。
2. **成田端**：成田機場官網（本次未打通路徑，`/en/flight/departure` 回 404），
   或改用華航官網選定日期查該日班號（官網對自動化封鎖，需人工）。
3. **不要**用 proflightsearch 的「Who flies it」區塊補營運日（是過期夏季時刻），
   也**不要**用它的狀態欄判定「已飛」（頁面資料有快取，實測落後 1–2 小時）。
4. 若要長期改善，方向是「加第二來源交叉驗證 + 覆蓋率告警」
   （任何 TPE–NRT 主幹航班 `len(days_of_operation) < 7` 就警示），
   而不是在 parser 裡補值。

## 5. 重新產生

```bash
python3 fetch_schedules.py                      # 抓兩個方向並寫入本目錄
python3 fetch_schedules.py --json               # 結構化結果到 stdout
python3 fetch_schedules.py --dry-run            # 只抓取／解析，不寫檔
python3 -m unittest discover -s tests/unit      # 離線解析測試（不發網路請求）
```

請求紀律沿用腳本設定：單次執行上限 4 個 GET、請求間隨機延遲 2–4 秒、失敗重試 2 次。

---

*免責：本目錄資料為 FlightMapper 的衍生物，`days_of_operation` 已知可能缺筆，
僅供參考。實際購票／行程規劃請以航空公司官網與機場當日 FIDS 為準。*
