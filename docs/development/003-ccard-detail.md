# ccard-detail — 開發規格

> **技術棧**：HTML5 + CSS3 + ES2020+（純靜態，無框架、無 bundler）
> **Tech Decision**：`docs/tech-decisions/票價趨勢圖-2026-08-14.md`
> **UI/UX 設計**：`docs/uiux/003-ccard-detail-design.md`
> **BDD**：`docs/bdds/003-ccard-detail.feature`
> **狀態**：設計完成，待開發

---

## 概述

點擊「每週票價變動」卡片（`.ccard`）後彈出詳情 modal，包含航班價格比較表格與歷史價格走勢迷你圖。核心組件：

1. **Detail Modal**：全屏 overlay + 內容面板（header / section 1 / section 2 / footer）
2. **航班價格比較表格**：多航班動態渲染、價格排序、最低價高亮、售罄/無價格降級
3. **歷史價格走勢 SVG mini chart**：從 `CACHE.units` 中同一 `outbound_date` 的不同抓取日期聚合折線圖
4. **RWD 適配**：桌面 modal、平板 90vw、mobile bottom sheet

---

## 2. 前端實作規格

### 2.1 檔案改動總覽

```
web/
├── index.html          ← 修改：新增 #detailModal 容器（與 .page 同層）
├── app.js              ← 修改：新增 detailModal 模組（open/close/渲染邏輯）+ ccard click handler
├── styles.css          ← 修改：新增 modal、bottom-sheet、detail table、detail chart 樣式
├── cache.js            ← 不動
└── aggregate.js        ← 不動
```

### 2.2 DOM 結構 — `#detailModal`（新增於 `index.html`）

插入位置：`</body>` 前、`.page` 之外（與 `<script>` 同層），確保 overlay 覆蓋全頁。

```html
<div id="detailModal" class="detail-overlay" role="dialog" aria-modal="true" aria-label="詳情面板" hidden>
  <div class="detail-backdrop" tabindex="-1"></div>
  <div class="detail-panel" role="document">
    <!-- Header -->
    <div class="detail-header">
      <div class="detail-title" id="detailTitle"></div>
      <button class="detail-close" id="detailClose" type="button" aria-label="關閉詳情">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
    <!-- Section 1: 航班價格比較 -->
    <div class="detail-section" id="detailFlightSection">
      <h2 class="detail-section-title">航班價格比較</h2>
      <div id="detailFlightBody"></div>
    </div>
    <!-- Section 2: 歷史價格走勢 -->
    <div class="detail-section" id="detailHistorySection">
      <h2 class="detail-section-title">歷史價格走勢</h2>
      <div id="detailChartWrap" class="detail-chart-wrap">
        <svg id="detailChart" viewBox="0 0 480 160" role="img" aria-label="歷史價格走勢圖"></svg>
      </div>
      <div id="detailStats" class="detail-stats"></div>
    </div>
    <!-- Footer: 資料說明 -->
    <div class="detail-footer" id="detailFooter"></div>
    <!-- 載入中 / 錯誤 -->
    <div id="detailSkeleton" class="detail-skeleton" hidden aria-hidden="true"></div>
    <div id="detailError" class="detail-error" hidden>
      <div class="detail-error-text">資料載入失敗</div>
      <button class="btn detail-retry" id="detailRetry" type="button">重試</button>
    </div>
  </div>
</div>
```

### 2.3 Detail Modal 模組 — `app.js` 新增

在 `app.js` 的 IIFE 內、`init()` 之前新增 detailModal 模組。以下為關鍵 function 簽名與邏輯骨架。

