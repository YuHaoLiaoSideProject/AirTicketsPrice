# PWA — 開發規格

> **對應 Roadmap**：無編號功能（沿用專案中文檔名慣例）；以 `docs/interaction-flows/PWA.md` 7 情境（P1-A~P2-F）為功能邊界
> **技術決策**：`docs/tech-decisions/PWA-2026-08-15.md`（D1–D8、T1–T13、Spike S1–S4、通知承載、manifest 規格）
> **操作流程**：`docs/interaction-flows/PWA.md`（情境 P1-A~P2-F、異常 E1–E14、邊界限制、26 項驗收）
> **BDD**：`docs/bdds/PWA.feature`（52 個 Scenario：Happy Path 14（含 1 Outline ×4 rows）／Error Handling 14（E1–E14）／Edge Cases 8／Business Rules 16（含 1 Outline ×2 rows））
> **測試計畫**：`docs/test-plans/PWA測試計畫.md`（SYS-01~15 / HDL-01~11 / F-01~26 / INT-01~07 / E2E-01~49 / MAN-01~15，附錄 A 52/52 覆蓋矩陣）
> **背景規格**：`docs/development/離線功能.md`（既有 SW 殼 6 檔 + IndexedDB 離線能力，本功能在其上增量）
> **狀態**：Phase 1+2 已實作，測試通過（T1–T13 全數完成，2026-08-15；全量回歸 unit 112／worker 18／Python 34／e2e_smoke 69／e2e_offline 105／e2e_pwa 174 全綠）

---

## 概述

把星宇機票價格趨勢頁做成**可安裝 PWA**，並在每週五票價下降時以**自建 Cloudflare Worker Web Push** 主動推播單則摘要通知、點通知直接跳到該航線；兩階段交付：Phase 1 可安裝（manifest + 圖示 + iOS + Lighthouse 達標）→ Phase 2 推播（Worker 三端點 + 爬蟲端 drop_last 偵測）。核心包含：

1. **圖示與 manifest（web/icons/ + web/manifest.webmanifest，新增）**：`scripts/gen_icons.py`（Pillow，dev-only）產生 192 / 512 / 512-maskable / apple-touch-icon(180) 四 PNG；manifest 欄位齊全（D4 規格）使瀏覽器具備安裝資格。
2. **前端 PWA 模組（web/pwa.js，新增）**：安裝按鈕狀態機（beforeinstallprompt deferred prompt）、iOS UA／16.4+ 版本判定、訂閱 UI 三態、user gesture 防護、notificationclick deep-link 拼接、通知承載格式化；UMD 匯出對齊 `cache.js`（瀏覽器掛全域 `Pwa`、Node `node:test` 可 require）。
3. **主程式整合（web/app.js 擴充 + web/index.html / styles.css 擴充）**：安裝按鈕、訂閱 toggle、三態狀態提示 DOM 與事件；`web/sw.js` 新增 `push` / `notificationclick` / `notificationclose` handler（Phase 2）並 bump cache version——既有 shell precache / SWR 邏輯與離線能力完全不動（零回歸，BR48）。
4. **推播服務（worker/，新增，Phase 2）**：Cloudflare Worker 免費 tier，三端點 `GET /vapid-public-key`、`POST /subscribe`（訂閱／退訂）、`POST /notify`（Bearer 驗證 → VAPID Web Push 廣播 → 404/410 失效訂閱自動清理）；訂閱名單存 Workers KV；VAPID 私鑰與 token 皆為 secret（D6 憑證分層）。
5. **爬蟲端擴充（fetch_prices.py，Phase 2）**：爬蟲完成後 `--notify` 流程——讀上一週 data 檔為基準 → drop_last 偵測 → 節流選取最多 3 條 → 週頻率守衛 → `POST /notify`（Bearer `PUSH_API_TOKEN`）；純函式 + stdlib `unittest` 可測（零新依賴）。
6. **驗證（tests/unit/test_pwa_drops.py + tests/unit/pwa.test.js + worker/src/index.test.js + tests/e2e_pwa.py，新增）**：爬蟲 Python 單元（SYS-01~15）、前端 PWA 單元（F-01~26）、Worker 單元（HDL-01~11）、PWA E2E（INT-01~07 + E2E-01~49，mock push service + CDP dispatch）；既有 `e2e_smoke`（69 checks）／`e2e_offline`（105 checks）為回歸門檻，**不動**。

章節取捨（依功能類型適配）：本功能同時有「後端」兩類可測單元——爬蟲端 Python 純函式（§1.3）與 Cloudflare Worker JS（§1.4），故 §1 以兩節呈現；有對外 endpoint（Worker 三端點）＋ Web Push 訊息 → §3（API / Message 合約）；跨「GitHub Actions 爬蟲 → Worker → push service → SW → UI」資料流 → §4；安裝狀態機／訂閱三態／SW push 事件生命週期 → §5（非 WebSocket）；BDD 52 Scenario 的異常、邊界與商業規則 → §6 + 附錄 A 覆蓋矩陣；新增 UI（安裝按鈕／訂閱 toggle／狀態提示）→ §7；Cloudflare wrangler / KV / VAPID / GitHub secret / workflow → §9。

---

## 1. 後端實作規格

> 本專案「後端」分兩層：**爬蟲端擴充**（Python 3.12，`fetch_prices.py`，Phase 2 的 drop_last 偵測與通知呼叫）與**推播服務**（Cloudflare Worker，`worker/`，JS，Phase 2 的三端點）。Phase 1 不涉及任何後端改動（純前端增量，D1）。

### 1.1 依賴新增

- **零新增 runtime 依賴**：爬蟲沿用 `requests>=2.31`（requirements.txt 不動）；Worker 零依賴、零 build（純 ES module + Web Crypto，Spike S1 驗證）。
- **dev-only 依賴**（不進 requirements.txt，不進 repo）：
  - `Pillow`：`scripts/gen_icons.py` 產生圖示（一次性，commit PNG 後即可移除）。
  - `web-push`（npm）：本機一次性產生 VAPID 金鑰對（`npx web-push generate-vapid-keys --json`，§9.1）；亦可用 `openssl ecparam` 替代。
- **測試零新依賴**：Python 用 stdlib `unittest`（`python -m unittest discover -s tests/unit`）；Worker 用 Node v22 `node:test`（CommonJS require，與既有 `tests/unit/*.test.js` 同模式）。

### 1.2 檔案改動總覽

```
AirTicketsPrice/
├── web/                                ← Phase 1（manifest/icons/meta/安裝 UX）+ Phase 2（訂閱 UI）
│   ├── manifest.webmanifest            ← 新增（T2）：D4 規格，theme_color 取自 styles.css token
│   ├── icons/                          ← 新增（T1）：icon-192 / icon-512 / icon-512-maskable / apple-touch-icon
│   │   └── （icon-192.png / icon-512.png / icon-512-maskable.png / apple-touch-icon.png）
│   ├── pwa.js                          ← 新增（T3/T9）：安裝狀態機 + 訂閱狀態機 + iOS 判定 + 通知純函式（UMD）
│   ├── index.html                      ← 修改（T3/T4/T9）：manifest/apple-touch-icon/theme-color/iOS meta + pwa.js script + 安裝按鈕/訂閱 UI DOM
│   ├── styles.css                      ← 修改（T4/T9）：§7 安裝/訂閱/提示樣式（沿用既有 token）
│   ├── app.js                          ← 修改（T4/T9）：beforeinstallprompt 整合 + 訂閱 toggle 與狀態 UI
│   └── sw.js                           ← 修改（T9）：新增 push/notificationclick/notificationclose；cache name bump（Phase 1 v2 / Phase 2 v3）；SHELL += pwa.js + 圖示
├── worker/                             ← 新增（T8，Phase 2）
│   ├── wrangler.toml                   ← 新增：name/main/compatibility_date/[vars] VAPID 公鑰與 subject/[[kv_namespaces]]
│   └── src/
│       ├── index.js                    ← 新增：三端點 handler（~150 行）+ 純函式（可測）
│       └── index.test.js               ← 新增：HDL-01~11（KV / push service 以 mock 注入）
├── scripts/
│   └── gen_icons.py                    ← 新增（dev-only，T1）：Pillow 產生四圖示 + --check 尺寸驗證
├── fetch_prices.py                     ← 修改（T10，Phase 2）：--notify 流程（detect_drops/select_top_drops/should_notify/within_weekly_window/build_notify_payload/call_notify/load_baseline）
├── tests/
│   ├── unit/test_pwa_drops.py          ← 新增（T10）：SYS-01~15（stdlib unittest）
│   ├── unit/pwa.test.js                ← 新增（T3/T9）：F-01~F-26（require('../../web/pwa.js') + fs 靜態驗證）
│   ├── mock_worker.py                  ← 新增（T12，可選 helper）：本機 mock push service 三端點
│   └── e2e_pwa.py                      ← 新增（T12）：INT-01~07 + E2E-01~49（mock + CDP dispatch + iOS UA + set_offline）
├── .github/workflows/weekly-crawl.yml  ← 修改（T11，Phase 2）：Commit 後追加「Detect drops & notify」step
├── data/last_notified.json             ← 新增（T10/T11，runtime 產物）：週頻率守衛 marker（隨 data/ commit；build_api.py 對非 list JSON 自動跳過）
├── README.md                           ← 修改（T6/T13）：安裝 / 訂閱 / secret 設定 / Worker 部署 / iOS 限制
└── 既有 tests/e2e_smoke.py（69 checks）、tests/e2e_offline.py（105 checks）、tests/unit/aggregate.test.js、tests/unit/cache.test.js
                                        ← 一律不動（回歸門檻 D8）
```

### 1.3 爬蟲端擴充（Python — fetch_prices.py，T10）

職責：Phase 2 在爬蟲完成並 commit 後，以「最近一次抓取（上一週 data 檔）」為基準比對本次資料，任一航班票價較上次下降（`drop_last`）即觸發通知；合併為單則摘要（最多 3 條、取下降幅度最大者）；以週頻率守衛防止同週重複發送（workflow_dispatch 二次觸發）；呼叫 Worker `/notify` 附 Bearer `PUSH_API_TOKEN`。**決策邏輯全部為純函式**（資料都在手上），Python stdlib `unittest` 直接可測（SYS-01~15）。

```python
# fetch_prices.py 新增區塊（Phase 2 / T10）——對照 docs/development/PWA.md §1.3 / §3 / §4

# ---------------------------------------------------------------- 通知（drop_last）
MAX_NOTIFY_DROPS = 3                     # D4：摘要最多 3 條
LAST_NOTIFIED_FILE = "data/last_notified.json"  # 週頻率守衛 marker（build_api.py 對非 list 自動跳過）


def load_baseline(data_dir: Path, current_file: Path) -> list[dict] | None:
    """基準 = 上一週 data/*.json（scraped_at 小於本次的最大者）；無 → None（E12 首次無基準）。

    ⚠️ 陷阱：不可用「本次 run 已覆寫的 api/latest.json」當基準——workflow 中 build_api.py
    已在本 step 前重產生 latest.json（內容 = 本次資料），誤用會造成「永遠比對自己 = 無下降」。
    基準一律從 data/ 原始檔依 scraped_at 選取（§4.1 流程圖）。
    """


def detect_drops(prev: list[dict], curr: list[dict]) -> list[dict]:
    """逐航班（route_id, outbound_date, return_date, outbound_flight_no）比對舊價→新價：
      僅 new_price < old_price 才列入；drop_amount = old - new。
      持平 / 上漲 / 僅低於全域平均 / 僅創近期新低 → 一律不觸發（BDD @edge-case「非 drop_last 一律不觸發」）；
      缺價（None）、差額 0、舊價 0 → 不誤判、不拋例外（SYS-14）。
    @returns [{route, outbound_date, return_date, flight_no, old_price, new_price, drop_amount}]
    """


def select_top_drops(drops: list[dict], max_n: int = MAX_NOTIFY_DROPS) -> list[dict]:
    """依 drop_amount 遞減排序取前 max_n 筆；≤max_n 全保留（SYS-05）；
      幅度相同 → 依原始順序穩定排序（不 flaky，SYS-12）；空清單 → 空。"""


def should_notify(prev: list[dict] | None, curr: list[dict]) -> tuple[bool, list[dict]]:
    """prev 為 None → (False, [])：跳過通知、僅建立基準（E12 / SYS-06）；
      否則 detect_drops + select_top_drops（SYS-01~03）。"""


def within_weekly_window(last_notified_iso: str | None, now: datetime) -> bool:
    """同 ISO 週（now.isocalendar() 相同）→ True（跳過，SYS-08）；
      跨週 / 無記錄 → False（SYS-15：同週 workflow_dispatch 二次觸發被阻擋）。"""


def build_notify_payload(drops: list[dict]) -> dict:
    """→ {"drops": [ {route, outbound_date, return_date, flight_no, old_price, new_price} ]}
      （Tech Decision D4 通知承載規格；SYS-09）。"""


def call_notify(payload: dict, token: str, url: str, timeout: int = 15) -> dict:
    """POST url 附 Authorization: Bearer <token>（SYS-10）；body = payload JSON。
      任何失敗（401 / 網路錯誤 / timeout）→ 回傳 {ok: False, status}，**不 raise**——
      資料已於本 step 前 commit，通知失敗不得中斷或污染流程（E6 / SYS-11）。"""


def main_notify() -> int:
    """fetch_prices.py --notify 入口（workflow「Detect drops & notify」step 呼叫）：
      1. 找本次 data 檔（最近寫入者）+ load_baseline（上一週）
      2. should_notify → (False, []) → 印「無下降（或首次），跳過通知」→ 0
      3. 讀 LAST_NOTIFIED_FILE → within_weekly_window → True → 印「同週已發送，跳過」→ 0
      4. build_notify_payload → call_notify（env PUSH_API_TOKEN / PUSH_NOTIFY_URL）
      5. 成功（ok）→ 寫 LAST_NOTIFIED_FILE（{scraped_at, notified_at}）→ 0；失敗 → 印錯誤 → 1
        （workflow 該 step 標記失敗；資料已 commit，E6）"""
```

