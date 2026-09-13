# app.js 拆分計畫

> **目標**：將 1714 行的 `web/app.js` 拆分為 4 個模組（`chart.js`、`modal.js`、`controls.js`、`app.js`），降低單檔複雜度、明確職責邊界。
>
> **原則**：本計畫只分析不改碼。所有新模組沿用既有 UMD（IIFE + `global.Xxx = factory()`）模式，與 `aggregate.js` / `cache.js` / `pwa.js` 一致。

---

## 1. 現有模組架構

```
aggregate.js   → window.PriceAgg  （純函式：聚合、計算、format）
cache.js       → window.OfflineCache （IDB 封裝 + 同步決策純函式）
pwa.js         → window.Pwa （安裝/訂閱狀態機 + 通知純函式）
app.js (IIFE)  → 佔據：DOM refs、狀態、資料層、圖表渲染、Modal、Controls、PWA 整合、Init
```

---

## 2. 函式分佈地圖（按行號）

### 2.1 chart.js — SVG 圖表渲染（~200 行）

| 行號（約） | 函式/區塊 | 說明 |
|-----------|----------|------|
| 803-810 | `NS`, `W/H/M`, `fmt`, `fmtD` | SVG 常數、格式化 |
| 807-810 | `esc()`, `svgEl()` | HTML 跳脫、SVG 元素工廠 |
| 811-812 | `rangeLabel()` | 範圍標籤 |
| 814-1005 | `buildChart()` | ⭐ **主圖表渲染**：網格、旺季區塊、平均線、資料點、折線、dot、badge、最便宜標記、chart-title |
| 1008-1050 | `showTip()`, `hideTip()` | Tooltip 顯示/隱藏 |
| 1053-1070 | `SVG_ARROW_UP/DOWN/CAL` | 漲跌表 SVG 箭頭常數 |

**依賴**：`routeCache`（Week[]）、`state.route / state.flight / state.range`、`currentVisible`、`CONFIG.SVG / CONFIG.RANGES / CONFIG.ROUTES`、`PriceAgg.*`（純函式）、`summaryData()`

### 2.2 modal.js — 詳情 Modal（~250 行）

| 行號（約） | 函式/區塊 | 說明 |
|-----------|----------|------|
| 42-57 | Modal DOM refs | `detailOverlay` 等 12 個 DOM ref |
| 59-61 | `lastTriggerCcard`, `currentDetailWeek`, `currentVisible` | Modal 內部狀態 |
| 1053-1070 | SVG 箭頭常數 | 與 chart.js 共用 → 從 PriceAgg 或獨立提取 |
| 1073-1100 | `aggregateHistory()` | 從 CACHE.units 聚合歷史走勢 |
| 1103-1115 | `getRawFlightsForWeek()` | 從 CACHE.units 取航班列表 |
| 1118-1145 | `buildSingleFlightHTML()` | 單航班卡片 HTML |
| 1148-1165 | `openDetail()`, `closeDetail()` | Modal 開/關 |
| 1168-1200 | Modal 事件綁定 | Escape、focus trap、retry |
| 1203-1300 | `renderDetailFlight()` | 航班表格/卡片渲染 |
| 1305-1400 | `renderDetailChart()` / `renderDetailChartSinglePoint()` / `renderDetailChartLine()` | Modal 內歷史圖表 |
| 1403-1415 | `renderDetailFooter()` | Modal footer |

**依賴**：`CACHE.units`、`state.route`、`routeCache`、`PriceAgg.*`（latestPrice、globalAverage、diffPct、datesFromUrl、formatChangePct）

### 2.3 controls.js — 控制項渲染與事件綁定（~100 行）

| 行號（約） | 函式/區塊 | 說明 |
|-----------|----------|------|
| 753-770 | `getRegions()`, `routesForRegion()`, `regionForRoute()` | 地區/航線映射 |
| 772-790 | `renderRegionSel()`, `renderRouteTabs()`, `renderRangeSeg()` | 控制項 DOM 渲染 |
| 792-800 | `renderFlightSel()` | 航班下拉選項 |
| 801 | `setToolbarDisabled()` | 控制項 disable 狀態 |
| 735-750 | `initControls()` | ⭐ **事件綁定**：region change、route click、flight change、range click |