```javascript
// ═══════════════ 詳情 Modal（003-ccard-detail） ═══════════════

const detailOverlay = $('detailModal');
const detailPanel = detailOverlay.querySelector('.detail-panel');
const detailBackdrop = detailOverlay.querySelector('.detail-backdrop');
const detailTitle = $('detailTitle');
const detailClose = $('detailClose');
const detailFlightBody = $('detailFlightBody');
const detailChartWrap = $('detailChartWrap');
const detailChart = $('detailChart');
const detailStats = $('detailStats');
const detailFooter = $('detailFooter');
const detailSkeleton = $('detailSkeleton');
const detailError = $('detailError');
const detailRetry = $('detailRetry');

let lastTriggerCcard = null;   // 記住觸發的 ccard DOM 節點，關閉後 focus 回去
let currentDetailWeek = null;  // 目前開啟的 week 物件

/**
 * 從 CACHE.units 中聚合指定 outbound_date 的歷史走勢資料。
 * @param {string} outboundDate - 出發日期 'YYYY-MM-DD'
 * @param {string} routeId - 航線 id（如 'TPE-NRT'）
 * @returns {Array<{scrapedAt: string, price: number|null}>} 依 scraped_at 排序
 *
 * 邏輯：
 * 1. 從 CACHE.units 中找出所有包含 '/' + routeId + '/' 的 URL
 * 2. 從 URL 檔名解析 outbound_date（與 datesFromUrl 相同邏輯）
 * 3. 匹配 outboundDate 的 unit
 * 4. 遍歷該 unit.json.flights[]，收集所有 history[] 的 { scraped_at, price_total, status }
 * 5. 以 scraped_at 分組，每組取所有 Available 航班的最低價
 * 6. 依 scraped_at 排序回傳
 */
function aggregateHistory(outboundDate, routeId) {
  if (!CACHE || !CACHE.units) return [];
  const pat = '/' + routeId + '/';
  for (const [url, rec] of Object.entries(CACHE.units)) {
    if (!url.includes(pat)) continue;
    // 從 URL 檔名解析 outbound_date
    const { d } = datesFromUrl(url);
    if (d !== outboundDate) continue;
    // 找到匹配的 unit，聚合 history
    const json = rec.json;
    if (!json || !Array.isArray(json.flights)) return [];
    // 收集所有 scraped_at 日期
    const scrapedDates = new Set();
    for (const fl of json.flights) {
      if (!Array.isArray(fl.history)) continue;
      for (const h of fl.history) {
        if (h.scraped_at) scrapedDates.add(h.scraped_at.split('T')[0]);
      }
    }
    // 每個 scraped_at 取最低價
    const result = [];
    for (const sd of Array.from(scrapedDates).sort()) {
      let minPrice = null;
      for (const fl of json.flights) {
        if (!Array.isArray(fl.history)) continue;
        const entry = fl.history.find(h => h.scraped_at && h.scraped_at.startsWith(sd));
        if (entry && entry.status === 'Available' && typeof entry.price_total === 'number') {
          if (minPrice === null || entry.price_total < minPrice) {
            minPrice = entry.price_total;
          }
        }
      }
      result.push({ scrapedAt: sd, price: minPrice });
    }
    return result;
  }
  return [];
}

/**
 * 開啟詳情 modal。
 * @param {object} week - aggregateWeek 產出的 Week 物件
 * @param {HTMLElement} triggerCcard - 觸發的 .ccard DOM 節點
 */
function openDetail(week, triggerCcard) {
  currentDetailWeek = week;
  lastTriggerCcard = triggerCcard;
  // 顯示 skeleton（先清空再開）
  detailFlightBody.innerHTML = '';
  detailChart.innerHTML = '';
  detailStats.innerHTML = '';
  detailFooter.innerHTML = '';
  detailError.hidden = true;
  detailSkeleton.hidden = false;
  // Header: 出發日期 → 回程日期
  detailTitle.textContent = fmtD(week.d) + ' 出發 → ' + fmtD(week.r) + ' 回程';
  // 顯示 overlay
  detailOverlay.hidden = false;
  document.body.style.overflow = 'hidden'; // 鎖 body 捲動
  // Focus 移入 modal
  detailClose.focus();
  // 異步渲染（模擬資料載入；實際資料已快取，但保留 skeleton 動畫語意）
  requestAnimationFrame(() => {
    renderDetailFlight(week);
    renderDetailChart(week);
    renderDetailFooter(week);
    detailSkeleton.hidden = true;
  });
}

/**
 * 關閉詳情 modal，focus 回觸發 ccard。
 */
function closeDetail() {
  detailOverlay.hidden = true;
  document.body.style.overflow = '';
  currentDetailWeek = null;
  if (lastTriggerCcard) {
    lastTriggerCcard.focus();
    lastTriggerCcard = null;
  }
}

// 關閉觸發
detailClose.addEventListener('click', closeDetail);
detailBackdrop.addEventListener('click', closeDetail);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !detailOverlay.hidden) closeDetail();
});
// Focus trap（簡化版）：Tab 只在 modal 內循環
detailOverlay.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  const focusable = detailPanel.querySelectorAll(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  );
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
});

/**
 * Section 1: 渲染航班價格比較表格。
 * - 多航班（≥2）→ <table> 語意標記，依價格升序排序
 * - 單航班（=1）→ 單行卡片（非表格）
 * - 售罄 → 灰色 + 「已售罄」
 * - 無價格 → 「暫無報價」
 */
function renderDetailFlight(week) {
  const flightEntries = Object.entries(week.f || {}).map(([no, price]) => ({
    no,
    price,
    status: week.fc && week.fc[no] ? week.fc[no] : null,
  }));
  // 加入售罄航班（price === null 但航班存在於 trip JSON）
  // 從 CACHE.units 取原始航班列表（含售罄）
  const rawFlights = getRawFlightsForWeek(week);
  const allFlights = rawFlights.map(fl => {
    const no = fl.outbound_flight_no;
    const { price, status } = latestPrice(fl);
    return {
      no,
      price,
      status,
      depTime: fl.outbound_departure_time || null,
      arrTime: fl.outbound_arrival_time || null,
    };
  });

  if (allFlights.length === 0) {
    detailFlightBody.innerHTML = '<div class="detail-empty">無航班資料</div>';
    return;
  }

  const avg = globalAverage(routeCache.get(state.route) || []);

  if (allFlights.length === 1) {
    // 單航班 → 單行卡片
    const fl = allFlights[0];
    const card = document.createElement('div');
    card.className = 'detail-flight-card';
    card.innerHTML = buildSingleFlightHTML(fl, avg);
    detailFlightBody.appendChild(card);
    return;
  }

  // 多航班 → 表格
  // 依價格升序排序（null 排最後）
  allFlights.sort((a, b) => {
    if (a.price === null && b.price === null) return 0;
    if (a.price === null) return 1;
    if (b.price === null) return -1;
    return a.price - b.price;
  });

  const minPrice = allFlights.find(f => f.price !== null)?.price ?? null;
  const table = document.createElement('table');
  table.className = 'detail-flight-table';
  // thead
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>航班</th><th>時間</th><th>價格</th><th>比平均</th></tr>';
  table.appendChild(thead);
  // tbody
  const tbody = document.createElement('tbody');
  for (const fl of allFlights) {
    const tr = document.createElement('tr');
    const isMin = fl.price !== null && fl.price === minPrice;
    const isSoldOut = fl.status !== 'Available' || fl.price === null;
    if (isMin) tr.classList.add('row-min');
    if (isSoldOut) tr.classList.add('row-soldout');

    let priceHTML;
    if (isSoldOut) {
      priceHTML = '<span class="detail-soldout">已售罄</span>';
    } else if (fl.price === null) {
      priceHTML = '<span class="detail-no-price">暫無報價</span>';
    } else {
      priceHTML = fmt(fl.price);
    }

    let diffHTML = '—';
    if (fl.price !== null && avg !== null) {
      const diff = diffPct(fl.price, avg);
      const cls = diff <= 0 ? 'diff-low' : 'diff-high';
      const arrow = diff < 0 ? '↓' : '↑';
      diffHTML = '<span class="' + cls + '">' + arrow + ' ' + Math.abs(diff) + '%</span>';
    }

    const timeText = (fl.depTime || '—') + ' → ' + (fl.arrTime || '—');

    tr.innerHTML =
      '<td class="detail-fl-no">' + esc(fl.no) + (isMin ? ' <span class="detail-min-badge">最低</span>' : '') + '</td>' +
      '<td class="detail-fl-time">' + esc(timeText) + '</td>' +
      '<td class="detail-fl-price">' + priceHTML + '</td>' +
      '<td class="detail-fl-diff">' + diffHTML + '</td>';
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  detailFlightBody.appendChild(table);

  // 最低價摘要
  if (minPrice !== null) {
    const minFl = allFlights.find(f => f.price === minPrice);
    const summary = document.createElement('div');
    summary.className = 'detail-flight-summary';
    summary.textContent = '最低價：' + minFl.no + ' ' + fmt(minPrice);
    detailFlightBody.appendChild(summary);
  }
}

/**
 * 從 CACHE.units 取得該 week 的原始航班列表（含售罄）。
 * @param {object} week
 * @returns {Array} flights array from trip JSON
 */
function getRawFlightsForWeek(week) {
  if (!CACHE || !CACHE.units || !week.d) return [];
  const pat = '/' + state.route + '/';
  for (const [url, rec] of Object.entries(CACHE.units)) {
    if (!url.includes(pat)) continue;
    const { d } = datesFromUrl(url);
    if (d !== week.d) continue;
    const json = rec.json;
    if (json && Array.isArray(json.flights)) return json.flights;
  }
  return [];
}

/**
 * 單航班卡片 HTML（無表格時使用）。
 */
function buildSingleFlightHTML(fl, avg) {
  const isSoldOut = fl.status !== 'Available' || fl.price === null;
  let priceHTML;
  if (isSoldOut) {
    priceHTML = '<span class="detail-soldout">已售罄</span>';
  } else if (fl.price === null) {
    priceHTML = '<span class="detail-no-price">暫無報價</span>';
  } else {
    priceHTML = fmt(fl.price);
  }

  let diffHTML = '';
  if (fl.price !== null && avg !== null) {
    const diff = diffPct(fl.price, avg);
    const cls = diff <= 0 ? 'diff-low' : 'diff-high';
    const arrow = diff < 0 ? '↓' : '↑';
    diffHTML = '<div class="' + cls + '">' + arrow + ' 比平均' + (diff <= 0 ? '便宜' : '貴') + ' ' + Math.abs(diff) + '%</div>';
  }

  return '<div class="detail-flight-card-no">' + esc(fl.no) + '</div>' +
    '<div class="detail-flight-card-time">' + esc((fl.depTime || '—') + ' → ' + (fl.arrTime || '—')) + '</div>' +
    '<div class="detail-flight-card-price">' + priceHTML + '</div>' +
    diffHTML;
}

/**
 * Section 2: 渲染歷史價格走勢 SVG mini chart。
 * - ≥2 次抓取 → 折線圖（X = 抓取日期, Y = 最低價）
 * - =1 次抓取 → 單點 + 提示文字
 * - 所有價格相同 → 水平線 + 「價格穩定」
 * - 最低點綠色圓點標記，最新點藍色圓點標記
 */
function renderDetailChart(week) {
  const history = aggregateHistory(week.d, state.route);
  detailChart.innerHTML = '';

  if (history.length === 0) {
    detailChartWrap.hidden = true;
    return;
  }
  detailChartWrap.hidden = false;

  const validHistory = history.filter(h => h.price !== null);

  if (validHistory.length === 0) {
    // 全部無價格
    detailChartWrap.hidden = true;
    return;
  }

  if (validHistory.length === 1) {
    // 單點
    renderDetailChartSinglePoint(validHistory[0], week);
    return;
  }

  // ≥2 點 → 折線圖
  renderDetailChartLine(validHistory, week);
}

/**
 * 單點渲染（僅 1 次抓取）。
 */
function renderDetailChartSinglePoint(point, week) {
  const W = 480, H = 160;
  const cx = W / 2, cy = H / 2;
  detailChart.appendChild(svgEl('circle', { cx, cy, r: 6, fill: 'var(--accent)' }));
  const label = svgEl('text', { x: cx, y: cy - 14, 'text-anchor': 'middle', class: 'detail-chart-label' });
  label.textContent = fmt(point.price);
  detailChart.appendChild(label);
  // 提示文字
  detailStats.innerHTML = '<div class="detail-single-hint">僅 1 個資料點，尚無走勢可比較</div>';
  detailStats.innerHTML += '<div class="detail-stats-row">資料日期：' + esc(point.scrapedAt) + '</div>';
}

/**
 * 折線圖渲染（≥2 次抓取）。
 * - 使用 inline SVG，寬度 100%、高度 160px（viewBox 0 0 480 160）
 * - X 軸：抓取日期（間隔標記）
 * - Y 軸：價格（動態範圍）
 * - 折線 + 最低點綠色標記 + 最新點藍色標記
 */
function renderDetailChartLine(history, week) {
  const W = 480, H = 160;
  const M = { l: 50, r: 16, t: 20, b: 30 };
  const n = history.length;

  const prices = history.map(h => h.price);
  const dataMin = Math.min(...prices);
  const dataMax = Math.max(...prices);
  const PAD = Math.max(500, (dataMax - dataMin) * 0.1);
  const yMin = Math.max(0, dataMin - PAD);
  const yMax = dataMax + PAD;

  const X = i => M.l + i * (W - M.l - M.r) / Math.max(n - 1, 1);
  const Y = v => H - M.b - (v - yMin) / (yMax - yMin) * (H - M.t - M.b);

  // Y 軸網格
  const range = yMax - yMin;
  const step = range <= 4000 ? 1000 : range <= 8000 ? 2000 : range <= 16000 ? 4000 : 6000;
  for (let v = Math.ceil(yMin / step) * step; v <= yMax; v += step) {
    detailChart.appendChild(svgEl('line', {
      x1: M.l, y1: Y(v), x2: W - M.r, y2: Y(v),
      stroke: 'var(--border)', 'stroke-width': 0.5, 'stroke-dasharray': '2,2',
    }));
    const t = svgEl('text', {
      x: M.l - 4, y: Y(v) + 4,
      'text-anchor': 'end', fill: 'var(--muted)', 'font-size': '9px', 'font-family': 'var(--mono)',
    });
    t.textContent = (v / 1000).toFixed(v % 1000 === 0 ? 0 : 1) + 'K';
    detailChart.appendChild(t);
  }

  // X 軸日期標記（每隔幾個標一個，避免重疊）
  const stepX = Math.max(1, Math.floor(n / 5));
  for (let i = 0; i < n; i += stepX) {
    const t = svgEl('text', {
      x: X(i), y: H - 4,
      'text-anchor': 'middle', fill: 'var(--muted)', 'font-size': '8px', 'font-family': 'var(--mono)',
    });
    t.textContent = history[i].scrapedAt.slice(5); // MM-DD
    detailChart.appendChild(t);
  }

  // 折線
  let d = '';
  history.forEach((h, i) => {
    d += (i === 0 ? 'M' : ' L') + X(i) + ' ' + Y(h.price);
  });
  detailChart.appendChild(svgEl('path', {
    d, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2, 'stroke-linejoin': 'round',
  }));

  // 檢查是否所有價格相同（水平線）
  const allSame = prices.every(p => p === prices[0]);

  // 找最低點與最新點
  let minIdx = 0;
  history.forEach((h, i) => { if (h.price < history[minIdx].price) minIdx = i; });
  const latestIdx = history.length - 1;

  // 最低點（綠色）
  detailChart.appendChild(svgEl('circle', {
    cx: X(minIdx), cy: Y(history[minIdx].price), r: 5,
    fill: 'var(--success)', stroke: '#fff', 'stroke-width': 1.5,
  }));
  const minLabel = svgEl('text', {
    x: X(minIdx), y: Y(history[minIdx].price) - 10,
    'text-anchor': 'middle', fill: 'var(--success)', 'font-size': '9px', 'font-weight': '600', 'font-family': 'var(--mono)',
  });
  minLabel.textContent = fmt(history[minIdx].price);
  detailChart.appendChild(minLabel);

  // 最新點（藍色；若與最低點重合則不重複標記）
  if (latestIdx !== minIdx) {
    detailChart.appendChild(svgEl('circle', {
      cx: X(latestIdx), cy: Y(history[latestIdx].price), r: 5,
      fill: 'var(--accent)', stroke: '#fff', 'stroke-width': 1.5,
    }));
  }

  // 統計摘要
  const allPrices = history.map(h => h.price);
  const lowest = Math.min(...allPrices);
  const highest = Math.max(...allPrices);
  const lowestDate = history[allPrices.indexOf(lowest)].scrapedAt;
  const highestDate = history[allPrices.indexOf(highest)].scrapedAt;
  const dropPct = highest > 0 ? Math.round((highest - lowest) / highest * 100) : 0;

  let statsHTML = '';
  if (allSame) {
    statsHTML = '<div class="detail-stats-row">價格穩定</div>';
  } else {
    statsHTML =
      '<div class="detail-stats-row">最低 ' + fmt(lowest) + '（' + esc(lowestDate) + '）</div>' +
      '<div class="detail-stats-row">最高 ' + fmt(highest) + '（' + esc(highestDate) + '）</div>' +
      '<div class="detail-stats-row">降幅 <span class="diff-low">' + dropPct + '% ↓</span></div>';
  }
  detailStats.innerHTML = statsHTML;
}

/**
 * Section 3: 渲染 footer（抓取次數與日期範圍）。
 */
function renderDetailFooter(week) {
  const history = aggregateHistory(week.d, state.route);
  if (history.length === 0) {
    detailFooter.hidden = true;
    return;
  }
  detailFooter.hidden = false;
  const dates = history.map(h => h.scrapedAt).sort();
  const first = dates[0];
  const last = dates[dates.length - 1];
  detailFooter.textContent = '資料來自 ' + history.length + ' 次抓取（' + first + ' ~ ' + last + '）';
}

// 重試按鈕（資料載入失敗時）
detailRetry.addEventListener('click', () => {
  if (currentDetailWeek) openDetail(currentDetailWeek, lastTriggerCcard);
});
```