`main()` 新增 argparse 分支：

```python
    parser.add_argument("--notify", action="store_true",
                        help="爬蟲後執行票價下降通知（Phase 2；讀 data/ 比對 → 呼叫 Worker /notify）")
    if args.notify:
        sys.exit(main_notify())   # 不重新爬蟲
```

### 1.4 推播服務（Cloudflare Worker — worker/src/index.js，T8）

職責：推播中繼（D3——決策邏輯在爬蟲端，Worker 不自己讀 GitHub）。三端點：`GET /vapid-public-key`（公鑰公開）、`POST /subscribe`（訂閱驗證寫入 KV／退訂刪除）、`POST /notify`（驗 Bearer token → 以 VAPID 私鑰對全部訂閱者 Web Push 廣播 → 404/410 失效訂閱自動清理）。handler 與純函式分離（對齊 `cache.js` 注入式設計）：KV 與 push service 呼叫以 mock 注入，`worker/src/index.test.js` 直接可測（HDL-01~11）。執行 `node --test worker/src/index.test.js`（CommonJS require）。

```javascript
// worker/src/index.js — 星宇票價趨勢推播服務（Cloudflare Worker，Phase 2 / T8）
// 對照：docs/tech-decisions/PWA-2026-08-15.md（D2/D3/D4/D6）；docs/development/PWA.md §1.4 / §3 / §9.1
// 測試：worker/src/index.test.js（HDL-01~11；KV / fetch 以 mock 注入）

const ROUTE_NAMES = { 'TPE-NRT': '東京', 'TPE-KIX': '大阪', 'TPE-FUK': '福岡', 'TPE-CTS': '札幌' };
const MAX_DROPS = 3;          // D4：摘要最多 3 條（爬蟲已選 top-3，Worker 防禦性再 slice）
const SUB_PREFIX = 'sub:';    // KV key = sub:{endpoint}（HDL-10：以 endpoint 為裝置單位）
const MAX_SUBS = 1000;        // D6：KV 防陌生人灌爆上限（超出 → 400 拒絕寫入）

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    };
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method === 'GET'  && url.pathname === '/vapid-public-key') return vapidPublicKey(env, cors);
    if (request.method === 'POST' && url.pathname === '/subscribe')       return subscribe(request, env, cors);
    if (request.method === 'POST' && url.pathname === '/notify')          return notify(request, env, cors);
    return new Response('Not Found', { status: 404, headers: cors });
  },
};

/** GET /vapid-public-key：回傳 VAPID 公鑰（base64url，前端 PushManager.subscribe 可解析）。
 *  無需任何驗證；回應僅含公鑰，絕不含私鑰（HDL-01 / BR 憑證分層）。 */
async function vapidPublicKey(env, cors) {
  return json({ publicKey: env.VAPID_PUBLIC_KEY }, cors);
}

/** POST /subscribe（訂閱／退訂雙用途，維持「三端點」路徑面；T9 免 token 調整）：
 *  ① 訂閱：**免 Bearer token**（前端瀏覽器無法持有 secret）→ 先過 **Origin 白名單**（github.io +
 *     本機測試 origin；其他 → 403 origin not allowed）→ body = PushSubscription JSON →
 *     isValidSubscription 嚴格驗證（endpoint https、keys 合法 base64url）→ KV put sub:{endpoint}
 *     （重複 endpoint → 覆寫，HDL-10）→ 200
 *  ② 退訂：body = {endpoint, action:'remove'} → KV delete → 200（E2E-11 KV 刪除；冪等；同受白名單保護）
 *  無效資料 → 400 且不寫入 KV（HDL-03）；達 MAX_SUBS → 400（D6 防灌爆）。 */
async function subscribe(request, env, cors) {
  if (!isOriginAllowed(request, env)) return json({ error: 'origin not allowed' }, cors, 403);  // T9：白名單（無 Origin → 允許）
  const body = await request.json().catch(() => null);
  if (body && body.action === 'remove') {
    await env.SUBS.delete(SUB_PREFIX + body.endpoint);   // 退訂：刪除 KV 記錄
    return json({ ok: true }, cors);
  }
  if (!isValidSubscription(body)) return json({ error: 'invalid subscription' }, cors, 400);
  const count = (await env.SUBS.list({ prefix: SUB_PREFIX })).keys.length;
  if (count >= MAX_SUBS) return json({ error: 'subscription limit reached' }, cors, 400);  // D6 防灌爆（§3.1 合約）
  await env.SUBS.put(SUB_PREFIX + body.endpoint, JSON.stringify(body));
  return json({ ok: true }, cors, 200);  // 成功碼 200
}

/** POST /notify：驗證 Bearer token（D6）→ drops 驗證 → 讀全部訂閱 → VAPID Web Push 廣播。
 *  - token 無效 / 缺 → 401，不發送、不寫入（HDL-05 / E6）
 *  - drops 缺失 / 空 / 非陣列 → 400 drops required（T8 合約；爬蟲端正常流程不會送空）
 *  - 無訂閱者 → 200 空廣播（HDL-06 / E7）
 *  - 單筆 push 回 404/410 → 刪除該訂閱（E5 / HDL-07）；其他失敗（5xx/網路）→ 記 failed（HDL-11，不誤報成功） */
async function notify(request, env, cors) {
  if (!isAuthorized(request, env.PUSH_API_TOKEN)) return json({ error: 'unauthorized' }, cors, 401);
  const { drops } = await request.json().catch(() => ({ drops: [] }));
  if (!Array.isArray(drops) || drops.length === 0) return json({ error: 'drops required' }, cors, 400);
  const payload = formatNotification(drops.slice(0, MAX_DROPS));   // HDL-08
  const list = await env.SUBS.list({ prefix: SUB_PREFIX });
  if (list.keys.length === 0) return json({ ok: true, sent: 0 }, cors);    // E7
  const failed = [];
  for (const k of list.keys) {
    const sub = JSON.parse(await env.SUBS.get(k.name));
    const res = await sendPush(sub, payload, env);        // Web Push（VAPID + RFC 8291 加密，Spike S1）
    if (res.status === 404 || res.status === 410) await env.SUBS.delete(k.name);  // E5 清理
    else if (!res.ok) failed.push(k.name);                                      // HDL-11
  }
  return failed.length
    ? json({ ok: false, failed: failed.length }, cors, 500)
    : json({ ok: true, sent: list.keys.length }, cors);
}

/* ── 純函式（worker/src/index.test.js 直接測試）── */

/** 訂閱資料驗證（HDL-02/03）：endpoint 為 https URL、keys.p256dh / keys.auth 為非空字串 */
export function isValidSubscription(sub) { /* ... */ }

/** 通知承載格式化（HDL-08 / F-19a/b 同簽名合約）：
 *  title = '✈️ 票價下降了！'
 *  body  = drops 逐筆「{route} {name} {M}/{D}–{M}/{D} 降至 NT${new}（原 NT${old}）」以 '\n' 連接
 *          （範例：'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）'；價格千分位、月日去前導零）
 *  data.url = '?route=' + drops[0].route（**相對 SW scope**，sw.js 以 registration.scope 拼接，F-14 / S2）
 *  drops 為空 → title 不變、body = '有票價更新'、data.url = '?route='
 */
export function formatNotification(drops) { /* ... */ }

/** VAPID Authorization 頭（Spike S1）：
 *  JWT（ES256，Web Crypto）：{ aud: new URL(endpoint).origin, exp: now+12h, sub: env.VAPID_SUBJECT }
 *  → Authorization: 'WebPush ' + jwt；Crypto-Key: 'p256dh=' + 公鑰 */
export async function makeVapidAuth(subscription, privateKeyJwk, subject) { /* ... */ }

/** Web Push payload 加密（RFC 8291：ECDH(p256dh) + HKDF(auth) + AES-128-GCM）；回傳 (ciphertext, salt, serverPub) */
export async function encryptPayload(payload, subscription, vapidPublicKey) { /* ... */ }

/** sendPush：組 VAPID 頭 + 加密 payload → fetch(endpoint)；回傳 Response（呼叫端依 status 分流 200/404/410/其他） */
export async function sendPush(subscription, payload, env) { /* ... */ }
```

`worker/src/index.test.js` 骨架（HDL-01~11，CommonJS require 對齊既有測試）：

```javascript
// worker/src/index.test.js — 推播服務單元測試（HDL-01~11）
// 執行：node --test worker/src/index.test.js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
// 以 ESM → CJS 動態載入（或將 handler 以 module.exports 一併匯出，與既有 UMD 測試同模式）
// KV mock：in-memory Map 包裝 get/put/delete/list（對齊 cache.test.js mkAdapter 風格）
// push 呼叫 mock：globalThis.fetch stub 記錄 (url, headers, body) 並依情境回 200/404/410/500
```

---

## 2. 前端實作規格

### 2.1 檔案改動總覽

```
web/
├── manifest.webmanifest        ← 新增（T2）：D4 規格（§2.2）
├── icons/                      ← 新增（T1）：icon-192 / icon-512 / icon-512-maskable / apple-touch-icon（§2.3）
│   └── *.png
├── pwa.js                      ← 新增（T3/T9）：安裝狀態機 + 訂閱狀態機 + iOS 判定 + 通知純函式（UMD 掛全域 Pwa，§2.4）
├── index.html                  ← 修改（T3/T4/T9）：head 六項 PWA 連結/meta + pwa.js script + 安裝/訂閱 DOM（§2.5）
├── styles.css                  ← 修改（T4/T9）：§7 樣式
├── app.js                      ← 修改（T4/T9）：beforeinstallprompt / appinstalled / 訂閱 toggle 整合（§2.6）
└── sw.js                       ← 修改（T9）：push / notificationclick / notificationclose；cache name bump；SHELL += pwa.js + 圖示（§2.7）
```

> **SHELL 擴充**：`index.html` 新增 `<script src="pwa.js">` 後，`pwa.js` 必須進 SW shell precache，否則離線 reload 頁面缺該檔會壞（沿用離線功能 §2.4 的「cache.js 必須進 shell」理由）；另建議納入 `icons/icon-192.png` 與 `icons/apple-touch-icon.png`（通知圖示離線渲染）。SHELL 由 6 檔擴為 7 檔 + 2 圖示。

### 2.2 manifest 規格（web/manifest.webmanifest，T2）

```json
{
  "name": "星宇機票價格趨勢",
  "short_name": "票價趨勢",
  "description": "星宇航空來回票價趨勢追蹤（經濟艙 · 去程週六 → 回程下週日）",
  "lang": "zh-Hant",
  "start_url": "./",
  "scope": "./",
  "display": "standalone",
  "theme_color": "#1a73e8",
  "background_color": "#ffffff",
  "icons": [
    { "src": "icons/icon-192.png",  "sizes": "192x192",  "type": "image/png" },
    { "src": "icons/icon-512.png",  "sizes": "512x512",  "type": "image/png" },
    { "src": "icons/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```

- `start_url` / `scope` 用**相對路徑 `./`**：GitHub Pages 子路徑部署（`/AirTicketsPrice/web/`）下自動解析正確（S2）；Lighthouse installable 要求 start_url / scope 落在 SW scope（`/AirTicketsPrice/web/`）內，相對路徑天然滿足。
- `theme_color` = styles.css 設計 token 主色 `--accent: #1a73e8`（沿用；dark mode 不動 theme_color，維持品牌色）。
- maskable icon：**主體落於 80% safe zone**（中央內切圓內；滿版背景出血），不因平台裁切缺角（MAN-12 / F-18）。

### 2.3 圖示（scripts/gen_icons.py + web/icons/，T1）

```python
#!/usr/bin/env python3
"""PWA 圖示產生（dev-only，一次性；Pillow 不需進 requirements.txt，commit PNG 後即可移除）

用法：
    python scripts/gen_icons.py            # 產生 web/icons/ 四個 PNG（192/512/512-maskable/180）
    python scripts/gen_icons.py --check    # 驗證已產生圖示尺寸（F-18 / CI 輔助，回傳非 0 = 失敗）
"""
from PIL import Image, ImageDraw

SPECS = [
    ("icon-192.png",            192, False),
    ("icon-512.png",            512, False),
    ("icon-512-maskable.png",   512, True),
    ("apple-touch-icon.png",    180, False),
]

def make_icon(size: int, maskable: bool) -> Image.Image:
    """滿版背景（--accent #1a73e8 或白底）+ 中央「✈️ 票價趨勢」主體；
      maskable=True → 背景延伸至邊緣（出血）、主體縮放至中央 60% 直徑內（80% safe zone，MAN-12）。"""
```