**依賴**：`INDEX.regions`、`CONFIG.ROUTES / CONFIG.REGIONS / CONFIG.RANGES`、`state`（全域）、回調 `drawCurrentRoute()` / `buildChart()`

### 2.4 app.js — 初始化 + 資料層 + 串接（剩餘 ~1160 行）

| 行號（約） | 區塊 | 說明 |
|-----------|------|------|
| 1-40 | DOM refs | 保留（串接層需要全部 ref） |
| 73-100 | PWA Install UI | `renderInstallUI()`, install 事件 |
| 103-170 | PWA Push UI | `renderSubUI()`, `initPwaPush()`, sub 事件 |
| 174-210 | 應用狀態 | `state`, `INDEX`, `CACHE`, `routeCache` 等 |
| 213-225 | API_ROOT | URL 基底計算 |
| 228-330 | 資料層 | `fetchIndexWithEtag()`, `fetchUnit()`, `fetchUnitsConditional()`, `jsonForResult()`, `unitsUrlsForRoute()` |
| 332-380 | 快取操作 | `weeksFromCache()`, `persistCacheSafe()` |
| 383-410 | Sync UI | `setSyncStatus()`, `setRefreshDisabled()` |
| 413-445 | 比對失敗 | `markCompareFailed()` |
| 448-505 | 離線層 | `enterOffline()`, `onOnline()`, `showRouteHint()` |
| 508-580 | 手動更新 + PWA 更新偵測 | `manualUpdate()`, `initUpdateDetection()` |
| 583-700 | 增量同步 | `refreshRouteCache()`, `backgroundCompare()`, `incrementalSync()` |
| 703-750 | 航線載入 | `loadRoute()` |
| 1418-1480 | 漲跌表 | `renderChangeCards()`, `bindCcardClicks()` |
| 1483-1500 | Summary | `renderSummary()` |
| 1503-1530 | 狀態處理 | `setLoading()`, `showError()`, `showStale()` |
| 1533-1714 | Init 流程 | `drawCurrentRoute()`, `firstLoad()`, `drawCurrentRouteFromCache()`, `init()` |
| 1680-1714 | Tooltip 事件委派 | chart mousemove/focus 事件 |

---

## 3. 共享狀態

### 3.1 全域變數（app.js 持有，其他模組讀寫）

| 變數 | 類型 | 讀取模組 | 說明 |
|------|------|---------|------|
| `state` | `{ region, route, flight, range, loading }` | controls.js（寫）、chart.js（讀）、modal.js（讀 route）、app.js（讀寫） | **核心共享狀態** |
| `INDEX` | `{ routes, trips, generated_at, regions }` | controls.js（讀 regions）、app.js（讀寫） | index.json 快照 |
| `CACHE` | `{ meta, units }` | modal.js（讀 units）、app.js（讀寫） | IDB 記憶體投影 |
| `routeCache` | `Map<routeId, Week[]>` | chart.js（讀）、modal.js（間接）、app.js（讀寫） | 航線週資料快取 |
| `currentVisible` | `Week[]` | chart.js（寫/讀）、modal.js（讀） | 目前可見週（buildChart 計算後供 bindCcardClicks 使用） |
| `loadToken` | `number` | app.js（讀寫） | 競態防護 |
| `syncState` | `string` | app.js（讀寫） | 同步狀態機 |
| `syncing` | `boolean` | app.js（讀寫） | 同步互斥旗標 |

### 3.2 DOM refs（全域 const，app.js 第 20-38 行）