### 2.4 ccard Click Handler — 修改 `renderChangeCards()`

在 `renderChangeCards()` 函式中，為每個 `.ccard` 綁定 click 與 keydown 事件。

```javascript
// 在 renderChangeCards() 的 for 迴圈末尾，changeTableEl.innerHTML = html; 之後：
function bindCcardClicks() {
  const ccards = changeTableEl.querySelectorAll('.ccard');
  ccards.forEach((card, idx) => {
    const week = visible[idx]; // visible 陣列與 DOM 順序一致
    if (!week) return;

    // 點擊開啟詳情
    card.addEventListener('click', () => openDetail(week, card));
    card.style.cursor = 'pointer';

    // Enter/Space 開啟詳情（鍵盤無障礙）
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openDetail(week, card);
      }
    });
  });
}
```

> **注意**：`visible` 陣列需從 `buildChart()` 傳入或在 `renderChangeCards()` 內部重新計算。建議將 `visible` 存為模組級變數（`let currentVisible = []`），在 `buildChart()` 中賦值，`renderChangeCards()` 與 `bindCcardClicks()` 共用。

### 2.5 資料流摘要

```
ccard click
  → openDetail(week, triggerCcard)
    → aggregateHistory(week.d, routeId)  // 從 CACHE.units 聚合歷史走勢
    → getRawFlightsForWeek(week)          // 從 CACHE.units 取原始航班列表
    → renderDetailFlight(week)            // Section 1: 航班比較表格
    → renderDetailChart(week)             // Section 2: 歷史走勢 mini chart
    → renderDetailFooter(week)            // Footer: 抓取次數與日期範圍
```