### 2.4 web/pwa.js — PWA 前端模組（T3 + T9）

職責：**安裝按鈕狀態機**（Phase 1）、**訂閱狀態機與流程**（Phase 2）、iOS UA／版本判定、user gesture 防護、notificationclick deep-link 拼接、通知承載格式化純函式。不碰既有資料層／圖表層；UMD 匯出對齊 `cache.js`（瀏覽器掛全域 `Pwa`，Node `node:test` 可 `require('../../web/pwa.js')`）。

```javascript
/**
 * PWA — 前端模組（安裝狀態機 + 訂閱狀態機 + iOS 判定 + 通知純函式）
 * UMD 匯出：瀏覽器掛全域 `Pwa`；Node（node:test）走 module.exports（對齊 cache.js / aggregate.js）。
 * 對照：docs/development/PWA.md §2.4；docs/tech-decisions/PWA-2026-08-15.md（D1/D2/D5/D7）
 * 測試：tests/unit/pwa.test.js（F-01~F-26）
 */
(function (global, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else global.Pwa = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ════════════════════════════════════════════════════════════
  // 設定常數（§9.4：Worker 部署後更新 URL；ROUTE_NAMES 與 config.py / aggregate.js 對齊）
  // ════════════════════════════════════════════════════════════
  const CONFIG = {
    PUSH_WORKER_URL: 'https://airtickets-price-push.<account>.workers.dev',  // §9.1 部署後改為實際 workers.dev 網域
    MAX_NOTIFY_DROPS: 3,                       // D4：摘要最多 3 條（與爬蟲 / Worker 一致）
    ROUTE_NAMES: { 'TPE-NRT': '東京', 'TPE-KIX': '大阪', 'TPE-FUK': '福岡', 'TPE-CTS': '札幌' },
    IOS_SUPPORT_VERSION: [16, 4],              // iOS 16.4+ installed PWA 才收得到推播（D5/D7/S3）
  };

  // ════════════════════════════════════════════════════════════
  // 安裝狀態機（Phase 1，T4；F-01~F-03）
  // ════════════════════════════════════════════════════════════

  /**
   * 安裝狀態機：'idle'（事件前）→ 'available'（beforeinstallprompt 後，deferredPrompt 暫存）
   *             → 'installed'（appinstalled / display-mode:standalone）｜'cancelled'（回到 available 可再觸發）
   * 回傳 { state, setPrompt(prompt), prompt(), reset(), canInstall() }；prompt() 僅在點擊按鈕時呼叫（F-02）。
   */
  function installStateMachine() { /* ... */ }

  /** 安裝按鈕是否顯示：installState==='available' 且非 standalone（F-03）；
   *  已安裝（matchMedia('(display-mode: standalone)') matches 或 navigator.standalone）→ false */
  function shouldShowInstall(state, isStandalone) { /* → boolean */ }

  // ════════════════════════════════════════════════════════════
  // iOS 判定（Phase 1/2；F-04 / F-21）
  // ════════════════════════════════════════════════════════════

  /** iOS UA 判定：/iPhone|iPad|iPod/（F-04）。iPadOS 13+ 桌面模式偽裝 Mac 的限制見 §6 EC9。 */
  function isIOS(ua) { /* → boolean */ }

  /** iOS 版本 ≥ (major, minor)：解析 UA 'CPU iPhone OS 17_5 like Mac OS X'（F-21）。
   *  <16.4 → 訂閱時顯示 iOS 推播限制提示（需加到主畫面且 16.4+），不提供 email 等替代（BDD @edge-case）。 */
  function iosVersionAtLeast(ua, major, minor) { /* → boolean */ }

  // ════════════════════════════════════════════════════════════
  // 訂閱狀態機與流程（Phase 2，T9；F-05~F-13, F-20, F-22, F-23, F-26）
  // ════════════════════════════════════════════════════════════

  /** 訂閱狀態三態（F-05a~d）：permission + subscription + opts.vapidReady →
   *   'unsubscribed'（「開啟票價提醒」）｜'subscribed'（「關閉票價提醒」＋狀態「已訂閱」）｜
   *   'denied'（拒絕引導「通知已封鎖，請到瀏覽器網站設定中允許通知」）；
   *   暫時性：'loading'（流程中）｜'error'（E2「訂閱失敗，請稍後重試」）｜'unavailable'（E3「提醒功能暫時不可用」，vapidReady=false 時）。
   *   return { state, buttonLabel, hint, retryable } */
  function subscriptionUI(permission, subscription, opts) { /* ... */ }

  /** VAPID 公鑰抓取（E3 / F-10）：GET {PUSH_WORKER_URL}/vapid-public-key → base64url 字串；
   *  失敗 → 回 null（app.js 停用按鈕＋「提醒功能暫時不可用」；下次載入自動恢復）。 */
  async function fetchVapidPublicKey() { /* → string|null */ }

  /** 訂閱流程（F-07 / F-06）：**僅在按鈕 click（user gesture）handler 內呼叫**；
   *  deps 注入瀏覽器環境（Node 測試 mock；瀏覽器以全域預設，browserDeps）：
   *  { ua, standalone, userGesture, permission, vapidKey, getRegistration,
   *    requestPermission, subscribe, postSubscribe }
   *  ① user gesture 守衛（F-06）② iOS 且非 standalone → 顯示「需加到主畫面後才收得到通知」，不發權限請求（E8 / F-13）
   *  ②' iOS <16.4 → 顯示限制提示（BDD @edge-case / EC6）
   *  ②'' macOS Safari 非 standalone → 顯示「需加到 Dock（程式塢）後才收得到通知」，不發權限請求（F-29 / E8 桌機；
   *      Safari desktop 未安裝時 subscribe 會以 AbortError 失敗，誤導為網路問題）
   *  ③ Notification.requestPermission() → 'granted' → PushManager.subscribe({ userVisibleOnly: true, applicationServerKey: 公鑰 })
   *  ④ POST /subscribe {endpoint, keys, action:'add'}（免 token，T9）成功 → 狀態 'subscribed'；失敗 → 'error'（F-09）
   *  防重入：subscribing 旗標，流程中忽略重複點擊（F-22）；頁面中止 → 無殘留狀態（F-23） */
  async function subscribeFlow(deps) { /* ... */ }

  /** 退訂流程（F-08）：getSubscription().unsubscribe()（本機移除）→ POST /subscribe {endpoint, action:'remove'}（免 token，T9）
   *  → Worker 刪除 KV 記錄 → 狀態回 'unsubscribed'（E2E-11）。 */
  async function unsubscribeFlow(deps) { /* ... */ }

  // ════════════════════════════════════════════════════════════
  // 通知純函式（Phase 2；F-14 / F-19a/b）
  // ════════════════════════════════════════════════════════════

  /** notificationclick deep-link 拼接（F-14）：data.url 為**相對 SW scope** 路徑（'?route=TPE-NRT'），
   *  以 new URL(dataUrl, scope) 解析 → GitHub Pages 子路徑下正確為 /AirTicketsPrice/web/?route=TPE-NRT（S2 / BDD @edge-case）。 */
  function resolveNotificationUrl(scope, dataUrl) { /* → string */ }

  /** 通知承載格式化（F-19a/b；與 worker/src/index.js formatNotification **同簽名合約**——皆接受 drops 陣列，
   *  逐筆一行、以 '\n' 連接，data.url 取 drops[0].route；空陣列 → body「有票價更新」、data.url「?route=」。
   *  { title: '✈️ 票價下降了！', body: 'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）',
   *    data: { url: '?route=TPE-NRT' } }（價格千分位、月日去前導零，以 BDD fixture 為準） */
  function formatNotification(drops) { /* → { title, body, data } */ }

  // UMD 匯出（tests/unit/pwa.test.js require 的公開面）
  return { installStateMachine, shouldShowInstall, isIOS, iosVersionAtLeast,
           subscriptionUI, shouldRequestPermission, iosSubscribeGate,
           fetchVapidPublicKey, subscribeFlow, unsubscribeFlow,
           resolveNotificationUrl, findNotificationTarget, formatNotification, CONFIG };
});
```

### 2.5 web/index.html 改動（T3 / T4 / T9）

```html
<head>
  <!-- Phase 1（T3）：PWA 連結與 iOS meta（F-17 / E2E-37 靜態驗證逐項） -->
  <link rel="manifest" href="manifest.webmanifest">
  <link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
  <meta name="theme-color" content="#1a73e8">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <!-- 既有 icon favicon 保留 -->
</head>
<body>
  <!-- Phase 1/2（T4/T9）：PWA 動作列（header 之下、route-tabs 之上；375px 自動換行，§7） -->
  <div class="pwa-actions" id="pwaActions">
    <!-- ① 安裝按鈕：初始 hidden；beforeinstallprompt 後顯示；standalone / iOS 隱藏 -->
    <button id="installBtn" class="btn install-btn" type="button" hidden>安裝 App</button>
    <!-- ② iOS「加到主畫面」提示：依 UA 顯示；3 步驟（分享 → 加到主畫面） -->
    <div id="iosHint" class="ios-hint" role="note" hidden>
      點分享 <svg class="ico" ...></svg> 後選「加到主畫面」，即可把票價趨勢加到主畫面
    </div>
    <!-- ③ 訂閱 toggle + 狀態：初始 hidden；secure context 且 Worker 公鑰可取時顯示 -->
    <button id="subBtn" class="btn sub-toggle" type="button" hidden>開啟票價提醒</button>
    <span id="subStatus" class="sub-status" role="status" aria-live="polite" hidden></span>
  </div>

  <!-- script 順序：aggregate.js → cache.js → pwa.js → app.js（pwa.js 須在 app.js 前） -->
  <script src="aggregate.js"></script>
  <script src="cache.js"></script>
  <script src="pwa.js"></script>
  <script src="app.js"></script>
</body>
```

> 既有元素（offBar / staleBar / refreshBtn / syncStatus 等）不動；訂閱 UI 的「頁面載入不自動彈權限詢問」由 §2.6 的 init 流程保證（F-06 / E2E-07）。

### 2.6 web/app.js 整合（T4 + T9）

既有結構（資料層 / 聚合 / 圖表 / 互動 / 離線狀態層）保留；新增兩段整合：

```javascript
// 依賴解構（既有 PriceAgg / OfflineCache 之外新增 Pwa）
const Pwa = window.Pwa;

// ── Phase 1（T4）：安裝入口 ──
const installBtn = $('installBtn'), iosHint = $('iosHint');
const installState = Pwa.installStateMachine();
const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();                       // 暫存 deferred prompt，點擊才呼叫（F-02 / BR40）
  installState.setPrompt(e);
  renderInstallUI();                        // 依狀態顯示/隱藏安裝按鈕（F-01）
});
window.addEventListener('appinstalled', () => { installState.reset(); renderInstallUI(); });

installBtn.addEventListener('click', async () => {
  // 只有 user gesture 才呼叫原生安裝流程（deferred prompt 暫存語意）
  installState.prompt().then(choice => {
    if (choice && choice.outcome === 'dismissed') renderInstallUI();  // 取消 → 按鈕保留可再觸發（P1-A）
  });
});

function renderInstallUI() {
  const ios = Pwa.isIOS(navigator.userAgent);
  const showBtn = Pwa.shouldShowInstall(installState.state(), isStandalone());
  installBtn.hidden = !(showBtn && !ios);   // iOS 不顯示安裝按鈕（P1-B）
  iosHint.hidden = !(ios && !isStandalone());  // iOS 且未安裝 → 顯示「加到主畫面」提示
}

// ── Phase 2（T9）：訂閱 toggle 與狀態 UI ──
const subBtn = $('subBtn'), subStatus = $('subStatus');
let subState = 'unsubscribed';
let vapidKey = null;                        // 公鑰快取（E3 失敗 → null → 按鈕停用）

async function initPwaPush() {
  // 非 secure context（file://）→ 整個訂閱區隱藏（E14 / F-24）
  if (!('serviceWorker' in navigator) || !('PushManager' in window) ||
      !/^https?:$/.test(location.protocol)) return;
  // ① 抓 VAPID 公鑰（E3：失敗 → 停用＋「提醒功能暫時不可用」，其餘功能正常）
  vapidKey = await Pwa.fetchVapidPublicKey();
  // ② 讀 Notification.permission + getSubscription() 還原三態（F-20 / F-26；不彈權限詢問，D5）
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  renderSubUI(Notification.permission, sub);
}

function renderSubUI(permission, subscription) {
  const ui = Pwa.subscriptionUI(permission, subscription, { vapidReady: !!vapidKey });
  subBtn.hidden = false;
  subBtn.textContent = ui.buttonLabel;          // 開啟票價提醒 / 關閉票價提醒
  subBtn.disabled = !vapidKey || ui.state === 'loading';   // E3 停用
  subStatus.hidden = !ui.hint;
  subStatus.textContent = ui.hint || '';
  subBtn.classList.toggle('subscribed', ui.state === 'subscribed');                 // §7 .sub-toggle.subscribed
  subStatus.classList.toggle('warn', ui.state === 'denied' || ui.state === 'error');
  subStatus.classList.toggle('unavailable', ui.state === 'unavailable');            // E3（§7 .sub-status.unavailable）
}

subBtn.addEventListener('click', async () => {
  // user gesture：訂閱或退訂（D5 唯一 requestPermission 入口；F-06）
  const reg = await navigator.serviceWorker.ready;
  const existing = await reg.pushManager.getSubscription();
  if (existing) await Pwa.unsubscribeFlow({ getRegistration: async () => reg });   // P2-C 退訂
  else await Pwa.subscribeFlow({ vapidKey, getRegistration: async () => reg });     // P2-A 訂閱（公鑰快取注入）
  const sub = await reg.pushManager.getSubscription();
  renderSubUI(Notification.permission, sub);
});
```

