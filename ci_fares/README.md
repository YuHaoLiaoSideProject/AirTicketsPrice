# `ci_fares/` — 華航（CI）TPE→NRT 逐班票價快照

本目錄由 `fetch_ci_fares.py` 產生（**手動執行，刻意不接排程**）：

| 檔案 | 內容 |
|---|---|
| `TPE-NRT_YYYY-MM-DD.json` | 單一日期：逐班 × 艙等 × 艙等代碼 × 剩位 × 含稅總價 |
| `TPE-NRT_YYYY-MM-DD_YYYY-MM-DD.json` | 日期區間：`queries[]` 每個日期一筆 |

> 這個目錄**不被任何既有程式或 workflow 讀取**（`build_api.py` 只掃 `data/*.json`，
> workflow 只 `git add data/`），所以放這裡不會污染既有的星宇票價 pipeline。
> 程式另有 runtime 防呆：`--out` 指到 `data/` 會直接 `SystemExit`。

## 1. 資料來源

- **華航官網前台自己的 API**（Amadeus 型 REST）：
  - `POST https://api-des.china-airlines.com/v1/security/oauth2/token/initialization`（取 Bearer）
  - `POST https://api-des.china-airlines.com/v2/search/air-bounds`（逐班票價）
- `client_id` / `client_secret` 是**官網前端 bundle 裡的明碼**，不需要帳號；
  但 `api-des` 後面有 **DataDome**、`des-portal` 後面有 **Imperva**，
  **沒有真實瀏覽器通過反爬後的 cookie 與 `x-d-token` 就是硬牆**
  （403 → `geo.captcha-delivery.com` / Imperva incident）。
- 完整實測證據（endpoint、header、payload、cookie 行為、延遲建議）：
  `docs/research/ci-deeplink-api-feasibility.md`。

## 2. 架構：一次瀏覽器 + 之後純 HTTP

```
① 冷啟動（~25–60 秒，只在需要時跑一次）
   Playwright 走真實官網 UI（flights.china-airlines.com → 逐字輸入 TPE/NRT
   → 點日曆 → 按一次「現在預訂」）
   → 瀏覽器自己通過 Akamai proof-of-work 與 Imperva，落到 des-portal
   → 攔截瀏覽器自己的網路流量，取出：
        cookie jar / x-d-token / x-incap-spa-info / privateFacts / fact 樣板
   → 落地成 session 快取（含到期時間）
② 暖查詢（純 requests，每個日期 2 個請求；**相鄰請求之間隨機 2–4 秒**）
   token/initialization → air-bounds → 逐班票價 JSON
```

- **沒有**任何指紋偽造、stealth patch、代理或更換 IP；冷啟動就是「照 UI 操作 + 等待」，
  連 Akamai 的 `Challenge Validation`（proof-of-work，實測約 25–30 秒）也是**等它自己完成**，
  不介入、不代解。
- 暖查詢只把武裝時擷取到的 `fact` 樣板換掉航段與日期（實測可行，見規格書 §2.2 #14–18）。
- `privateFacts`（JWE）跨請求 byte-identical、跨日期可用，所以換日期不必回頭找 bookingportal。

## 3. cookie 快取機制

| 項目 | 值 |
|---|---|
| 位置 | `~/.cache/ci_fares/ci_session.json`（可用 `CI_FARES_CACHE_DIR` 覆寫） |
| 內容 | `cookies[]`、`x_d_token`、`private_facts`、`public_facts_jwt`、`fact_template`、`saved_at`、`ttl_seconds` |
| 權限 | `0600`（含 cookie 與 token） |
| TTL | **20 分鐘**（保守值；實測可重用 ≥25 分鐘） |
| 瀏覽器 profile | `~/.cache/ci_fares/browser_profile/`（約 100MB，可安全刪除） |

- 有有效快取 → **跳過瀏覽器**（第二個日期起完全純 HTTP）。
- 快取過期 / 損壞 / 缺欄位 / **不是合法 UTF-8** / `saved_at` 型別錯誤 → 視為損壞重新武裝
  （只印警告，不會以 traceback 結束）。
- 快取寫入失敗（磁碟滿／權限不足）只印警告，不中斷已完成的查詢；下次執行需重新武裝。
- `--refresh-cookie`：忽略快取、強制重新武裝。
- 執行中遇到 **401** → 重新武裝**一次**並重試**一次**；再失敗就明確報錯。
  這段邏輯**與 `--dry-run` 解耦**（原本被 `and not args.dry_run` 綁住）：
  `--dry-run` 現在根本不會發請求（見 §8），因此也不會遇到 401。