---

## 6. 邊界條件處理

| 情境 | 來源 | 處理方式 |
|------|------|---------|
| 僅 1 個航班 | BDD `@flight-comparison @p1` | 單行卡片顯示（非表格），含航班號、時間、價格、比平均差異 |
| 售罄航班 | BDD `@flight-comparison @p1` | 航班列表仍可見（灰色 `.row-soldout`），價格欄顯示「已售罄」 |
| 無價格資料 | BDD `@flight-comparison @p2` | 價格欄顯示「暫無報價」 |
| 僅 1 次抓取 | BDD `@history-trend @p1` | 圖表顯示單點（無折線），下方提示「僅 1 個資料點，尚無走勢可比較」 |
| 僅 2 次抓取 | BDD `@history-trend @p2` | 兩點連線，仍顯示完整圖表 |
| 所有抓取價格相同 | BDD `@history-trend @p2` | 水平折線 + 統計摘要顯示「價格穩定」 |
| 無航班資料 | 設計 §4 狀態矩陣 | Section 1 顯示「無航班資料」 |
| 無歷史走勢資料 | 設計 §4 狀態矩陣 | Section 2 隱藏（`detailChartWrap.hidden = true`） |
| 資料尚未載入完成 | BDD `@loading @p1` | 顯示骨架屏（`.detail-skeleton`）佔位 |
| 資料載入失敗 | BDD `@error @p1` | 顯示錯誤提示 + 重試按鈕（`.detail-error`） |
| 點擊重試重新載入 | BDD `@error @p2` | `openDetail()` 重新執行（資料已快取，實際為重新渲染） |
| Mobile bottom sheet | BDD `@rwd @p0/@p1` | ≤767px 從底部滑入，圓角 16px，全寬 |
| Focus 管理 | BDD `@a11y @p0` | 開啟時 focus 移入 modal（`detailClose.focus()`），關閉時回到觸發 ccard |
| `prefers-reduced-motion` | 設計 §7 實作建議 #4 | `reduce` 時取消滑入動畫（CSS `@media (prefers-reduced-motion: reduce)`） |