`init()`（既有）末尾追加 `initPwaPush()`（fire-and-forget，不阻斷首繪）；`file://` 下 `navigator.serviceWorker` 不存在 → 直接 return（E14 降級，既有記憶體快取行為不變）。

### 2.7 web/sw.js 擴充（T9）

**既有 shell precache / SWR / activate 邏輯不動**；新增 push 三 handler + cache name bump（§5.3）。

```javascript
/* 既有區塊（離線功能）不動，僅：
 *  - CACHE = 'airtickets-shell-v3'   // Phase 1 bump v2、Phase 2 bump v3（§5.3 版本管理）
 *  - SHELL = [ index.html, styles.css, app.js, aggregate.js, cache.js, pwa.js, sw.js,
 *             'icons/icon-192.png', 'icons/apple-touch-icon.png' ]   // += pwa.js + 通知圖示
 */

/* ═══ Phase 2（T9）：push / notificationclick / notificationclose ═══ */

// T9：載入 pwa.js（deep-link 拼接 / 分頁決策純函式單一來源；pwa.js 在 SHELL precache 內）
importScripts('pwa.js');

self.addEventListener('push', e => {
  // Worker 已格式化 payload：{ title, body, data: { url } }（§3.2）；無 payload → fallback（§5.4）
  let payload = null;
  try { payload = e.data && e.data.json(); } catch (err) { payload = null; }
  const p = payload || {
    title: '✈️ 票價下降了！', body: '有票價更新', data: { url: './' },
  };
  e.waitUntil(self.registration.showNotification(p.title || '票價趨勢', {
    body: p.body || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/icon-192.png',
    data: p.data || {},
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  // deep-link：以 registration.scope 為基準拼接相對路徑（F-14 / S2 / BDD @edge-case 子路徑部署）
  const url = new URL((e.notification.data && e.notification.data.url) || './', self.registration.scope);
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    // 分頁決策（F-14b / E10 / EC8）：既有同 origin 分頁 → focus + navigate；無 → openWindow
    const target = Pwa.findNotificationTarget(list, url.href);
    if (target) {
      target.focus(); target.navigate(url.href).catch(() => {});   // E10：既有分頁 → 聚焦並切換航線，不重開分頁
      return;
    }
    return clients.openWindow(url.href);           // 無分頁 → 開新分頁（P2-B / E2E-10）
  }));
});

self.addEventListener('notificationclose', e => { /* E13 / F-15：關閉無任何後續動作 */ });
```

> 點通知開啟後：連網 → 頁面 fetch 最新資料；離線 → 既有 cache-first 快取繪圖 + 離線橫幅（P2-E / E9）；若該航線從未快取且離線 → 既有「此航線尚未下載，需連網」提示、停留原航線（離線功能 E2 語意）。SW 不因新增 push handler 改變既有 fetch 攔截行為（BR48 零回歸）。

---

## 3. API / Message 合約

### 3.1 Worker 三端點（Phase 2，T8）

| 方法 | 路徑 | 認證 | Request | Response | 說明 |
|------|------|------|---------|----------|------|
| GET | `/vapid-public-key` | 無 | — | `200 { "publicKey": "<base64url VAPID 公鑰>" }` | 無需驗證；供前端 `PushManager.subscribe(applicationServerKey)`（HDL-01 / E2E-41）；跨域需 CORS（§9.1） |
| POST | `/subscribe` | **免認證（T9：前端瀏覽器無法持有 secret token）→ 改以 Origin 白名單** | PushSubscription JSON：`{ endpoint, expirationTime, keys: { p256dh, auth } }` | `200 { "ok": true }` ｜ `400 { "error": "invalid subscription" }` ｜ `400 { "error": "subscription limit reached" }` ｜ `403 { "error": "origin not allowed" }` | **① Origin 白名單**：允許 `https://yuhaoliaosideproject.github.io` 及本機測試 origin（`http(s)://127.0.0.1[:port]` 與 `http(s)://localhost[:port]`，含 `env.ALLOWED_ORIGINS` 擴充）；其他 → 403 不寫入 KV；無 Origin header（非瀏覽器 client）→ 允許（CSRF 語意）。② `isValidSubscription` 嚴格驗證（endpoint 為 https URL、keys.p256dh/auth 為合法 base64url，HDL-02/03/10）。③ 超過 `MAX_SUBS` 上限 → 400（D6 防灌爆）。驗證後寫入 KV `sub:{endpoint}`；重複 endpoint 覆寫 |
| POST | `/subscribe`（退訂） | **免認證（同受 Origin 白名單保護）** | `{ "endpoint": "<原訂閱 endpoint>", "action": "remove" }` | `200 { "ok": true }` ｜ `403 { "error": "origin not allowed" }` | 前端退訂時刪除 KV 記錄（P2-C / E2E-11「mock 退訂端點」語意即此 remove 動作）；冪等 |
| POST | `/notify` | `Authorization: Bearer <PUSH_API_TOKEN>` | `{ "drops": [...] }`（§3.2；爬蟲已選 top-3，Worker 防禦性 slice 至 3）| `200 { "ok": true, "sent": N, "failed": 0 }` ｜ `400 { "error": "drops required" }`（drops 缺失/空）｜ `401 { "error": "unauthorized" }` ｜ `500 { "ok": false, "sent": N, "failed": M }` | 對全部有效訂閱者 Web Push 廣播 → 404/410 自動清理（HDL-04~07 / E6 / E7 / HDL-11） |

- 全端點帶 CORS 頭（`Access-Control-Allow-Origin: *`；OPTIONS 204 preflight），因頁面（github.io）與 Worker（workers.dev）跨域（§9.1）。
- 認證分層（T9 調整）：`/notify` 以 Bearer `PUSH_API_TOKEN` 保護（爬蟲持有 secret，D6）；`/subscribe` **免 token**（前端瀏覽器無法持有 secret），改以 **Origin 白名單** ＋ 資料嚴格驗證（`isValidSubscription`）＋ `MAX_SUBS` 上限保護；`/notify` 另以 drops 非空驗證保護。

> **T8/T9 合約調整（2026-08-15 實作）**：①（T8）`/notify` 新增 `400 drops required`（drops 缺失/空時拒絕，不空廣播壞資料）；成功碼統一 200（非 201）。②（T9）`/subscribe` **移除 Bearer token 驗證**（T8 依合約加上，但前端瀏覽器無法持有 secret token）——改以 **Origin 白名單**（`https://yuhaoliaosideproject.github.io` ＋ 本機測試 origin；其他 → `403 origin not allowed`）＋ 既有資料驗證（`isValidSubscription`，endpoint https、keys 合法 base64url）＋ `MAX_SUBS` 上限保護；`/notify` 維持 Bearer token（爬蟲持有）。前端 `pwa.js subscribeFlow` / `unsubscribeFlow` 呼叫 `/subscribe` **不附任何 token**（§2.4 / §3.1 表格）。

### 3.2 通知承載（Web Push message，D4）

爬蟲 `POST /notify` 請求體：

```json
{
  "drops": [
    { "route": "TPE-NRT", "outbound_date": "2026-08-22", "return_date": "2026-08-30",
      "flight_no": "JX 804", "old_price": 26008, "new_price": 24120 }
  ]
}
```

Worker 廣播給每個訂閱者的 Web Push payload（`formatNotification(drops)` 產出，§1.4）：

```json
{
  "title": "✈️ 票價下降了！",
  "body": "TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）",
  "data": { "url": "?route=TPE-NRT" }
}
```

| 欄位 | 規則 |
|------|------|
| `title` | 固定「✈️ 票價下降了！」（BDD BR 通知承載格式） |
| `body` | 每 drop 一行「`{route} {名稱} {M}/{D}–{M}/{D} 降至 NT${new,千分位}（原 NT${old,千分位}）`」，多筆以 `\n` 連接（最多 3 行）；月/日去前導零（`8/22` 非 `08/22`） |
| `data.url` | **相對 SW scope** 路徑 `?route={route}`（BDD fixture：`?route=TPE-NRT`）；sw.js 以 `new URL(url, registration.scope)` 解析 → 正式部署為 `/AirTicketsPrice/web/?route=TPE-NRT`（S2 / F-14） |

> **語意對照（上游文件差異統一）**：Tech Decision D4 與 E2E-44 的 `data.url = "/web/?route=TPE-NRT"` 描述的是**解析後的絕對路徑**（頁面根）；F-14 / INT-04 / BDD @edge-case 31 的「相對路徑 `?route=TPE-NRT`、以 scope 基準拼接」是**承載與解析規則**（子路徑部署正確性所必需）。本規格採後者為 canonical：payload 帶相對路徑，sw.js 用 `registration.scope` 拼接（任何子路徑深度皆正確）。

BDD 承載 fixture（附錄 A 情境 46 的兩 row）：

| route | 名稱 | body | data.url |
|-------|------|------|----------|
| TPE-NRT | 東京 | `TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）` | `?route=TPE-NRT` |
| TPE-KIX | 大阪 | `TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）` | `?route=TPE-KIX` |

---

## 4. 資料流

跨「GitHub Actions 爬蟲 → Cloudflare Worker → Browser Push Service → SW → UI」的完整管線（含降級分支）。

```
GitHub Actions（每週五 09:00 UTC+8，cron "0 1 * * 5"）
  ├─ fetch_prices.py ─▶ data/YYYYMMDD.json            （既有，不動）
  ├─ build_api.py ─▶ api/*                             （既有，不動）
  ├─ Commit data & api                                 （既有，不動）
  └─ [新增，T11] Detect drops & notify（python fetch_prices.py --notify）
       1. load_baseline：data/ 中 scraped_at < 本次的最大檔（上一週；⚠️ 不可用本次已覆寫的
          api/latest.json 當基準——見 §1.3 陷阱）
       2. should_notify：
          ├─ prev=None（首次）→ (False, []) → 跳過，僅建立基準（E12）
          └─ detect_drops → select_top_drops(3)
       3. within_weekly_window(last_notified.json) → 同週 → 跳過（SYS-15）
       4. build_notify_payload → POST /notify（Bearer PUSH_API_TOKEN）
          └─ 成功 → 寫 data/last_notified.json（週守衛）｜失敗 → step 標記失敗（資料已 commit，E6）
                                              │ POST /notify
                                              ▼
┌────────────────────────────────────────────────────────────┐
│ Cloudflare Worker（free tier；三端點 §3.1）                │
│  ├─ 驗證 Bearer token → 無效 → 401（E6）                  │
│  ├─ SUBS KV list（sub:*）→ 空 → 200 空廣播（E7）          │
│  └─ 每筆訂閱：formatNotification(drops.slice(0,3))         │
│      → makeVapidAuth（ES256 JWT）+ encryptPayload（RFC 8291）│
│      → fetch(endpoint)                                    │
│          ├─ 200/201 → 成功                                 │
│          ├─ 404/410 → SUBS.delete（E5 失效訂閱清理）        │
│          └─ 其他 → failed[] → 500（HDL-11）                │
└────────────────────────────────────────────────────────────┘
                              │ Web Push（FCM / APNs / Mozilla autopush）
                              ▼
                    使用者裝置（<50 親友，已訂閱）
                              │
                      SW push handler → showNotification（單則摘要，§2.7）
                              │
                      notificationclick → resolveNotificationUrl(scope, '?route=TPE-NRT')
                              ▼
        /AirTicketsPrice/web/?route=TPE-NRT（開啟或聚焦既有分頁，E10）
                              │
              ├─ 連網 → 既有 cache-first 同步 → 最新趨勢圖
              └─ 離線 → 既有 IDB 快取繪圖 + 離線橫幅（P2-E）
                        └─ 該航線未快取 → 「此航線尚未下載，需連網」停留原航線（E9）
```

### 4.1 資料流對應 BDD 情境

| BDD 情境 | 資料流環節 |
|---------|-----------|
| P2-F 系統自動偵測下降並廣播（@smoke） | workflow notify step → detect/select → call_notify → Worker 廣播（SYS-10, HDL-04, E2E-14） |
| P2-B 每週五收到單則摘要（@smoke） | detect_drops 合併 → formatNotification → SW showNotification（SYS-01/03, F-19, E2E-09） |
| P2-B 點擊通知開啟航線（@smoke） | notificationclick → scope 拼接 → openWindow/focus → 航線頁（F-14, E2E-10） |
| P2-A 同意權限訂閱成功（@smoke） | 前端 subscribe → POST /subscribe → KV；狀態「已訂閱」（F-07, INT-05, E2E-08） |
| P2-C 關閉票價提醒退訂（@happy-path） | unsubscribe → POST /subscribe remove → KV 刪除（F-08, E2E-11） |
| P2-E 離線點通知看快取（@regression） | notificationclick → 離線分支 → IDB 快取繪圖 + 橫幅（INT-01/02, E2E-13） |
| E6 notify 401 資料照常提交 | notify step 位於 Commit 之後；call_notify 不 raise（SYS-11, E2E-20, MAN-15） |
| E5 訂閱過期 404/410 自動清理 | Worker 廣播時 404/410 → KV delete；下次開啟 getSubscription 空 → 未訂閱（HDL-07, F-20, E2E-19） |