目前所有 DOM ref 集中在 app.js 頂部。拆分後：
- **chart.js** 需要：`chart`, `chartWrap`, `tip`, `chartTitle`
- **modal.js** 需要：`detailOverlay`, `detailPanel`, `detailBackdrop`, `detailTitle`, `detailClose`, `detailFlightBody`, `detailChartWrap`, `detailChart`, `detailStats`, `detailFooter`, `detailSkeleton`, `detailError`, `detailRetry`
- **controls.js** 需要：`regionSel`, `routeTabs`, `flightSel`, `rangeSeg`, `toolbar`, `progress`
- **app.js** 需要：其餘所有 ref

---

## 4. 跨模組通訊

### 4.1 依賴圖

```
                    ┌─────────────┐
                    │  app.js     │
                    │ (串接層)    │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │controls.js│   │ chart.js │    │ modal.js │
    └──────────┘    └──────────┘    └──────────┘
```

### 4.2 跨模組通訊方式

| 場景 | 觸發方 | 接收方 | 通訊方式 | 說明 |
|------|--------|--------|---------|------|
| 切航線 → 重繪圖表 | controls.js | app.js → chart.js | **Callback** | `controls.onRouteChange(routeId) → app.js 串接 drawCurrentRoute()` |
| 切航線（離線未載入）→ 顯示 hint | controls.js | app.js | **Callback** | `controls.onRouteChange` 內部判斷，或 app.js 注入回調 |
| 切航班 → 重繪圖表 | controls.js | chart.js | **Callback**（經 app.js） | `onFlightChange → buildChart()` |
| 切範圍 → 重繪圖表 | controls.js | chart.js | **Callback**（經 app.js） | `onRangeChange → buildChart()` |
| ccard 點擊 → 開 Modal | chart.js（bindCcardClicks） | modal.js | **Callback**（經 app.js） | `openDetail(week, trigger)` |
| Modal 內 Retry → 重開 Modal | modal.js | modal.js（自身） | 內部方法 | `openDetail(currentWeek)` |
| 背景同步完成 → 刷新圖表 | app.js | chart.js | **Callback** | `refreshRouteCache → buildChart()` |

### 4.3 推薦通訊模式

採用 **Callback 注入**（最簡單，符合既有 IIFE 模式）：

```js
// controls.js 建構時接收回調
function createControls(state, callbacks) {
  // callbacks = { onRouteChange, onFlightChange, onRangeChange, onRegionChange }
}
// chart.js 建構時接收回調
function createChart(state, callbacks) {
  // callbacks = { onCcardClick }  // 點 ccard → 開 Modal
}
// modal.js 建構時接收回調
function createModal(state, callbacks) {
  // callbacks = { onRetry }  // retry → 可選重載
}
```

不使用 Event / Pub-Sub，因為：
- 現有 IIFE 模式不依賴模組載入器
- Callback 注入最直觀、最少抽象層
- 模組數量少（4 個），不需要解耦框架

---

## 5. 拆分計畫

### 5.1 新檔案職責邊界

#### `web/modal.js`（~250 行）
**職責**：詳情 Modal 的開關、航班表格/卡片渲染、歷史走勢圖表、footer

```js
// UMD 匯出 window.PriceModal
window.PriceModal = (function() {
  function createModal(state, deps) {
    // deps = { CACHE, routeCache, PriceAgg, DOM_REFS: { detailOverlay, ... } }
    return {
      openDetail(week, triggerCcard) { ... },
      closeDetail() { ... },
      aggregateHistory(outboundDate, routeId) { ... },
      getRawFlightsForWeek(week) { ... },
      // 內部：renderDetailFlight, renderDetailChart, renderDetailFooter, buildSingleFlightHTML
    };
  }
  return { createModal };
})();
```

**不負責**：ccard 點擊綁定（由 chart.js 或 app.js 負責呼叫 `openDetail`）

#### `web/chart.js`（~200 行）
**職責**：主 SVG 圖表渲染（網格、旺季、平均線、折線、dot、badge、最便宜標記）、Tooltip 顯示/隱藏