---

## 7. CSS 關鍵樣式

| class | 樣式重點 |
|-------|---------|
| `.detail-overlay` | `position: fixed; inset: 0; z-index: 100; display: flex; align-items: center; justify-content: center;`；mobile ≤767px 時 `align-items: flex-end` |
| `.detail-overlay[hidden]` | `display: none !important` |
| `.detail-backdrop` | `position: absolute; inset: 0; background: rgba(0,0,0,0.4);` |
| `.detail-panel` | `position: relative; background: var(--surface); border-radius: 12px; box-shadow: var(--shadow-lg); max-height: 85vh; overflow-y: auto; width: 560px; max-width: 90vw;`；mobile ≤767px：`width: 100%; border-radius: 16px 16px 0 0; max-height: 90vh; animation: slideUp 0.3s ease` |
| `.detail-header` | `display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.25rem; border-bottom: 1px solid var(--border);` |
| `.detail-title` | `font-weight: 700; font-size: 1rem;` |
| `.detail-close` | `width: 44px; height: 44px; border-radius: 50%; border: none; background: none; cursor: pointer; display: flex; align-items: center; justify-content: center;`；hover 時 `background: var(--surface-2)` |
| `.detail-section` | `padding: 1rem 1.25rem;` |
| `.detail-section-title` | `font-size: 0.85rem; font-weight: 600; color: var(--muted); margin: 0 0 0.75rem;` |
| `.detail-flight-table` | `width: 100%; border-collapse: collapse; font-size: 0.85rem;` |
| `.detail-flight-table th` | `text-align: left; padding: 0.5rem 0.6rem; border-bottom: 2px solid var(--border); font-weight: 600; color: var(--muted); font-size: 0.75rem;` |
| `.detail-flight-table td` | `padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border);` |
| `.detail-flight-table .row-min` | `background: var(--success-light);` |
| `.detail-flight-table .row-soldout` | `opacity: 0.5;` |
| `.detail-min-badge` | `background: var(--success); color: #fff; font-size: 0.65rem; padding: 0.15rem 0.4rem; border-radius: 10px; margin-left: 0.3rem;` |
| `.detail-soldout` | `color: var(--muted); font-style: italic;` |
| `.detail-no-price` | `color: var(--muted);` |
| `.diff-low` | `color: var(--success); font-weight: 600;` |
| `.diff-high` | `color: var(--danger); font-weight: 600;` |
| `.detail-flight-card` | `border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem 1rem;`（單航班降級） |
| `.detail-flight-summary` | `margin-top: 0.5rem; font-size: 0.82rem; color: var(--muted);` |
| `.detail-chart-wrap` | `overflow-x: auto; -webkit-overflow-scrolling: touch;` |
| `.detail-chart-wrap svg` | `width: 100%; height: 160px;` |
| `.detail-stats` | `padding: 0.5rem 0; font-size: 0.82rem;` |
| `.detail-stats-row` | `margin-bottom: 0.3rem;` |
| `.detail-single-hint` | `color: var(--muted); font-style: italic; font-size: 0.8rem; margin-bottom: 0.3rem;` |
| `.detail-footer` | `padding: 0.75rem 1.25rem; border-top: 1px solid var(--border); font-size: 0.78rem; color: var(--muted);` |
| `.detail-skeleton` | `padding: 2rem;`；內部骨架以 `.skeleton-line` 動畫脈衝（複用既有 `.skeleton` 樣式） |
| `.detail-error` | `padding: 1.5rem; text-align: center;` |
| `.detail-retry` | 複用既有 `.btn` 樣式 |