### 4.2 競態與並發

- **爬蟲端**：notify step 單一執行緒，無並發；週頻率守衛（`within_weekly_window`）防 workflow_dispatch 重入（SYS-15）；`call_notify` 失敗不 raise（E6），不影響後續（已是 workflow 最後一步）。
- **Worker**：`/notify` 廣播迴圈逐訂閱 `fetch`，單筆失敗不中斷其餘（HDL-11）；KV 寫入為單 key 原子操作（HDL-10 覆寫語意）。
- **前端**：`subscribing` 防重入旗標（F-22 快速連點只觸發一次）；`initPwaPush` 非阻塞（不延後首繪）；訂閱狀態還原以 `getSubscription()` 為唯一真相（F-20/F-26，不重複訂閱、不彈權限）。
- **SW**：多則通知各自獨立 showNotification；`notificationclick` 只對被點擊的那則作用（E13 / @edge-case 多則通知，INT-07）。

---

## 5. 生命週期

### 5.1 安裝狀態機（Phase 1，T4）

```
idle ──(beforeinstallprompt)──▶ available（deferredPrompt 暫存；按鈕出現，F-01）
                                 │
             ┌───────────────────┼────────────────────┐
             ▼ 點擊按鈕（user gesture）                 │
          prompt() 原生安裝確認框                       │
             ├─ 接受 → appinstalled → installed        │
             │     → 主畫面圖示；之後 standalone 開啟（按鈕隱藏，F-03/P1-C）
             └─ 取消 → cancelled → 回 available（按鈕保留可再觸發，P1-A）
iOS 分支：isIOS 且未 standalone → 不顯示安裝按鈕，顯示「加到主畫面」hint（P1-B / BR41）
standalone（含 iOS navigator.standalone）→ 安裝按鈕與 hint 皆隱藏（P1-C）
```

### 5.2 訂閱狀態機（Phase 2，T9；三態 + 暫時性）

```
                ┌──────────── 頁面載入（不自動彈權限，D5）────────────┐
                ▼                                                    │
   subscriptionUI(Notification.permission, getSubscription())          │
      ├─ default / granted＋無訂閱 → 'unsubscribed'（「開啟票價提醒」）  │
      ├─ granted＋有訂閱   → 'subscribed'（「關閉票價提醒」＋已訂閱）    │
      └─ denied            → 'denied'（拒絕引導，不重複詢問，E1）       │
                │                                                    │
    'unsubscribed' ──點擊（user gesture）──▶ requestPermission()      │
        │                                    ├─ granted → subscribe（userVisibleOnly + 公鑰）
        │                                    │     ├─ POST /subscribe 200（免 token；Origin 白名單 + 格式驗證）→ 'subscribed' ──┐
        │                                    │     └─ 失敗 → 'error'（可重試，E2）          │
        │                                    ├─ denied → 'denied'（引導去設定，E1）         │
        │                                    └─ 忽略/關閉 → 維持 'unsubscribed'（無錯誤，E4）│
        │                                    ├─ iOS 非 standalone → 提示不發請求（E8）      │
        │                                    └─ iOS <16.4 → 限制提示（@edge-case）          │
        ▼                                                                                  │
   'subscribed' ──點擊「關閉票價提醒」──▶ unsubscribe() → POST /subscribe remove（免 token）          │
        │                                        → KV 刪除 → 'unsubscribed' ◀────────────────┘
   任一狀態 ──E3（公鑰抓取失敗）──▶ 'unavailable'（按鈕停用「提醒功能暫時不可用」）
   任一狀態 ──非 secure context（file://）──▶ 整個訂閱區隱藏（E14）
```

### 5.3 Service Worker / cache 版本管理

| 層 | 版本載體 | bump 時機 | 效果 |
|----|---------|-----------|------|
| SW shell | cache name `airtickets-shell-vN` | **Phase 1**（index.html 加 meta/按鈕、SHELL += pwa.js）→ `v2`；**Phase 2**（sw.js 加 push handler）→ `v3` | `activate` 刪舊 cache；新頁面用新 shell（既有機制，§5.1 離線功能規格） |
| 資料 schema | `DB_VERSION` / `meta.version` | 不變（本功能不動離線快取 schema） | 離線資料層完全不受 PWA 化影響（BR33 獨立語意） |
| 前端 Worker URL | `pwa.js CONFIG.PUSH_WORKER_URL` | Worker 部署後更新一次（§9.4） | 影響訂閱/公鑰端點；部署後需 re-deploy 前端 |

### 5.4 SW push 事件生命週期（Phase 2）

```
（每週五爬蟲後）Worker 廣播 ─▶ push 事件
  ├─ payload 存在（{title, body, data}）→ 直接 showNotification（信任 Worker 格式化結果）
  └─ 無 payload / 解析失敗 → fallback 通知（title「✈️ 票價下降了！」、body「有票價更新」、url=scope）
      ▼
 通知顯示於裝置通知中心（可達性依裝置設定，@edge-case 勿擾/靜音）
  ├─ 點擊 → notificationclick：close → new URL(data.url, scope) → 既有分頁 focus/navigate（E10）或 openWindow（P2-B）
  ├─ 滑掉 → notificationclose：無任何動作（E13）
  └─ 不動作 → 停留通知中心（多則各別獨立，@edge-case）
```

- **訂閱過期（E5）**：瀏覽器資料被清 → push service 回 404/410 → Worker 廣播時刪除 KV；下次開啟頁面 `getSubscription()` 為空 → 狀態「未訂閱」→ 可重新訂閱（F-20）。
- **退訂後**：瀏覽器端 push service 亦移除（`unsubscribe()`），後續廣播自然收不到（P2-C「之後不再收到通知」）。

---

## 6. 邊界條件處理

來源：BDD @error-handling E1–E14 + @edge-case ×8 + @business-rules ×16 + Tech Decision 風險登錄 + 測試計畫補充（SYS-12/14/15、HDL-11、F-22~26）。**52 Scenario 的完整對應見附錄 A 覆蓋矩陣**。

### 6.1 異常處理（E1–E14）

| # | 情境 | 系統行為 | 對應 BDD / 測試 |
|---|------|---------|----------------|
| E1 | 權限被封鎖（denied） | 顯示拒絕引導「通知已封鎖，請到瀏覽器網站設定中允許通知」；不重複 requestPermission；設定允許後回頁面重按 → 重跑訂閱流程 | @error p0 / F-05d, F-11, E2E-15, MAN-05 |
| E2 | 訂閱失敗（subscribe 拋錯 / /subscribe 非 2xx） | 狀態「訂閱失敗，請稍後重試」；subscribe 拋 `AbortError`（瀏覽器連不上推播服務，VPN／公司防火牆常見）→「通知服務連線失敗，請確認網路後重試（若使用 VPN／公司網路，關閉或切換後再試）」；按鈕可重試；圖表/航線/離線完全不受影響 | @error p0 / F-09, E2E-16 |
| E3 | VAPID 公鑰取得失敗（Worker 未部署/掛掉） | `fetchVapidPublicKey` 回 null → 按鈕停用＋「提醒功能暫時不可用」；其餘功能正常；下次載入恢復 | @error p0 / F-10, E2E-17 |
| E4 | 權限詢問被忽略（關閉詢問框） | 狀態維持「未訂閱」、無錯誤提示；再點按鈕重新彈詢問 | @error p1 / F-12, E2E-18 |
| E5 | 訂閱過期（push service 404/410） | Worker 廣播時自動刪除失效訂閱；下次開啟 `getSubscription()` 空 → 「未訂閱」→ 重新訂閱恢復 | @error p1 / HDL-07, F-20, E2E-19 |
| E6 | /notify 401（token 失效） | Worker 拒絕（401）不發送；使用者端無影響；workflow notify step 標記失敗但資料已 commit；輪換 token 後下週恢復 | @error p1 / HDL-05, SYS-11, E2E-20, MAN-15 |
| E7 | KV 無訂閱者 | `/notify` 回 200 空廣播；0 次 Web Push、無錯誤 | @error p2 / HDL-06, E2E-21 |
| E8 | iOS 未加到主畫面就訂閱 | 顯示「需加到主畫面後才收得到通知」；不發權限請求（requestPermission 呼叫數 = 0）；加到主畫面後再點 → 正常流程 | @error p1 / F-13, E2E-22, MAN-06 |
| E8b | macOS Safari 未加到 Dock 就訂閱 | 顯示「需加到 Dock（程式塢）後才收得到通知」；不發權限請求（requestPermission 呼叫數 = 0）；加到 Dock 後再點 → 正常流程（修「通知服務連線失敗」誤導） | @error p1 / F-29 |
| E9 | 離線點通知且目標航線未快取 | 沿用離線功能 E2 語意：「此航線尚未下載，需連網」提示、停留原航線；不白屏、不出錯誤卡 | @error p1 / INT-01, E2E-23 |
| E10 | 通知對應分頁已開啟 | 聚焦既有分頁並 `navigate` 切換到通知航線；分頁數不增加 | @error p1 / INT-03, E2E-24 |
| E11 | 下降航班超過 3 條 | `select_top_drops`（爬蟲）＋ `slice(0,3)`（Worker 防禦）→ 只發下降幅度最大 3 條合併單則摘要（body 3 行） | @error p0 / SYS-04, SYS-05, E2E-25 |
| E12 | 首次爬蟲無基準 | `should_notify(prev=None)` → 跳過通知、僅建立基準；下週起正常觸發 | @error p1 / SYS-06, E2E-26 |
| E13 | 滑掉通知 | `notificationclose` 無任何動作（不開頁、不 focus） | @error p1 / F-15, E2E-27 |
| E14 | file:// 本機開啟 | SW 不註冊 → 無安裝資格、無推播；訂閱區隱藏；頁面降級為既有記憶體快取；http://localhost 開啟恢復 | @error p2 / F-24, INT-06, E2E-28, MAN-07 |

### 6.2 邊界（EC1–EC9，BDD @edge-case）

| # | 情境 | 系統行為 | 對應 BDD / 測試 |
|---|------|---------|----------------|
| EC1 | 非 drop_last 一律不觸發 | `detect_drops` 僅比「較上次下降」；持平/上漲/僅低於平均/僅創近期新低 → 空清單 → 不發通知（below_avg / new_low / 週摘要本輪不做） | @edge p0 / SYS-02, SYS-14, E2E-29 |
| EC2 | 通知頻率維持每週一次 | `within_weekly_window`（last_notified.json，ISO 週）阻擋同週重複；跨週恢復 | @edge p1 / SYS-08, SYS-15, E2E-30 |
| EC3 | 子路徑部署 deep-link | `data.url` 為相對路徑，sw.js 以 `registration.scope` 拼接 → `/AirTicketsPrice/web/?route=TPE-NRT`；航線參數正確套用 | @edge p1 / F-14, INT-04, E2E-31 |
| EC4 | 訂閱以瀏覽器/裝置為單位 | KV key = `sub:{endpoint}`；瀏覽器 B（獨立 storage）顯示「未訂閱」；瀏覽器 A 訂閱不受影響（無跨裝置同步） | @edge p1 / HDL-10, E2E-32 |
| EC5 | 訂閱狀態與離線快取彼此獨立 | 離線不使訂閱失效：離線開啟 → 快取繪圖 + 橫幅照常、訂閱狀態仍「已訂閱」；訂閱/SW/離線三層互不干擾 | @edge p1 / INT-02, E2E-33 |
| EC6 | iOS 16.4 以下收不到推播 | `iosVersionAtLeast(ua,16,4)` false → 誠實提示「需加到主畫面且 16.4+」；無 email 等替代方案 | @edge p2 / F-21, E2E-34, MAN-08 |
| EC7 | 裝置勿擾/靜音影響可達性 | 裝置層級行為，頁面與訂閱狀態不受影響；非本專案可控（MAN-09 文件註記） | @edge p2 / MAN-09 |
| EC8 | 同時多則通知點擊一則 | `notificationclick` 只對被點擊那則（其 `data.url`）作用；其他通知無連動 | @edge p2 / INT-07, E2E-35, MAN-10 |
| EC9 | iPadOS 13+ 桌面模式偽裝 Mac UA | `isIOS` 對 iPad 以 `/iPad/` 判別；iPadOS 13+ 若啟用「桌面版網站」會送 Mac UA → 顯示 Mac 版（無「加到主畫面」hint）；保守策略：`navigator.maxTouchPoints > 1` 且 `/Macintosh/` 亦視為 iOS 裝置；不影響 Android/桌面（補充：偵測限制） | 補充 / F-04, E2E-04, MAN-02 |

### 6.3 商業規則（BR1–BR16，BDD @business-rules）