```js
window.PriceChart = (function() {
  function createChart(state, deps) {
    // deps = { routeCache, currentVisible, PriceAgg, CONFIG, DOM_REFS: { chart, chartWrap, tip, chartTitle } }
    return {
      buildChart() { ... },
      showTip(w, ev) { ... },
      hideTip() { ... },
      bindChartEvents() { ... },  // mousemove/focus 事件委派
    };
  }
  return { createChart };
})();
```

**不負責**：漲跌表 `renderChangeCards()`（留在 app.js 或獨立）；Summary（留在 app.js）

#### `web/controls.js`（~100 行）
**職責**：地區下拉、航線 tab、航班 select、範圍 button 的 DOM 渲染 + 事件綁定

```js
window.PriceControls = (function() {
  function createControls(state, deps) {
    // deps = { INDEX, CONFIG, callbacks: { onRouteChange, onFlightChange, onRangeChange, onRegionChange } }
    return {
      renderRegionSel() { ... },
      renderRouteTabs() { ... },
      renderFlightSel(weeks) { ... },
      renderRangeSeg() { ... },
      initControls() { ... },  // 綁定事件，觸發 callback
      setToolbarDisabled(disabled) { ... },
      getRegions() { ... },
      routesForRegion(regionId) { ... },
      regionForRoute(routeId) { ... },
    };
  }
  return { createControls };
})();
```

#### `web/app.js`（剩餘 ~1160 行 → 重構後 ~600 行）
**職責**：初始化、資料層（fetch）、快取操作、同步邏輯、PWA 整合、離線層、串接

```js
(function() {
  'use strict';
  // DOM refs（精簡後）
  // import PriceAgg, OfflineCache, Pwa（全域）
  // import PriceChart, PriceModal, PriceControls（全域）

  // 應用狀態
  const state = { ... };

  // 資料層函式（fetchIndexWithEtag, fetchUnit, fetchUnitsConditional 等）
  // 快取操作（weeksFromCache, persistCacheSafe 等）
  // 同步邏輯（incrementalSync, backgroundCompare 等）
  // 離線層（enterOffline, onOnline 等）
  // PWA 整合（renderInstallUI, initPwaPush, initUpdateDetection）

  // 建構子模組
  const chart = PriceChart.createChart(state, { routeCache, currentVisible, ... });
  const modal = PriceModal.createModal(state, { CACHE, routeCache, ... });
  const controls = PriceControls.createControls(state, {
    INDEX, CONFIG,
    callbacks: {
      onRouteChange: drawCurrentRoute,
      onFlightChange: () => chart.buildChart(),
      onRangeChange: () => chart.buildChart(),
      onRegionChange: drawCurrentRoute,
    }
  });

  // 漲跌表 + Summary + ccard 點擊（保留在此，呼叫 modal.openDetail）
  // Init 流程
  // 啟動
})();
```

---

### 5.2 拆分順序（由安全到困難）

| 順序 | 模組 | 理由 | 風險等級 |
|------|------|------|---------|
| **1** | `modal.js` | 最獨立：只讀 CACHE.units 和 routeCache，不寫 state；純渲染；只有「開/關」一個輸出動作。測試方式：手動點 ccard 開 Modal → 航班表、歷史圖、footer 正確 | 🟢 低 |
| **2** | `chart.js` | 較獨立：只讀 routeCache / currentVisible / state（不寫）；輸出為 DOM 渲染。可與 modal 同時平行開發。風險：tooltip 事件委派需綁在 chart DOM 上 | 🟡 中 |
| **3** | `controls.js` | 中等：會 **寫 state**（切航線/航班/範圍），需回調 app.js 觸發重繪。風險：離線判斷 + showRouteHint 需要 CACHE 資訊 | 🟡 中 |
| **4** | `app.js` 整理 | 最後清理：移除已拆出的函式，確認串接正確。需驗證整體流程（init → 首載 → 同步 → 離線 → 重連） | 🟠 中高 |

---

### 5.3 風險點

#### ⚠️ 高風險：`state` 對象的讀寫分散