**RWD 斷點**：

```css
/* ≥1024px */
.detail-panel { width: 560px; }

/* 768–1023px */
@media (max-width: 1023px) and (min-width: 768px) {
  .detail-panel { width: 90vw; max-width: 480px; }
}

/* ≤767px: bottom sheet */
@media (max-width: 767px) {
  .detail-overlay { align-items: flex-end; }
  .detail-panel {
    width: 100%;
    border-radius: 16px 16px 0 0;
    max-height: 90vh;
    animation: slideUp 0.3s ease;
  }
}

/* 動畫取消 */
@media (prefers-reduced-motion: reduce) {
  .detail-panel { animation: none; }
}
```

**深色模式**：全部使用 CSS 變數（`var(--surface)`, `var(--border)`, `var(--text)` 等），自動跟隨既有 `@media (prefers-color-scheme: dark)` 切換。

---

## 8. 開發順序

| 步驟 | 內容 | 依賴 |
|------|------|------|
| 1 | `index.html` 新增 `#detailModal` DOM 結構 | — |
| 2 | `styles.css` 新增 modal / bottom-sheet / detail-table / detail-chart 樣式 | #1 |
| 3 | `app.js` 新增 `aggregateHistory()` 從 `CACHE.units` 聚合歷史走勢資料 | — |
| 4 | `app.js` 新增 `renderDetailFlight()` 航班價格比較渲染 | #3 |
| 5 | `app.js` 新增 `renderDetailChart()` 歷史走勢 SVG mini chart（含 `renderDetailChartSinglePoint` / `renderDetailChartLine`） | #3 |
| 6 | `app.js` 新增 `renderDetailFooter()` footer 資料說明 | #3 |
| 7 | `app.js` 新增 `openDetail()` / `closeDetail()` modal 開關與 focus 管理 | #1, #4, #5, #6 |
| 8 | `app.js` 修改 `renderChangeCards()` 結尾新增 `bindCcardClicks()` 綁定 click/keydown handler | #7 |
| 9 | `app.js` 新增 focus trap / ESC 關閉 / backdrop 點擊關閉邏輯 | #7 |
| 10 | 端對端驗證：所有 BDD Scenario 走查 + `prefers-reduced-motion` + Mobile bottom sheet | #1~#9 |