| # | 規則 | 系統行為 | 對應 BDD / 測試 |
|---|------|---------|----------------|
| BR1 | manifest 欄位齊全 + Lighthouse 稽核 | §2.2 規格逐欄位（F-16）；Lighthouse「Installable」＋「離線 reload」pass（S4） | @business p0 / F-16, E2E-36, MAN-11 |
| BR2 | maskable 80% safe zone | `gen_icons.py` 主體置中央 60% 圓（safe zone 內）；視覺驗證 MAN-12 | @business p1 / F-18, MAN-12 |
| BR3 | index.html PWA 連結與 iOS meta | §2.5 六項逐項（rel=manifest / apple-touch-icon 180 / theme-color / mobile-web-app-capable / apple-mobile-web-app-capable / status-bar-style） | @business p0 / F-17, E2E-37 |
| BR4 | 安裝按鈕只在 beforeinstallprompt 後出現 | installState：事件前不顯示；事件後顯示；standalone 隱藏（三段時機 E2E-38） | @business p0 / F-01, F-03, E2E-38 |
| BR5 | iOS 依 UA 顯示提示 + apple-touch-icon | `isIOS` → 顯示「加到主畫面」逐步提示、不顯示安裝按鈕；head 圖示為 apple-touch-icon（180） | @business p1 / F-04, E2E-39 |
| BR6 | 下降比對以最近一次抓取為基準（drop_last） | `load_baseline`（上一週 data 檔）＋ `detect_drops`；非絕對價格或其他指標 | @business p0 / SYS-07, E2E-40 |
| BR7 | GET /vapid-public-key 回傳公鑰 | §3.1；前端取得並用於 `PushManager.subscribe` | @business p1 / HDL-01, E2E-41 |
| BR8 | POST /subscribe 驗證後寫入 KV | 免 token（T9）＋ `isOriginAllowed` Origin 白名單（github.io／本機測試 origin；其他 → 403）；`isValidSubscription`（endpoint https、keys 合法 base64url）；無效 → 400 不寫入；重複 endpoint 覆寫（裝置單位） | @business p1 / HDL-02, HDL-03, HDL-05b/c, E2E-42 |
| BR9 | POST /notify 驗證 token 廣播並清理 | Bearer 驗證（401 拒絕）→ 廣播 → 404/410 清理 | @business p0 / HDL-04, HDL-05, HDL-07, E2E-43 |
| BR10 | 通知承載單則摘要格式（Outline ×2） | §3.2 fixture：title/body/data.url 逐字符合（TPE-NRT / TPE-KIX 兩 row） | @business p0 / SYS-09, F-19a/b, HDL-08, E2E-44a/b |
| BR11 | 憑證分層 | 公鑰公開（/vapid-public-key 或前端常量）；私鑰只在 Worker secret；訂閱名單只在 KV；/notify 以 Bearer token 保護；前端檔案不含私鑰字串 | @business p1 / HDL-09, F-25, E2E-45 |
| BR12 | 既有測試與 Lighthouse 全量零回歸 | 單元（node --test tests/unit/ + worker）＋ e2e_smoke 69 ＋ e2e_offline 105 全綠；Lighthouse 無 best-practices 回歸 | @business p0 @regression / E2E-46 |
| BR13 | 既有爬蟲、data/、api/ 維持原樣 | workflow 既有步驟不動；僅 Commit 後**追加** notify step；data/api 產出流程不變 | @business p1 @regression / SYS-13, E2E-47 |
| BR14 | Playwright mock push service 端到端 | e2e_pwa.py：訂閱 → CDP dispatchPushEvent → notificationclick deep-link 全流程 | @business p1 / E2E-48 |
| BR15 | 公開免登入、親友規模、$0 成本 | 無登入牆；無訂閱者管理後台（前端按鈕管理）；GitHub Pages + CF Workers 免費 tier（100k req/day、KV 1GB） | @business p2 / E2E-49, MAN-13 |
| BR16 | README 與文件完整說明 | 安裝說明、訂閱說明、`PUSH_API_TOKEN` secret 設定、Worker 部署步驟、iOS 限制（需加到主畫面且 16.4+） | @business p2 / MAN-14 |

### 6.4 風險登錄對應（Tech Decision §6）

| 風險 | 緩解落地位置 |
|------|-------------|
| CF Workers 上 VAPID 簽章實作複雜 | **Spike S1 為 Phase 2 前置**（§8 步驟 7）；失敗備援 Vercel/Netlify function（web-push 套件）→ OneSignal 復議 |
| iOS 推播限制（僅 installed、16.4+） | EC6 / E8 誠實提示；MAN-02/04/06/08 手動驗證 |
| GitHub secret 引入（打破零 Secrets） | 僅 1 個 `PUSH_API_TOKEN`；README 明示；可輪換（E6） |
| 「上次比較基準」語意 | §1.3 `load_baseline` + 陷阱註記（不可用本次覆寫的 latest.json）；E12 首次跳過 |
| 通知轟炸 / 退訂 | D4 單則摘要最多 3 條（EC1/E11）；前端一鍵退訂（P2-C）；notificationclose 無動作（E13） |
| 既有離線功能回歸 | Phase 1 純前端增量、SHELL 擴充不動既有邏輯、cache bump（§5.3）；e2e_smoke/e2e_offline 為合併門檻（BR12） |
| beforeinstallprompt 行為差異 | 標準 deferred prompt（F-02）；iOS 以 UA hint（BR5）；S3 實機驗證 |
| Lighthouse 稽核條件變動 | 以「Installable + 離線 + best-practices 無回歸」為驗收，不追求工具版本分數（D8） |
| 陌生人灌爆 KV | /notify Bearer（D6）+ /subscribe 免 token 改以 Origin 白名單（T9）＋ 資料嚴格驗證（endpoint https、keys base64url）＋ MAX_SUBS 上限（§1.4 / §3.1） |

---

## 7. CSS 關鍵樣式

新增樣式骨架（既有 token 直接沿用：`--accent`、`--success`、`--danger`、`--warning`、`--border`、`--muted` 等）：

```css
/* ═══ PWA 動作列（.pwa-actions）═══ */
.pwa-actions {
  display: flex; align-items: center; gap: 0.6rem;
  flex-wrap: wrap; padding: 0.2rem 0 0.6rem;
}

/* ═══ 安裝按鈕（.install-btn；T4）═══ */
.install-btn {
  display: inline-flex; align-items: center; gap: 0.4rem;
  background: var(--accent); color: #fff;
  border: none; border-radius: 20px; padding: 0.42rem 1rem;
  font-size: 0.8rem; font-weight: 600; cursor: pointer;
  transition: background var(--transition), transform var(--transition);
}
.install-btn:hover { background: var(--accent-hover); }
.install-btn:focus-visible { outline: 3px solid var(--accent-light); outline-offset: 2px; }

/* ═══ iOS「加到主畫面」提示（.ios-hint；P1-B）═══ */
.ios-hint {
  display: inline-flex; align-items: center; gap: 0.45rem;
  font-size: 0.78rem; color: var(--accent);
  background: var(--accent-light); border: 1px solid rgba(26,115,232,0.3);
  border-radius: 8px; padding: 0.45rem 0.8rem;
}
.ios-hint .ico { width: 15px; height: 15px; color: var(--accent); }

/* ═══ 訂閱 toggle（.sub-toggle；三態，T9）═══ */
.sub-toggle { border-radius: 20px; font-size: 0.8rem; font-weight: 600; }
.sub-toggle.subscribed {
  background: var(--success-light); color: var(--success);
  border: 1px solid var(--success-border);
}
.sub-toggle:disabled { opacity: 0.55; cursor: not-allowed; }   /* E3 停用（沿用 .btn:disabled） */

/* ═══ 訂閱狀態提示（.sub-status；T9）═══ */
.sub-status {
  font-size: 0.74rem; font-weight: 600; color: var(--success);
  background: var(--success-light); border: 1px solid var(--success-border);
  border-radius: 8px; padding: 0.35rem 0.7rem;
}
.sub-status.warn {                      /* denied / error：拒絕引導、訂閱失敗（E1/E2） */
  color: var(--danger); background: var(--danger-light); border-color: var(--danger-border);
}
.sub-status.unavailable {               /* E3：提醒功能暫時不可用 */
  color: var(--warning); background: var(--warning-light);
  border-color: rgba(227,116,0,0.35);
}
```

RWD：375px 下 `.pwa-actions` 隨既有單欄堆疊（flex-wrap）；按鈕/提示維持 ≥44px 觸控高度（`--h-mobile`，MAN-07 語意）；`[hidden]` 統一由既有 `{ display:none !important }` 控制（與離線橫幅相同機制）。

---

## 8. 開發順序

整合 Tech Decision §5 T1–T13 與 Spike S1–S4，拆為可驗證步驟（**DAG，無循環依賴**）。**Phase 1（步驟 1–6，可安裝）先於 Phase 2（步驟 8–13，推播）**；**Spike S1（步驟 7）為 Phase 2 前置**（可與 Phase 1 平行進行，但 Phase 2 開工前必須有結論）。

| 步驟 | 任務（對應 T#） | 內容 | 依賴 | 狀態（T13） |
|------|----------------|------|------|------------|
| 1 | T1 圖示 | `scripts/gen_icons.py`（Pillow）產生 `web/icons/` 四 PNG（192/512/512-maskable/180）；maskable 主體於 80% safe zone；`--check` 尺寸驗證 | — | ✅ 完成 |
| 2 | T2 manifest | `web/manifest.webmanifest`（§2.2：name/short_name/start_url ./ / scope ./ / display standalone / icons / theme_color #1a73e8 / background_color / lang zh-Hant） | 1 | ✅ 完成 |
| 3 | T3 head 整合 + pwa.js 骨架 | `index.html` 六項 PWA 連結/meta + `<script src="pwa.js">`（§2.5）；`web/pwa.js` UMD 骨架：CONFIG / installStateMachine / shouldShowInstall / isIOS / iosVersionAtLeast / resolveNotificationUrl / formatNotification 純函式；`sw.js` SHELL += pwa.js（cache bump v2）；`tests/unit/pwa.test.js` 起步（F-01~F-04, F-16~F-18 靜態與安裝） | 2 | ✅ 完成 |
| 4 | T4 安裝 UX | `app.js` beforeinstallprompt（deferred prompt 暫存）/ appinstalled 整合 + 安裝按鈕 / iOS hint 顯示邏輯（§2.6）；`index.html` / `styles.css` 對應 DOM 與樣式（§7）；standalone 隱藏（P1-C） | 3 | ✅ 完成 |
| 5 | T5 驗證（Phase 1） | Lighthouse（`npx lighthouse <url>`：Installable + 離線 reload）＋ 新增 manifest/SW 註冊 E2E check ＋ 既有 `e2e_smoke`（69）＋ `e2e_offline`（105）全綠（BR12）；**Spike S2 / S4 驗證**（子路徑 start_url/scope、Lighthouse 通過條件） | 3, 4 | ✅ 完成（S2/S4 以 CDP installability 驗證，§9.7） |
| 6 | T6 文件 | README 補「安裝 App」操作說明 + 本機驗證指令；本規格即 T6 的 `docs/development/` 產出 | 5 | ✅ 完成 |
| 7 | **Spike S1（Phase 2 前置）** | 小型 spike：CF Workers 上 Web Crypto VAPID（ES256 JWT）＋ RFC 8291 加密 ＋ 呼叫 push service endpoint 可行性；或評估 `@block65/webcrypto-web-push`；失敗備援 Vercel/Netlify function（web-push 套件）、最後 OneSignal 復議 | — | ✅ 完成（worker/spike/verify.mjs 12/12） |
| 8 | T8 Worker | `worker/wrangler.toml`（KV namespace + [vars] 公鑰/subject）+ `worker/src/index.js` 三端點（§1.4/§3.1）+ VAPID 金鑰產生與 secret 配置（§9.1）+ `worker/src/index.test.js`（HDL-01~11） | 7 | ✅ 完成（18/18） |
| 9 | T9 前端訂閱 | `pwa.js` subscriptionUI / fetchVapidPublicKey / subscribeFlow / unsubscribeFlow（user gesture 防護、防重入 F-22、iOS 分支 E8/EC6）；`app.js` initPwaPush + toggle 整合；`sw.js` push / notificationclick / notificationclose（cache bump v3）；`styles.css` 訂閱樣式；`tests/unit/pwa.test.js` 補齊（F-05~F-15, F-19~F-26） | 3, 8 | ✅ 完成 |
| 10 | T10 爬蟲端 | `fetch_prices.py` `--notify`（load_baseline / detect_drops / select_top_drops / should_notify / within_weekly_window / build_notify_payload / call_notify，§1.3）；`tests/unit/test_pwa_drops.py`（SYS-01~15，stdlib unittest） | 8 | ✅ 完成（34/34 Python） |
| 11 | T11 workflow + secret | `weekly-crawl.yml` Commit 後追加「Detect drops & notify」step（Bearer `PUSH_API_TOKEN`，`if:` 守衛未設 secret 時跳過）；README 註明 secret 設定與 Worker 部署步驟（§9.2） | 10 | ✅ 完成 |
| 12 | T12 E2E + 全量回歸 | `tests/e2e_pwa.py`（INT-01~07 + E2E-01~49：mock worker / beforeinstallprompt stub / permission & PushManager stub / CDP dispatchPushEvent / dispatchNotificationClickEvent / iOS UA / set_offline）；`tests/mock_worker.py`（可選 helper）；全量回歸（`node --test tests/unit/` + `node --test worker/` + `e2e_smoke` + `e2e_offline`）+ Lighthouse 複測（BR12） | 8, 9, 10 | ✅ 完成（e2e_pwa 174/174；本機 Chromium 149 已移除 CDP dispatch* 方法 → 以 SW 執行緒內建構事件觸發真實 handler，§6.0 技術註記） |
| 13 | T13 文件收尾 | README 補推播說明與 iOS 限制（需加到主畫面且 16.4+）；決策文件補實作狀態；**Spike S3 實機驗證**（iOS 16.4+ 親友實機，無機則列 MAN-02/04） | 12 | ✅ 完成（S3：環境無 iPhone → 列 MAN-02/04/06/08 手動驗證項，本步驟完成以「手動清單」收尾） |