`state` 是全域共享狀態，controls.js 寫、chart.js 讀、app.js 讀寫。拆分後若各模組直接引用 `state` 而非透過注入，會形成隱性耦合。

**建議**：`state` 作為 reference type 傳入各子模組（`createXxx(state, ...)`），所有模組讀寫同一物件。不需 getter/setter，但要在文件中標註「此物件為 single source of truth」。

#### ⚠️ 高風險：`currentVisible` 由 chart.js 計算但被 app.js（bindCcardClicks）使用

`buildChart()` 內部計算 `currentVisible = filterRangeWithExpiry(...)` 後，`bindCcardClicks()` 需要它來取得對應 week。拆分後若 `currentVisible` 只存在 chart.js 內部，app.js 或 modal.js 就拿不到。

**建議**：`currentVisible` 設為 app.js 的狀態屬性（`state.currentVisible`），chart.js 計算後寫回，bindCcardClicks 讀取。或 chart.js 提供 `getVisibleWeeks()` 方法。

#### ⚠️ 中風險：PWA Install/Push UI 留在 app.js

PWA 相關函式（`renderInstallUI`, `renderSubUI`, `initPwaPush`, `initUpdateDetection`）涉及 `window.Pwa` 全域物件和 service worker 事件，且與 state 交互較少。

**建議**：保留 in app.js（不另建 pwa-app.js），因為它們已是 app.js 初始化流程的一部分，且 `pwa.js` 已作為純函式庫存在。避免過度拆分。

#### ⚠️ 中風險：`CACHE` 的生命週期

`CACHE` 由 app.js 初始化（首次載入或 IDB 讀取），modal.js 和 chart.js 只讀不寫。但 `refreshRouteCache()` 會修改 routeCache 並可能觸發 modal 重新渲染。

**建議**：modal.js 只在 `openDetail()` 時讀取 `CACHE.units`（已為最新快照），不需要即時同步。

#### ⚠️ 中風險：漲跌表 `renderChangeCards()` 的歸屬

`renderChangeCards()` 生成漲跌卡片 HTML 並綁定 ccard 點擊 → `openDetail()`。它依賴 `currentVisible` 和 modal 的 `openDetail`。

**建議**：保留在 app.js（串接層），因為它是連接 chart 資料與 modal 的橋樑。

#### ⚠️ 低風險：SVG 箭頭常數 `SVG_ARROW_UP/DOWN/CAL`

被 `renderChangeCards()` 使用（保留在 app.js），不需共享。

---

## 6. HTML 腳本載入順序

```html
<!-- 現有 -->
<script src="aggregate.js"></script>
<script src="cache.js"></script>
<script src="pwa.js"></script>

<!-- 新增（必須在 app.js 之前） -->
<script src="modal.js"></script>
<script src="chart.js"></script>
<script src="controls.js"></script>

<!-- app.js 最後載入，串接一切 -->
<script src="app.js"></script>
```

---

## 7. 測試策略

| 模組 | 測試方式 |
|------|---------|
| modal.js | 手動測試：click ccard → 開 Modal → 驗證航班表、歷史圖、footer；Escape 關閉 |
| chart.js | 手動測試：切航線/航班/範圍 → 圖表正確重繪；tooltip 顯示正確 |
| controls.js | 手動測試：切地區/航線/航班/範圍 → 圖表更新；離線切未載入航線 → hint 出現 |
| 整體流程 | E2E 測試（如有）：init → 首載 → 同步 → 離線 → 重連 全流程 |

---

## 8. 預期成果

| 指標 | 現在（app.js） | 拆分後 |
|------|---------------|--------|
| 最大檔案行數 | 1714 | ~600（app.js） |
| 單一職責覆蓋 | 全部混在一起 | 每個 .js 一個明確職責 |
| 可測試性 | 低（全域耦合） | 中（子模組可獨立測試渲染） |
| 改一個功能的影響範圍 | 整個 app.js | 僅對應 .js |