**DAG 依賴圖**：

```
#1 DOM ─┬─→ #2 CSS ──────────────────────┐
        │                                 │
#3 aggregate ─┬─→ #4 flight ──┐           │
              ├─→ #5 chart ───┤           │
              └─→ #6 footer ──┴─→ #7 open/close ─→ #8 bind clicks ─→ #9 focus trap ─→ #10 E2E
```

---

## BDD Scenario 對照表

| BDD Scenario | 對應規格位置 |
|-------------|-------------|
| 點擊 ccard 開啟詳情 modal | §2.4 `openDetail()` + §2.8 步驟 #8 |
| 按 ESC 關閉詳情 modal | §2.3 `keydown` listener |
| 點擊 backdrop 關閉詳情 modal | §2.3 `detailBackdrop.addEventListener('click', closeDetail)` |
| 關閉 modal 後 focus 回到觸發 ccard | §2.3 `closeDetail()` 中 `lastTriggerCcard.focus()` |
| 顯示多航班價格比較表格 | §2.3 `renderDetailFlight()` 多航班分支 |
| 航班依價格由低到高排序 | §2.3 `allFlights.sort()` 升序排序 |
| 單一航班降級為單行顯示 | §2.3 `renderDetailFlight()` `allFlights.length === 1` 分支 |
| 售罄航班顯示已售罄 | §2.3 `isSoldOut` 判定 + `.row-soldout` class |
| 無價格時顯示暫無報價 | §2.3 `fl.price === null` 分支 |
| 顯示歷史價格折線圖 | §2.3 `renderDetailChartLine()` |
| 顯示歷史價格統計摘要 | §2.3 統計摘要 HTML（最低/最高/降幅） |
| 單次抓取顯示單點與提示 | §2.3 `renderDetailChartSinglePoint()` |
| 僅 2 次抓取仍顯示折線圖 | §2.3 `renderDetailChartLine()` `n >= 2` |
| 所有抓取價格相同顯示穩定 | §2.3 `allSame` 判定 + 「價格穩定」文字 |
| 顯示抓取次數與日期範圍 | §2.3 `renderDetailFooter()` |
| 不同斷點的 modal 寬度 | §7 CSS 斷點（≥1024px / 768–1023px / ≤767px） |
| Mobile 使用 bottom sheet 樣式 | §7 `@media (max-width: 67px)` + `slideUp` 動畫 |
| modal 開啟時 focus 管理 | §2.3 `detailClose.focus()` + `aria-modal="true"` |
| 關閉按鈕具有 aria-label | §2.2 HTML `aria-label="關閉詳情"` |
| 表格使用語意化標記 | §2.3 `renderDetailFlight()` 使用 `<table>` / `<th>` / `<td>` |
| 圖表具有無障礙描述 | §2.2 HTML `role="img"` + `aria-label` |
| 漲跌使用多重管道傳達 | §2.3 `diffHTML` 同時使用箭頭符號（↑↓）與百分比文字 |
| 載入中顯示骨架屏 | §2.3 `openDetail()` 中 `detailSkeleton.hidden = false` |
| 載入失敗顯示錯誤提示 | §2.3 `detailError` 顯示 + 重試按鈕 |
| 點擊重試重新載入 | §2.3 `detailRetry.addEventListener('click', ...)` |

## §8 驗收清單對照（設計文件）

| 驗收項目 | 對應規格位置 |
|---------|-------------|
| 點擊 ccard 開啟詳情 modal | §2.4 `openDetail()` |
| ESC / 點 backdrop 關閉 | §2.3 `keydown` + `detailBackdrop` listener |
| 航班比較表格正確顯示（含最低價高亮） | §2.3 `renderDetailFlight()` `.row-min` + `.detail-min-badge` |
| 歷史走勢圖正確繪製 | §2.3 `renderDetailChartLine()` |
| 僅 1 航班時降級為單行卡片 | §2.3 `allFlights.length === 1` 分支 |
| 僅 1 次抓取時顯示單點 + 提示 | §2.3 `renderDetailChartSinglePoint()` |
| 售罄狀態正確顯示 | §2.3 `isSoldOut` + `.row-soldout` + 「已售罄」 |
| Mobile bottom sheet 行為正確 | §7 `@media (max-width: 67px)` |
| 無障礙：focus 管理、aria 標記 | §2.2 HTML aria 屬性 + §2.3 `closeDetail()` focus 回歸 |
| Console 無 error | §8 步驟 #10 E2E 驗證 |