> DAG 說明：1 → 2 → 3 → 4 → 5 → 6（Phase 1 線性）；7（Spike S1）獨立、可與 Phase 1 平行 → 8（Worker 需 S1 結論）→ 9、10 平行於 8 之後（9 另需 3 的 pwa.js；10 需 8 的 /notify 合約）→ 11（需 10）→ 12（需 8/9/10，E2E 需功能齊備）→ 13（需 12）。無循環。
>
> 驗證流程（§9.5）：`python -m http.server 8000` → `node --test tests/unit/` → `node --test worker/src/index.test.js` → `python -m unittest discover -s tests/unit` → `python tests/e2e_smoke.py` → `python tests/e2e_offline.py` → `python tests/e2e_pwa.py` → `npx lighthouse <url>`。

---

## 9. 基礎架構設定

### 9.1 Cloudflare Worker 部署（Phase 2，T8）

```bash
# ① 安裝 wrangler 並登入（一次性）
npm i -g wrangler
wrangler login

# ② 建立 KV namespace（訂閱名單）
wrangler kv namespace create SUBSCRIPTIONS
#   → 輸出 binding id，貼到 wrangler.toml [[kv_namespaces]]

# ③ 產生 VAPID 金鑰對（一次性，本機；dev-only，金鑰不進 repo）
# ⚠️ 不用 `npx web-push generate-vapid-keys`（輸出 raw 32B 私鑰，Web Crypto 無法 import 簽章）——
#    必須用 Spike S1 附屬工具一次給齊 JWK + raw point（Spike S1 §3.2 踩坑結論）：
node worker/spike/gen-vapid-keys.mjs
#   → stdout JSON：{ publicKey, privateKeyJwk, privateKeyJwkJson }
#     - publicKey（87 chars raw point b64url）→ 貼到 wrangler.toml [vars] VAPID_PUBLIC_KEY
#     - privateKeyJwkJson → 以 secret 設定（④；EC P-256 JWK JSON，含 x/y/d）

# ④ 設定 secrets（VAPID 私鑰 + 爬蟲→Worker 共用的 PUSH_API_TOKEN）
wrangler secret put VAPID_PRIVATE_KEY     # 貼 ③ 的 privateKeyJwkJson（JWK JSON；不可見、可輪換）
wrangler secret put PUSH_API_TOKEN        # 與 GitHub repo secret 相同值（D6）

# ⑤ 部署
wrangler deploy
#   → 得到 https://airtickets-price-push.<account>.workers.dev
#   → 更新 web/pwa.js CONFIG.PUSH_WORKER_URL（§9.4）
```

`worker/wrangler.toml`：

```toml
name = "airtickets-price-push"
main = "src/index.js"
compatibility_date = "2026-08-15"

[vars]
VAPID_PUBLIC_KEY = "<base64url public key>"   # 公開：/vapid-public-key 回傳、前端訂閱使用
VAPID_SUBJECT = "mailto:you@example.com"      # VAPID JWT 的 sub 聲明（可任意 email）

[[kv_namespaces]]
binding = "SUBS"                              # 訂閱名單（key = sub:{endpoint}；HDL-10 裝置單位）
id = "<由 wrangler kv namespace create 產生>"

# VAPID_PRIVATE_KEY / PUSH_API_TOKEN 為 secrets（wrangler secret put，不寫入本檔）
```

**CORS（重要）**：頁面在 `github.io`、Worker 在 `workers.dev`（跨域）。三端點回應須帶 `Access-Control-Allow-Origin: *`，並處理 OPTIONS preflight（§1.4 cors 物件）；否則前端 `fetch('/vapid-public-key')` / `POST /subscribe` 會因 CORS 失敗（E3/E2 假象）。

### 9.2 GitHub Actions（Phase 2，T11）

`weekly-crawl.yml` 在既有「Commit data & api」步驟**之後**追加（既有爬蟲/bulid/commit 步驟一字不動，BR13）：

```yaml
      - name: Detect drops & notify（PWA Phase 2；需先設定 PUSH_API_TOKEN secret）
        if: ${{ secrets.PUSH_API_TOKEN != '' }}   # 未設定 secret（Phase 1 部署）→ 跳過
        env:
          PUSH_API_TOKEN: ${{ secrets.PUSH_API_TOKEN }}
          PUSH_NOTIFY_URL: https://airtickets-price-push.<account>.workers.dev/notify
        run: python fetch_prices.py --notify
        # 失敗 → 本 step 標記失敗；爬蟲資料已於前一步 commit（E6 語意：資料照常提交）
```

- **GitHub repo secret**：首次引入 1 個 `PUSH_API_TOKEN`（Settings → Secrets → Actions；與 Worker `PUSH_API_TOKEN` secret 同值）。外洩可兩邊同時輪換（E6 / 風險登錄）。
- 步驟位置理由：notify 在 Commit **之後** → ① 基準讀「上一週 data 檔」不受本次 build 污染（§1.3 陷阱）② E6「資料已提交」語意成立（401 失敗不影響 commit）。
- 本機手動觸發 `workflow_dispatch` 時：`within_weekly_window` 阻擋同週二次發送（SYS-15）；資料無變化（`detect_drops` 空）亦不發（EC1）。

### 9.3 GitHub Pages 子路徑（Phase 1/2，S2）

- 頁面路徑：`https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/`；`manifest.webmanifest` 以相對路徑 `<link rel="manifest" href="manifest.webmanifest">` 引用，`start_url`/`scope` 皆 `./` → 自動解析為 `/AirTicketsPrice/web/`（S2 實測於步驟 5 驗證）。
- **deep-link 基準**：通知 `data.url` 為相對路徑，sw.js 以 `registration.scope`（=`/AirTicketsPrice/web/`）拼接 → 正式 URL `/AirTicketsPrice/web/?route=TPE-NRT`（EC3 / F-14）；不需硬編碼路徑前綴。
- **Secure context**：GitHub Pages HTTPS 天然滿足 SW / PushManager / Notification API；`file://` 下無 SW、無安裝資格、無推播（E14，僅開發限制）。
- SW 註冊以相對路徑 `navigator.serviceWorker.register('sw.js')`（既有 app.js init 行為，不動）→ scope 自動 `/AirTicketsPrice/web/`。

### 9.4 前端 Worker URL 設定

`web/pwa.js` `CONFIG.PUSH_WORKER_URL` 為部署相關常數（§2.4 註解標示）：`wrangler deploy` 後更新為實際 workers.dev 網域並 commit。訂閱按鈕依「公鑰抓取成功與否」啟用/停用（E3），Worker 未部署期間頁面其餘功能完全正常。

### 9.5 本機開發 / 驗證指令

```bash
cd /fork/YuHaoLiaoSideProject/AirTicketsPrice
python scripts/gen_icons.py --check                 # 圖示尺寸驗證（F-18 輔助）
python -m http.server 8000                          # repo 根；開 http://localhost:8000/web/
node --test tests/unit/                             # 純函式（aggregate + cache + pwa）
node --test worker/src/index.test.js                # Worker 單元（HDL-01~11）
python -m unittest discover -s tests/unit           # 爬蟲 Python 單元（含 test_pwa_drops，SYS-01~15）
python tests/e2e_smoke.py                           # 既有 69 checks（回歸門檻，不得退步）
python tests/e2e_offline.py                         # 既有 105 checks（回歸門檻，不得退步）
python tests/e2e_pwa.py                             # 新增 PWA E2E（INT-01~07 + E2E-01~49，mocked）
npx lighthouse http://localhost:8000/web/           # Phase 1 與 Phase 2 結束各跑一次（MAN-11）
```

### 9.6 成本與配額（D2 / BR15）

| 項目 | 配額 | 本次用量 |
|------|------|---------|
| Cloudflare Workers 免費 tier | 100k req/day | 每週 1 次 /notify + 訂閱/公鑰讀取；<50 訂閱者，連零頭都用不到 |
| Workers KV | 1GB / 100k 讀 1k 寫每日 | 每訂閱 ~500B，<50 筆 |
| GitHub Pages | 免費 | 既有 |
| GitHub secret | 1 個（`PUSH_API_TOKEN`） | 首次引入（D6 明示） |

→ 維持 **$0**（MAN-13 每月檢查儀表板）。

### 9.7 Spike 驗證狀態（待開發階段驗證）

| Spike | 驗證方式 | 狀態 |
|-------|---------|------|
| S1（CF Workers Web Push / Web Crypto VAPID） | 步驟 7 spike：Worker 內產生 ES256 JWT + RFC 8291 加密並呼叫 push service（或 mock endpoint） | ✅ 驗證通過（2026-08-15）：`worker/spike/verify.mjs` 12/12 全綠（含 RFC 8291 §5 官方範例解密 + 與 web-push 底層 http_ece 雙向互通）；T8 已移植為 `worker/src/vapid.mjs` 並於 `worker/src/index.js` 投入使用（`node --test worker/src/index.test.js` 16/16 綠） |
| S2（子路徑 scope / start_url / deep-link） | 步驟 5：以 `/AirTicketsPrice/web/` 子路徑 serve 安裝與 notificationclick 實測 | ✅ Phase 1 步驟 5 驗證通過（2026-08-15）：`http.server` 子路徑 `/web/` 下 SW scope = `/web/`、manifest `start_url`/`scope` `./` 解析為 `/web/`（`tests/e2e_pwa.py` + 實測記錄）；deep-link 拼接以 F-14 單元測試 + Phase 2 E2E 覆蓋 |
| S3（iOS 16.4+ 實際推播） | 步驟 13：親友實機（iOS Safari → 加到主畫面 → 訂閱）；無 iPhone 則列 MAN-02/04 手動 | ⏳ **僅手動驗證（T13 收尾註記）**：本開發環境無 iPhone 實機，iOS 16.4+ 真實訂閱與 APNs 送達**無法自動化驗證**（headless 無法模擬 iOS Safari 加到主畫面／APNs 推播）→ 明示列為 **MAN-02 / MAN-04 / MAN-06 / MAN-08 手動驗證項**（測試計畫 §7）：需以真實 iPhone Safari 開啟 → 加到主畫面 → 訂閱 → 等待週五下降通知；自動化已覆蓋的部分為 iOS UA 判定（E2E-04）、iOS 16.4+ 訂閱流程（E2E-12）、<16.4 限制提示（E2E-34）、未安裝提示（E2E-22） |
| S4（Lighthouse installable 通過條件） | 步驟 5 / 12：**Lighthouse 13 起已移除 PWA 安裝稽核（installable / works-offline），改以 CDP `Page.getAppManifest` + `Page.getInstallabilityErrors` 驗證**（`tests/e2e_pwa.py`；errors 為空 = 可安裝，實測通過）；**Lighthouse 列為 MAN 手動驗證項**（MAN-11） | ✅ Phase 1 步驟 5 以 CDP 驗證通過（2026-08-15） |

---

## 附錄 A：BDD 覆蓋矩陣（52/52）

> 對照測試計畫附錄 A（測試案例編號一致）；規格章節欄為本文件內的對應實作/章節。Scenario Outline 展開：情境 6（4 rows）→ F-05a~d / E2E-06a~d；情境 46（2 rows）→ F-19a/b / E2E-44a/b。

### Happy Path（14）