- **`--dry-run` 絕不開瀏覽器**：它只做本地檢查（參數、session 快取、輸出路徑），
  不發任何 HTTP 請求、不寫檔。**沒有可用快取時直接報錯 exit 1**（提示先正常跑一次），
  `--dry-run` 與 `--refresh-cookie` 互斥（exit 2）。

## 4. 三態分離（嚴格）

| `status` | 意義 | 出現在哪 |
|---|---|---|
| `ok` | HTTP 200 **且**解析到至少一個航班票價 | `queries[].flights[]` 有內容 |
| `no_data` | HTTP 200、**結構完整且明確為空**（`airBoundGroups: []`、`totalPrices: []`、`total == 0`） | `flights: []` + `evidence` |
| `failed` | 4xx/5xx/timeout/**解析失敗／結構異常** | `error` + `http_status` |

**絕不**把「查無」寫成 0 元或混成空陣列假裝成功 —— 這是
`docs/research/ci-coverage-probe.md` 的教訓（38% 覆蓋率被誤讀成「沒有便宜票」）。

### 4.1 「確定無資料」vs「看不懂資料」（`no_data` 的紅線）

`no_data` **只允許**在下面兩種情況出現：

1. `data.airBoundGroups` 是合法 list 且為**空陣列**；或
2. 結構完整（`boundDetails.segments`、`airBounds`、`prices` 都在），但票價清單明確為空
   （`totalPrices: []`）或 `total` 明確為 `0`。

**任何結構異常一律 `failed`**，並在 `error` 寫明是哪個欄位缺失／型別不對：

| 情境 | 結果 | `error` 範例 |
|---|---|---|
| `prices.totalPrices[].total` 被改名／不見 | `failed` | `回應結構異常（無法安全判定為查無）：…prices.totalPrices.total 缺少（站方欄位可能已改名）` |
| `total` 型別不對（字串／bool） | `failed` | `…total 型別錯誤（str，應為數字）` |
| 缺 `data` / `data.airBoundGroups` / `boundDetails.segments` | `failed` | `缺少 data.airBoundGroups（無法判定是否有資料）` |
| `airBoundGroups: []` | `no_data` | `null` |
| `totalPrices: []` 或 `total == 0` | `no_data` | `null` |

寧可 `failed` 也不要給出「看起來像查無」的結果。每個查詢的 `evidence.parse_stats` 會保留
`airBoundGroups` / `bounds_seen` / `price_entries_seen` / `rejected_no_total` /
`rejected_bad_total` / `zero_total` / `anomalies[]`，讓失敗原因可被機器判別。

> 結構異常屬**確定性失敗**（同一份回應重試結果相同），因此不進重試迴圈、只花 2 個請求。

### 4.1.1 `queries[]` 的 schema 一律一致（所有狀態同一批 key）

每個日期物件（`ok` / `no_data` / `failed` / 中止）都由同一個建構函式產生，
**key 集合完全相同**（見 `fetch_ci_fares.py` 的 `QUERY_RESULT_KEYS`，共 18 個）：
取不到的值一律填 **`null`** 而不是省略 key，例如未執行的日期也有
`http_status: null`、`raw_bounds_count: null`、`parse_stats: null`、`evidence: null`。
中止的日期另有機器可判讀欄位：`aborted: true` + `abort_reason`（`"blocked"` = 403/429、
`"budget_exhausted"` = `--max-requests` 用盡）→ 下游用固定 schema 解析不會 KeyError。

### 4.2 輸出檔安全（原子寫入 + 失敗不覆寫）

- 輸出檔名由 route + 日期決定，同一日期重跑會指向同一個檔名。
- 寫入採 **原子寫入**：先寫 `<name>.json.tmp` 再 `os.replace()`；任何一步失敗都會清掉 tmp，
  **不會**留下半截 JSON，也不會先 truncate 原檔。
- **本輪任何日期 `failed` 或被中止（403/429 攔截、`--max-requests` 用盡）時，不會覆寫既有含可用資料（`ok`/`no_data`）的輸出檔**；
  舊檔原樣保留，摘要會印出「🛡 本輪不完整…保留上一輪資料」與舊檔的 `generated_at` / `totals`，
  本輪結果仍可用 `--json` 取得。
- 正常覆寫前會把上一版備份成 `<name>.bak.json`，並在新檔的 `previous` 欄位記錄舊版的
  `generated_at` 與 `totals`；兩者都有，回溯時不必猜。
- 若輸出路徑本來就不存在（或舊檔已壞），失敗的一輪仍會寫入，以免錯誤完全沒有痕跡。
- 保留是**整檔層級**的：一次多日查詢中只要有任一日 `failed`／被中止，就不覆寫該輸出檔；
  該輪成功日期的結果要保存請加 `--json` 或先用 `--out` 寫到別的檔名。

### 4.3 結束碼（exit code）

| code | 常數 | 意義 |
|---|---|---|
| `0` | `EXIT_OK` | 全部日期皆為 `ok` 或 `no_data`（完整成功） |
| `1` | `EXIT_ENV` | 環境／IO 錯誤：playwright/chromium 不可用、冷啟動失敗、session 快取或輸出檔寫入失敗、**`--dry-run` 沒有可用 session 快取** |
| `2` | `EXIT_USAGE` | 參數錯誤（日期格式、互斥參數、不支援航線、輸出路徑不可寫、`--dry-run` + `--refresh-cookie`、間隔參數不合理） |
| `3` | `EXIT_ALL_FAILED` | 所有查詢日期皆 `failed` |
| `4` | `EXIT_PARTIAL` | 部分日期 `failed`（同時有 `ok`/`no_data`） |
| `5` | `EXIT_ABORTED` | 被反爬攔截（403/429）而中止整輪（即使前面有成功日期）；`abort_reason="blocked"` |
| `6` | `EXIT_UNEXPECTED` | 未預期例外（本工具 bug 或未涵蓋的環境問題） |
| `7` | `EXIT_BUDGET_ABORT` | 達到 `--max-requests` 上限而中止整輪，剩餘日期未執行；`abort_reason="budget_exhausted"` |

**任何中止都回非 0**：預算中止 = 7、反爬中止 = 5（兩者原因字串也不同，可直接比對）。
輸出檔的 `aborted` / `abort_reason` / `abort_message` 與摘要的 🛑 列會帶同一組原因。

`--help` 也會列出同一份語意。所有錯誤都會以人類可讀訊息走 stderr，**不以 traceback 結束**；
設定 `CI_FARES_DEBUG=1` 可印出完整 traceback（追 bug 用）。

## 5. 延遲與重試

| 項目 | 值 |
|---|---|
| 請求間隔 | **所有對外 HTTP 請求之間**隨機 **2–4 秒**（`--sleep-min` / `--sleep-max` 可調）：同日 `token` → `air-bounds`、**每次重試之前**、**401 重新武裝之後**、不同日期之間 |
| 本地操作 | 讀快取、備份、原子寫檔等**不延遲**；`--dry-run` 不發請求所以也不延遲 |
| 失敗重試 | **只有 5xx / timeout / 連線錯誤**才重試，最多 **2 次**，退避 **3s / 6s**（正常日期 2 個請求、5xx 最多 6 個） |
| **400 / 404 / 422 等其餘 4xx** | **終止性錯誤：立即停止該日期、不重試**（只花 1–2 個請求，不燒 budget） |
| **429 / 403** | **立即停止整輪**（不重試、不退避硬闖），其餘日期標 `aborted` + `abort_reason="blocked"`，exit 5 |
| 單次執行請求上限 | `--max-requests`（預設 **30** → 最多 14 個日期）；**用盡即停止整輪**，其餘日期標 `aborted` + `abort_reason="budget_exhausted"`，exit 7 |
| 401 | 重新武裝一次 + 重試一次（僅一次；與 `--dry-run` 無關） |

## 6. 已知風險（務必讀）

1. **ToS / 存取控制**：這是航空公司官網前台自己的 API，需要**真實瀏覽器通過反爬**才進得去。
   低頻手動取樣與「持續排程抓取」在性質上不同；本工具刻意**不接排程**、單航線、
   不快取轉售。是否可用於任何產品用途，請由專案方自行決定並承擔。
   永續解仍是商業授權資料源（OAG / Amadeus / Travelport）。
2. **反爬會輪替**：DataDome 的 `x-d-token`、Imperva 的 `reese84` / `incap_ses`、
   Akamai `_abck` 都可能改規則；遇到 403/429 就是「今天不通」，等下一輪人工執行。
   本工具**不會**嘗試繞過（不偽造指紋、不用代理、不換 IP、不代解 captcha）。
3. **cookie / `x-d-token` TTL 不確定**：實測 ≥25 分鐘可重用（含 3 個不同日期），
   上限未測；`datadome` / `visid_incap` cookie 標示 1 年但**不可信**。
   快取 TTL 只是樂觀值。
4. **前端明碼 secret 會輪替**：官網改版就取不到 token → 工具會直接停下並報錯，不硬重試。
5. **價格語意**：`total` 已含稅（`base + totalTaxes`）、`pointOfSale=SEA`（海外 POS）。
   與台灣官網顯示的一致性目前只在 CI100 / CI104 兩個點驗證過；`quota` 是查詢當下剩位，
   重跑會有 ±1 的跳動。
6. **單向**：只查 TPE→NRT 去程那一腿（回程腿以 `isRequestedBound: false` 送出、不取價）。
   回程要另發一次查詢（本工具刻意不做，避免把請求量翻倍）。

## 7. 如何驗證資料正確性

1. **與基準比對（已做過）**：2026-11-20 TPE→NRT
   - CI100 經濟艙 **TWD 17,107**（`TWSHECF` / `B` / quota 1）
   - CI104 經濟艙 **TWD 12,451**（`TWSHECS` / `K` / quota 1）
   - 與 `docs/research/ci-deeplink-api-feasibility.md` §〇、§五 的實測值一致
     （也與 10/31 官網截圖上的 CI100 / CI104 價格一致）。
2. **跨日期一致性**：2026-11-21 的 CI100 13,372 / CI104 11,238 / CI106 8,892 /
   CI9902 19,580，與研究階段以自組 `fact` 抓到的樣本完全相同。
3. **人工抽查**：官網選同一天、同一艙等，比對含稅總價與剩位數字。
4. **結構檢查**：每一筆票價都有 `fare_family_code` / `cabin` / `booking_class` /
   `quota` / `price_total` / `base` / `total_taxes` / `currency`，
   且 `price_total == base + total_taxes`（離線測試會驗這一條）。

## 8. 重新產生

```bash
# 需要 playwright（本專案刻意不列入 requirements.txt）
pip install playwright && playwright install chromium

python3 fetch_ci_fares.py --date 2026-11-20
python3 fetch_ci_fares.py --start-date 2026-11-20 --end-date 2026-11-24
python3 fetch_ci_fares.py --date 2026-11-20 --json        # 結構化結果到 stdout
python3 fetch_ci_fares.py --date 2026-11-20 --dry-run     # 純本地檢查：不開瀏覽器、
                                                           # 不發請求、不寫檔（無快取 → exit 1）
python3 fetch_ci_fares.py --date 2026-11-20 --refresh-cookie
python3 fetch_ci_fares.py --start-date 2026-11-20 --end-date 2026-11-24 \
    --max-requests 10 --sleep-min 3 --sleep-max 6         # 上限用盡即中止（exit 7）
xvfb-run -a python3 fetch_ci_fares.py --date 2026-11-20   # 無 DISPLAY 的機器

python3 -m unittest discover -s tests/unit                # 離線測試（不發網路請求）
```

## 9. 已知待修清單（P1，已全部修完）

原始稽核：`docs/research/ci-fares-error-handling.md`（§1 的 #7、#10、#11、#12、
§R2 落差 G3a、§R3 落差 G4）。以下 5 項**已於本輪修復**，並各自補上離線 mock 測試：

1. ✅ **`--max-requests` 用完會立即中止整輪**：不再逐日進 `query_leg`、不再 sleep；
   剩餘日期標 `aborted: true` / `abort_reason: "budget_exhausted"`（與 403/429 的
   `"blocked"` 分開），摘要顯示「已用請求數 / 上限」與中止原因，**exit 7（非 0）**。
2. ✅ **400/404/422 等確定性 4xx 不再重試**：只有 5xx / timeout / 連線錯誤才重試
   （2 次、退避 3s/6s）；其餘 4xx（401/403/429 另有專門處理）立即停止該日期。
   請求數：正常日期 2 個、token 層 4xx 1 個、5xx 最多 6 個（測試已固化）。
3. ✅ **請求間隔覆蓋所有對外請求**：同日 `token` → `air-bounds`、每次重試之前、
   401 重新武裝之後、不同日期之間，一律隨機 2–4 秒（`--sleep-min`/`--sleep-max` 可調）；
   讀快取／寫檔等本地操作不 sleep。
4. ✅ **`--dry-run` 零副作用**：絕不開瀏覽器；沒有可用 session 快取 → 明確報錯 +
   exit 1（`--dry-run` + `--refresh-cookie` → exit 2）；有快取時**零 HTTP 請求、零 sleep**。
   401 重新武裝的 `and not args.dry_run` 綁定已移除（解耦）。
5. ✅ **中止日期 schema 與其他狀態一致**：所有狀態的日期物件都由 `make_query_result()`
   產生、key 集合完全相同，缺值一律 `null`（不再省略 `http_status` / `raw_bounds_count`）。

目前沒有其他已知待修項目。

---

*免責：本目錄資料為華航官網前台 API 的衍生物，屬於**非授權來源**，
且受反爬機制保護。數值僅供技術驗證與人工對照，實際購票請以華航官網為準。*