| # | BDD Scenario | Tags | 規格章節 / 實作單元 | 測試案例 |
|---|--------------|------|---------------------|---------|
| 1 | 符合安裝條件的瀏覽器顯示「安裝 App」按鈕（P1-A） | @smoke @happy-path @p0 | §2.4 installStateMachine、§2.6 beforeinstallprompt、§6 BR4 | F-01, F-02, E2E-01, E2E-38 |
| 2 | 接受安裝後主畫面出現 App 圖示並以 standalone 開啟（P1-A） | @smoke @happy-path @p0 | §2.2/§2.3 manifest + icons、§2.6 appinstalled、§5.1 | E2E-02, MAN-01 |
| 3 | 取消安裝確認後按鈕保留可再次觸發（P1-A） | @happy-path @p0 | §2.4 cancelled 狀態、§5.1 | F-02, E2E-03 |
| 4 | iOS Safari 依提示「加到主畫面」後以 standalone 開啟（P1-B） | @happy-path @p1 | §2.4 isIOS、§2.5 iosHint、§6 BR5 | F-04, E2E-04, MAN-02 |
| 5 | 已安裝模式隱藏安裝入口且離線能力照常（P1-C） | @smoke @happy-path @p0 @regression | §2.4 shouldShowInstall、§2.7 SHELL、§6 BR12 | F-03, E2E-05 |
| 6 | 頁面依權限與訂閱狀態顯示對應的提醒入口（P2-A Outline ×4） | @smoke @happy-path @p0 | §2.4 subscriptionUI 三態、§2.6 renderSubUI、§5.2 | F-05a~d, E2E-06a~d |
| 7 | 點「開啟票價提醒」於 user gesture 下觸發權限詢問（P2-A） | @smoke @happy-path @p0 | §2.4 subscribeFlow（僅 click handler）、§6 E4 | F-06, E2E-07 |
| 8 | 同意權限後訂閱成功狀態變「已訂閱」（P2-A） | @smoke @happy-path @p0 | §2.4 subscribeFlow、§3.1 POST /subscribe、§4.1 | F-07, INT-05, E2E-08 |
| 9 | 每週五票價下降時收到單則摘要通知（P2-B） | @smoke @happy-path @p0 | §1.3 detect_drops、§3.2 承載、§2.7 push handler | SYS-01, SYS-03, F-19, E2E-09 |
| 10 | 點擊通知開啟對應航線頁面（P2-B） | @smoke @happy-path @p0 | §2.4 resolveNotificationUrl、§2.7 notificationclick、§4 | F-14, E2E-10, MAN-03 |
| 11 | 關閉票價提醒完成退訂且不再收到通知（P2-C） | @happy-path @p0 | §2.4 unsubscribeFlow、§3.1 remove、§5.4 | F-08, E2E-11 |
| 12 | iOS 已加到主畫面的 PWA 可正常訂閱（P2-D） | @happy-path @p1 | §2.4 iosVersionAtLeast + standalone、§6 EC6 | F-21, E2E-12, MAN-02, MAN-04 |
| 13 | 離線時點擊通知仍可開啟頁面看快取資料（P2-E） | @happy-path @p1 @regression | §2.7 notificationclick、既有離線快取、§4.1 | INT-01, INT-02, E2E-13 |
| 14 | 系統每週五自動偵測下降並廣播通知，使用者零操作（P2-F） | @smoke @happy-path @p0 | §1.3 main_notify、§1.4 notify handler、§9.2 workflow | SYS-10, HDL-04, E2E-14, E2E-48 |

### Error Handling（E1–E14）

| # | BDD Scenario | Tags | 規格章節 | 測試案例 |
|---|--------------|------|---------|---------|
| 15 | 權限被封鎖時顯示拒絕引導且不重複詢問（E1） | @error-handling @p0 | §6.1 E1、§2.4 subscriptionUI denied | F-11, E2E-15, MAN-05 |
| 16 | 訂閱失敗顯示可重試提示且不影響頁面瀏覽（E2） | @error-handling @p0 | §6.1 E2、§2.4 subscribeFlow 失敗分支 | F-09, E2E-16 |
| 17 | 取得 VAPID 公鑰失敗時停用訂閱按鈕（E3） | @error-handling @p0 | §6.1 E3、§2.4 fetchVapidPublicKey | F-10, E2E-17 |
| 18 | 權限詢問被忽略時維持未訂閱且無錯誤提示（E4） | @error-handling @p1 | §6.1 E4、§5.2 | F-12, E2E-18 |
| 19 | 訂閱過期（push service 回 404/410）時自動清理並可重新訂閱（E5） | @error-handling @p1 | §6.1 E5、§1.4 404/410 清理、§5.4 | HDL-07, F-20, E2E-19 |
| 20 | 通知發送授權失敗（401）不影響使用者且資料照常提交（E6） | @error-handling @p1 | §6.1 E6、§1.3 call_notify 不 raise、§9.2 步驟位置 | HDL-05, SYS-11, E2E-20, MAN-15 |
| 21 | 推播服務沒有訂閱者時空廣播回成功（E7） | @error-handling @p2 | §6.1 E7、§1.4 空廣播 | HDL-06, E2E-21 |
| 22 | iOS 未加到主畫面時提示且不發無效權限請求（E8） | @error-handling @p1 | §6.1 E8、§2.4 subscribeFlow iOS 分支 | F-13, E2E-22, MAN-06 |
| 23 | 離線點通知且目標航線未快取時顯示提示並停留原航線（E9） | @error-handling @p1 | §6.1 E9、既有離線功能 E2 | INT-01, E2E-23 |
| 24 | 通知對應分頁已開啟時聚焦既有分頁並切換航線（E10） | @error-handling @p1 | §6.1 E10、§2.7 notificationclick focus/navigate | INT-03, E2E-24 |
| 25 | 下降航班超過 3 條時只發下降幅度最大的 3 條（E11） | @error-handling @p0 | §6.1 E11、§1.3 select_top_drops、§1.4 slice | SYS-04, SYS-05, E2E-25 |
| 26 | 首次爬蟲無基準資料時跳過通知僅建立基準（E12） | @error-handling @p1 | §6.1 E12、§1.3 should_notify(prev=None) | SYS-06, E2E-26 |
| 27 | 滑掉通知時無任何後續動作（E13） | @error-handling @p1 | §6.1 E13、§2.7 notificationclose | F-15, E2E-27 |
| 28 | file:// 本機開啟時無 SW 與推播，降級為一般頁面（E14） | @error-handling @p2 | §6.1 E14、§2.6 initPwaPush secure context、§9.3 | F-24, INT-06, E2E-28, MAN-07 |

### Edge Cases（8）

| # | BDD Scenario | Tags | 規格章節 | 測試案例 |
|---|--------------|------|---------|---------|
| 29 | 非 drop_last 條件一律不觸發通知 | @edge-case @p0 | §6.2 EC1、§1.3 detect_drops | SYS-02, SYS-14, E2E-29 |
| 30 | 通知頻率與爬蟲同頻，維持每週一次 | @edge-case @p1 | §6.2 EC2、§1.3 within_weekly_window、§9.2 | SYS-08, SYS-15, E2E-30 |
| 31 | 子路徑部署下通知 deep-link 以 SW scope 為基準拼接 | @edge-case @p1 | §6.2 EC3、§2.4 resolveNotificationUrl、§9.3 | F-14, INT-04, E2E-31 |
| 32 | 訂閱以瀏覽器/裝置為單位，無跨裝置同步 | @edge-case @p1 | §6.2 EC4、§1.4 SUB_PREFIX 裝置單位 | HDL-10, E2E-32 |
| 33 | 訂閱狀態與離線快取彼此獨立，離線不失效 | @edge-case @p1 | §6.2 EC5、§5.3 資料 schema 不動 | INT-02, E2E-33 |
| 34 | iOS 16.4 以下版本收不到推播且無其他替代方案 | @edge-case @p2 | §6.2 EC6、§2.4 iosVersionAtLeast | F-21, E2E-34, MAN-08 |
| 35 | 裝置通知設定（勿擾/靜音）影響通知可達性 | @edge-case @p2 | §6.2 EC7（裝置層級，文件註記） | MAN-09 |
| 36 | 同時有多則通知時點擊一則只開啟該則航線 | @edge-case @p2 | §6.2 EC8、§2.7 notificationclick | INT-07, E2E-35, MAN-10 |

### Business Rules（16）

| # | BDD Scenario | Tags | 規格章節 | 測試案例 |
|---|--------------|------|---------|---------|
| 37 | manifest 欄位齊全且 Lighthouse 安裝與離線稽核通過 | @business-rules @p0 | §2.2、§9.7 S4、§6 BR1 | F-16, E2E-36, MAN-11 |
| 38 | maskable 圖示主體落在 80% safe zone 內 | @business-rules @p1 | §2.3 gen_icons.py、§6 BR2 | F-18, MAN-12 |
| 39 | index.html 具備 PWA 所需連結與 iOS meta | @business-rules @p0 | §2.5 六項、§6 BR3 | F-17, E2E-37 |
| 40 | 「安裝 App」按鈕只在瀏覽器觸發安裝事件後出現 | @business-rules @p0 | §2.4/§2.6、§6 BR4 | F-01, F-03, E2E-38 |
| 41 | iOS 依 UA 顯示「加到主畫面」提示且圖示為 apple-touch-icon | @business-rules @p1 | §2.4 isIOS、§2.3 180 圖示、§6 BR5 | F-04, E2E-39 |
| 42 | 下降比對以最近一次抓取資料為基準（drop_last） | @business-rules @p0 | §1.3 load_baseline、§6 BR6 | SYS-07, E2E-40 |
| 43 | GET /vapid-public-key 回傳前端訂閱所需公鑰 | @business-rules @p1 | §3.1、§1.4 vapidPublicKey、§6 BR7 | HDL-01, E2E-41 |
| 44 | POST /subscribe 驗證後將訂閱寫入 KV | @business-rules @p1 | §3.1、§1.4 isValidSubscription、§6 BR8 | HDL-02, HDL-03, E2E-42 |
| 45 | POST /notify 驗證 token 後對全部訂閱者廣播並清理失效訂閱 | @business-rules @p0 | §3.1、§1.4 notify handler、§6 BR9 | HDL-04, HDL-05, HDL-07, E2E-43 |
| 46 | 通知承載符合單則摘要格式（Outline ×2） | @business-rules @p0 | §3.2 fixture、§1.4/§2.4 formatNotification、§6 BR10 | SYS-09, F-19a/b, HDL-08, E2E-44a/b |
| 47 | 憑證分層：公鑰公開、私鑰與訂閱名單只在推播服務端 | @business-rules @p1 | §1.4、§9.1 secrets、§6 BR11 | HDL-09, F-25, E2E-45 |
| 48 | 既有測試與 Lighthouse 全量零回歸 | @business-rules @p0 @regression | §6 BR12、§8 步驟 5/12、§9.5 | E2E-46 |
| 49 | 既有爬蟲、data/ 與 api/ 維持原樣，僅追加通知呼叫 | @business-rules @p1 @regression | §9.2 workflow 追加 step、§6 BR13 | SYS-13, E2E-47 |
| 50 | 訂閱與通知流程以 Playwright mock push service 端到端驗證 | @business-rules @p1 | §8 步驟 12、tests/e2e_pwa.py、§6 BR14 | E2E-48 |
| 51 | 公開免登入、少數親友規模且維持 $0 成本 | @business-rules @p2 | §9.6、§6 BR15 | E2E-49, MAN-13 |
| 52 | README 與文件完整說明安裝、推播與 iOS 限制 | @business-rules @p2 | §8 步驟 6/13、§6 BR16 | MAN-14 |

> 覆蓋率：52/52 全數對應。智能補充（SYS-12/14/15、HDL-11、F-22~F-26、EC9 下述）為邊界／並發／依賴失敗／生命週期補充，不取代任何 BDD 對應。

---

## 附錄 B：實作對照備註

- **函式命名對照**：§1.3（Python）與 §1.4（JS）的函式名 = 測試計畫 §2/§3 的函式名（`detect_drops` / `select_top_drops` / `within_weekly_window` / `build_notify_payload` / `call_notify` / `formatNotification` / `isValidSubscription` 等），實作與測試皆以本規格為準，避免兩套命名。
- **data.url 語意**：payload 帶**相對 SW scope** 路徑（`?route=TPE-NRT`），sw.js 以 `registration.scope` 拼接（§3.2 語意對照）。上游文件的 `/web/?route=TPE-NRT` 一律視為解析後絕對路徑的描述，不作為承載值。
- **退訂與「三端點」**：維持三端點路徑面（`/vapid-public-key` / `/subscribe` / `/notify`）；退訂以 `POST /subscribe {endpoint, action:'remove'}` 表達（測試計畫 E2E-11 即驗證 mock 端收到此 remove 請求）。
- **通知格式化單點**：`formatNotification(drops)` 於 Worker（生產）與 pwa.js（前端單元測試 F-19a/b 合約 + 未來頁內預覽）各有一份**同簽名實作**（皆接受 drops 陣列、逐筆一行以 `\n` 連接、`data.url` 取 `drops[0].route`；空陣列 → body「有票價更新」）；SW 信任 Worker 格式化結果，僅做欄位防禦（§5.4）。
- **基準陷阱**：`--notify` 的基準**只能**從 `data/` 原始檔選取（上一週），不可讀取本次 run 已覆寫的 `api/latest.json`（§1.3 註記；E6 語意同時要求 notify 步驟在 Commit 之後）。
- **上線順序與回歸**：Phase 1 上線先跑步驟 5 全量回歸（Lighthouse + 69/105 checks）再進入 Phase 2；Phase 2 每步合併前 `e2e_smoke` / `e2e_offline` 必須全綠（BR12 合併門檻）。
- **上游文件回鏈**：建議於 `docs/tech-decisions/PWA-2026-08-15.md`（決策後續）、`docs/interaction-flows/PWA.md`（下游建議）、`docs/test-plans/PWA測試計畫.md`（背景規格）補上指向本文件的「開發規格」連結。
- **PWA 上線後手動確認**：`wrangler deploy` 後以 curl 驗證 `GET /vapid-public-key` 與 `OPTIONS` preflight；iOS 16.4+ 實機訂閱（S3）納入 MAN-02/04；每月檢查 CF 用量儀表板維持 $0（MAN-13）。
