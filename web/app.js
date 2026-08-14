/**
 * 票價趨勢圖 — 主程式（資料層 + 圖表層 + 互動層 + 狀態處理）
 *
 * 依賴：web/aggregate.js（全域 PriceAgg，純函式）
 * 對照：docs/development/票價趨勢圖.md §2.4~§2.8、§4、§6
 */
(function () {
  'use strict';

  const {
    CONFIG,
    aggregateWeekly, globalAverage, diffPct, filterRange,
    minMark, detectPeak, isStale, originAllowed, formatGeneratedAt, summaryData,
    hasAnyPrice,
  } = window.PriceAgg;

  // ═══════════════ DOM refs ═══════════════
  const $ = id => document.getElementById(id);
  const routeTabs = $('routeTabs'), flightSel = $('flightSel'), rangeSeg = $('rangeSeg');
  const toolbar = $('toolbar'), progress = $('progress');
  const chart = $('chart'), chartWrap = $('chartWrap'), tip = $('tip'), chartTitle = $('chartTitle');
  const skeleton = $('skeleton'), errBox = $('errBox'), errDetail = $('errDetail'), retryBtn = $('retryBtn');
  const emptyBox = $('emptyBox'), staleBar = $('staleBar'), staleText = $('staleText'), updText = $('updText');
  const sumMin = $('sumMin'), sumMinS = $('sumMinS'), sumAvg = $('sumAvg'), sumPeak = $('sumPeak'), sumPeakS = $('sumPeakS');
  const summary = $('summary'); // 三卡容器（無資料時整區隱藏）

  // ⚠️ SVG 元素的 `.hidden = true` 只改 IDL property、不會反映成 hidden attribute（Chromium 怪癖），
  // 而 CSS `[hidden] { display:none }` 依賴 attribute → 圖表會"看似隱藏其實仍顯示"。
  // 統一用 setAttribute/removeAttribute 控制 #chart 顯示狀態。
  const setChartHidden = hidden => {
    if (hidden) chart.setAttribute('hidden', '');
    else chart.removeAttribute('hidden');
  };

  // ═══════════════ 應用狀態（單一來源） ═══════════════
  const state = {
    route: CONFIG.ROUTES[0].id,   // 預設東京 TPE-NRT（F-14）
    flight: 'all',                // 'all' = 每週最低價主線；否則航班號（如 'JX 800'）
    range: '3m',                  // 預設 3 個月（與 mockup 一致）
    loading: false,
  };

  let INDEX = null;               // fetchIndex 結果（全域；供 loadRoute 篩 trips）
  let loadToken = 0;              // 競態防護：快速切航線只套用最新請求（F-21）
  const abortCtl = new AbortController(); // 頁面卸載中止 fetch（F-22）
  const routeCache = new Map();   // Map<routeId, Week[]>（F-13）

  // ═══════════════ 資料層（§2.4） ═══════════════
  const API_ROOT = new URL('../', document.baseURI);

  /** 載入 index.json：no-cache 重新驗證 + shape 驗證 */
  async function fetchIndex() {
    let res;
    try {
      res = await fetch(new URL('api/index.json', API_ROOT), { cache: 'no-cache', signal: abortCtl.signal });
    } catch (e) {
      const err = new Error('網路層錯誤');
      err.code = 'ERR_NETWORK';
      throw err;
    }
    if (!res.ok) {
      const err = new Error('index HTTP ' + res.status);
      err.code = 'ERR_INDEX_FETCH';
      throw err;
    }
    let json;
    try {
      json = await res.json();
    } catch (e) {
      const err = new Error('index JSON 解析失敗');
      err.code = 'ERR_INDEX_INVALID';
      throw err;
    }
    if (!json || typeof json.generated_at !== 'string' ||
        !Array.isArray(json.routes) || !Array.isArray(json.trips)) {
      const err = new Error('index shape 驗證失敗');
      err.code = 'ERR_INDEX_INVALID';
      throw err;
    }
    return json;
  }

  /** 並行載入 trips：Promise pool（同時最多 CONCURRENCY），失敗該筆 null 佔位（F-20） */
  async function fetchTrips(urls, onProgress, signal) {
    const results = new Array(urls.length);
    const cache = new Map(); // Map<url, json> 記憶體快取
    let cursor = 0;
    async function worker() {
      while (cursor < urls.length) {
        const i = cursor++;
        const url = urls[i];
        if (cache.has(url)) {
          results[i] = cache.get(url);
        } else {
          try {
            const res = await fetch(new URL(url, API_ROOT), { signal });
            results[i] = res.ok ? await res.json() : null;
          } catch (e) {
            results[i] = null; // 網路錯誤該筆佔位，不拋錯
          }
          cache.set(url, results[i]);
        }
        if (onProgress) onProgress(i + 1, urls.length);
      }
    }
    const workers = Array.from({ length: Math.min(CONFIG.CONCURRENCY, urls.length) }, worker);
    await Promise.all(workers);
    return results;
  }

  /** 載入整條航線（含競態防護與快取） */
  async function loadRoute(routeId, onProgress) {
    if (routeCache.has(routeId)) return routeCache.get(routeId); // F-13 命中快取
    const token = ++loadToken;
    // 依 index.trips 檔名前綴篩該航線 URL（值為 'api/trips/TPE-NRT_...'，檔名前綴在路徑內）
    const prefix = routeId + '_';
    const urls = INDEX.trips.filter(t => t.includes('/' + prefix));
    const tripJsons = await fetchTrips(urls, onProgress, abortCtl.signal);
    if (token !== loadToken) return null; // 過期回應丟棄（F-21）
    const weeks = aggregateWeekly(urls, tripJsons);
    routeCache.set(routeId, weeks);
    return weeks;
  }

  // ═══════════════ 互動層（§2.5） ═══════════════
  function renderRouteTabs() {
    routeTabs.innerHTML = '';
    CONFIG.ROUTES.forEach(r => {
      const b = document.createElement('button');
      b.className = 'rtab' + (r.id === state.route ? ' active' : '');
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', r.id === state.route ? 'true' : 'false');
      b.dataset.route = r.id;
      b.innerHTML = r.name + ' <span class="code">' + r.id + '</span>';
      routeTabs.appendChild(b);
    });
  }

  function renderRangeSeg() {
    rangeSeg.innerHTML = '';
    CONFIG.RANGES.forEach(r => {
      const b = document.createElement('button');
      b.className = r.key === state.range ? 'active' : '';
      b.setAttribute('aria-pressed', r.key === state.range ? 'true' : 'false');
      b.dataset.range = r.key;
      b.textContent = r.label;
      rangeSeg.appendChild(b);
    });
  }

  /** 航班下拉選項 = 該航線所有週 flights 聯集；目前航班不存在 → 回退 all */
  function renderFlightSel(weeks) {
    const flightSet = [];
    for (const w of weeks) {
      for (const no of Object.keys(w.f)) {
        if (!flightSet.includes(no)) flightSet.push(no);
      }
    }
    if (!flightSet.includes(state.flight)) state.flight = 'all'; // 回退（大阪無 JX 800）
    flightSel.innerHTML = '';
    const all = document.createElement('option');
    all.value = 'all';
    all.textContent = '全部（每週最低價）';
    flightSel.appendChild(all);
    flightSet.forEach(no => {
      const o = document.createElement('option');
      o.value = no;
      o.textContent = '航班 ' + no; // DOM API 建構，避免 API 資料注入 HTML（XSS）
      flightSel.appendChild(o);
    });
    flightSel.value = state.flight;
  }

  function setToolbarDisabled(disabled) {
    flightSel.disabled = disabled;
    rangeSeg.querySelectorAll('button').forEach(b => (b.disabled = disabled));
    routeTabs.querySelectorAll('button').forEach(b => (b.disabled = disabled));
    if (disabled) progress.hidden = false; else progress.hidden = true;
  }

  let controlsBound = false;
  function initControls() {
    if (controlsBound) return; // 重試（init 重跑）不重複綁定，避免 listener 堆疊
    controlsBound = true;
    routeTabs.addEventListener('click', async e => {
      const b = e.target.closest('button[data-route]');
      if (!b || b.dataset.route === state.route || state.loading) return;
      state.route = b.dataset.route;
      renderRouteTabs();
      setLoading(true);
      await drawCurrentRoute();
      setLoading(false);
    });
    flightSel.addEventListener('change', () => {
      state.flight = flightSel.value;
      buildChart();
    });
    rangeSeg.addEventListener('click', e => {
      const b = e.target.closest('button[data-range]');
      if (!b) return;
      state.range = b.dataset.range;
      renderRangeSeg();
      buildChart();
    });
    retryBtn.addEventListener('click', init);
    window.addEventListener('beforeunload', () => abortCtl.abort()); // F-22
  }

  // ═══════════════ 圖表層（§2.6，移植 mockup） ═══════════════
  const NS = 'http://www.w3.org/2000/svg';
  const { W, H, M } = CONFIG.SVG;
  const { MIN: YMIN, MAX: YMAX } = CONFIG.Y;
  const fmt = n => 'NT$' + n.toLocaleString('en-US');
  const fmtD = d => d ? d.split('-').slice(1).join('/') : '—';
  // HTML 跳脫（API 資料進入 innerHTML 前必經，防 XSS）
  const esc = s => String(s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const svgEl = (name, attrs) => {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  };
  const rangeLabel = () => (CONFIG.RANGES.find(r => r.key === state.range) || CONFIG.RANGES[3]).label;

  function buildChart() {
    const weeks = routeCache.get(state.route) || [];
    // 航線無任何有效價格資料（trips 全缺 / 全部無效）：一律空狀態，不渲染空網格圖表。
    // 所有進入點（切航線 / 切航班 / 切範圍）都經過這裡，避免任一操作後空圖表復現（F-11 擴充）
    if (!hasAnyPrice(weeks)) {
      setChartHidden(true);
      emptyBox.hidden = false;
      chartTitle.textContent = '';
      chart.removeAttribute('aria-label');
      chart.innerHTML = ''; // 清掉殘留圖形（如切航線前他航線的網格/折線）
      // 無資料：Summary 三卡清空並整區隱藏（避免殘留他航線數字 / 「—」殘影）
      sumMin.textContent = '—'; sumMinS.textContent = '';
      sumAvg.textContent = '—';
      sumPeak.textContent = '—'; sumPeakS.textContent = '';
      summary.hidden = true;
      return;
    }
    const visible = filterRange(weeks, (CONFIG.RANGES.find(r => r.key === state.range) || {}).weeks);
    const avg = globalAverage(weeks); // 全域（不隨範圍漂移，F-06）
    const n = visible.length;
    if (n === 0) return; // 可見範圍無資料（防呆）
    const X = i => M.l + i * (W - M.l - M.r) / Math.max(n - 1, 1); // 單週資料時避免除以零（NaN）
    const Y = v => H - M.b - (v - YMIN) / (YMAX - YMIN) * (H - M.t - M.b);
    const Yclamp = v => Math.max(Y(v), M.t); // E21：出界點 clamp 至圖表上緣

    chart.innerHTML = '';
    const routeInfo = CONFIG.ROUTES.find(r => r.id === state.route) || { name: state.route };

    // Y 軸網格 + 標籤
    for (let v = YMIN; v <= YMAX; v += 6000) {
      chart.appendChild(svgEl('line', { x1: M.l, y1: Y(v), x2: W - M.r, y2: Y(v), 'class': 'grid-major' }));
      const t = svgEl('text', { x: M.l - 8, y: Y(v) + 4, 'class': 'tick-label', 'text-anchor': 'end' });
      t.textContent = (v / 1000) + 'K';
      chart.appendChild(t);
    }
    for (let i = 0; i < n; i += 4) {
      const t = svgEl('text', { x: X(i), y: H - M.b + 16, 'class': 'tick-label', 'text-anchor': 'middle' });
      t.textContent = fmtD(visible[i].d);
      chart.appendChild(t);
    }
    const cap = svgEl('text', { x: W - M.r, y: H - 4, 'class': 'axis-caption', 'text-anchor': 'end' });
    cap.textContent = '出發日期（週六）· 單位 TWD 來回';
    chart.appendChild(cap);

    // 旺季區塊（在可見範圍內定位；含重疊裁切）
    CONFIG.PEAKS.forEach(p => {
      const start = visible.findIndex(w => w.d >= p.from);
      if (start < 0) return;
      if (visible[start].d > p.to) return; // 可見範圍在旺季結束之後 → 無交集，避免單格殘影
      let end = start;
      while (end + 1 < visible.length && visible[end + 1].d <= p.to) end++;
      const x1 = X(start);
      const x2 = X(end) + (X(1) - X(0));
      chart.appendChild(svgEl('rect', { x: x1, y: M.t, width: x2 - x1, height: H - M.t - M.b, 'class': 'peak-rect', rx: 4 }));
      const t = svgEl('text', { x: x1 + 6, y: M.t + 15, 'class': 'peak-label' });
      t.textContent = p.label;
      chart.appendChild(t);
    });

    // 全域平均虛線
    if (avg !== null) {
      chart.appendChild(svgEl('line', { x1: M.l, y1: Y(avg), x2: W - M.r, y2: Y(avg), 'class': 'avg-line' }));
      const al = svgEl('text', { x: W - M.r - 4, y: Y(avg) - 7, 'class': 'avg-label', 'text-anchor': 'end' });
      al.textContent = '平均 ' + fmt(avg);
      chart.appendChild(al);
    }

    // 資料點（null → 斷點；航班模式取 w.f[flight]，undefined → null）
    const pts = visible.map((w, i) => {
      let price = w.min;
      if (state.flight !== 'all') price = (w.f && w.f[state.flight] !== undefined) ? w.f[state.flight] : null;
      return { i, x: X(i), y: price === null ? null : Yclamp(price), price, w };
    });

    // 折線（斷點分段；僅有單點（無 L 段）時不產生退化 path）
    let d = '', seg = false, hasLine = false;
    pts.forEach(p => {
      if (p.y === null) { seg = false; return; }
      if (seg) hasLine = true;
      d += (seg ? ' L' : ' M') + p.x + ' ' + p.y;
      seg = true;
    });
    if (hasLine) chart.appendChild(svgEl('path', { d, 'class': 'price-line' }));

    // 資料點 circle + 售罄/缺資料標示
    pts.forEach(p => {
      if (p.y === null) {
        // 缺資料/售罄週：平均線高度畫空心虛線圈（gap-dot）
        chart.appendChild(svgEl('circle', { cx: p.x, cy: Y(avg ?? YMIN), r: 4, 'class': 'gap-dot', 'data-i': p.i }));
        if (p.w.status === 'sold_out') {
          const t = svgEl('text', { x: p.x, y: H - M.b + 30, 'class': 'sold-out-label', 'text-anchor': 'middle' });
          t.textContent = '售罄';
          chart.appendChild(t);
        }
        return;
      }
      const c = svgEl('circle', { cx: p.x, cy: p.y, r: 4, 'class': 'dot', tabindex: '0', role: 'button' });
      c.setAttribute('data-i', p.i);
      c.setAttribute('aria-label', '出發 ' + p.w.d + '，價格 ' + fmt(p.price));
      chart.appendChild(c);
    });

    // 可見範圍最低價標記（F-05）
    const mark = minMark(visible);
    if (mark && mark.d) {
      const mi = visible.findIndex(w => w === mark);
      if (mi >= 0) {
        const p = pts[mi];
        if (p.y !== null) {
          chart.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: 7, 'class': 'dot min', 'data-i': p.i }));
          const ml = svgEl('text', { x: p.x, y: Math.max(p.y - 13, M.t + 10), 'class': 'min-label', 'text-anchor': 'middle' });
          ml.textContent = '最便宜 ' + fmt(mark.min);
          chart.appendChild(ml);
        }
      }
    }

    // chart-title（BDD 標題格式）
    const flightLabel = state.flight === 'all' ? '每週最低價' : '航班 ' + state.flight;
    chartTitle.innerHTML = '<b>' + esc(routeInfo.name) + ' ' + esc(state.route) + '</b> · ' +
      esc(flightLabel) + ' · 顯示 ' + esc(rangeLabel()) + '（共 ' + n + ' 週）';
    chart.setAttribute('aria-label', '票價趨勢圖：' + routeInfo.name + ' ' + state.route + '，' + flightLabel + '，' + rangeLabel());

    renderSummary(visible, avg);
  }

  // ═══════════════ Tooltip（§2.7） ═══════════════
  function showTip(w, ev) {
    const avg = globalAverage(routeCache.get(state.route) || []);
    let html = '<div class="t-date">去程 ' + esc(fmtD(w.d)) + '（週六）· 回程 ' + esc(fmtD(w.r)) + '</div>';
    let price = w.min;
    if (state.flight !== 'all') price = (w.f && w.f[state.flight] !== undefined) ? w.f[state.flight] : null;
    if (price !== null && price !== undefined) {
      const diff = diffPct(price, avg);
      html += '<div class="t-price">' + fmt(price) + '</div>' +
        '<div class="' + (diff <= 0 ? 't-low' : 't-high') + '">比平均' + (diff <= 0 ? '便宜' : '貴') + ' ' + Math.abs(diff) + '%</div>';
    } else if (w.status === 'sold_out') {
      html += '<div class="t-none">本週已售罄</div>';
    } else {
      html += '<div class="t-none">本週無資料</div>';
    }
    if (state.flight !== 'all') {
      html += '<div class="t-fl">航班 ' + esc(state.flight) + '</div>';
    } else if (w.min !== null && w.f) {
      const no = Object.keys(w.f).find(k => w.f[k] === w.min);
      if (no) html += '<div class="t-fl">最低價航班 ' + esc(no) + '</div>';
    }
    tip.innerHTML = html;
    tip.classList.add('show');
    if (ev) {
      const isMobile = window.matchMedia('(max-width: 767px)').matches;
      if (isMobile) {
        tip.style.left = Math.min(ev.clientX + 10, window.innerWidth - 190) + 'px';
        tip.style.top = Math.max(ev.clientY - 90, 4) + 'px';
      } else {
        const r = chartWrap.getBoundingClientRect();
        tip.style.left = Math.min(ev.clientX - r.left + 16, r.width - 190) + 'px';
        tip.style.top = Math.max(ev.clientY - r.top - 12, 4) + 'px';
      }
    }
  }
  function hideTip() { tip.classList.remove('show'); }

  // ═══════════════ Summary（§2.7） ═══════════════
  function renderSummary(visible, avg) {
    const s = summaryData(visible, avg);
    if (!s.minWeek) {
      // 可見範圍無有效價（如全缺週的早期區間）：Summary 整區隱藏
      summary.hidden = true;
      return;
    }
    summary.hidden = false;
    const dMin = diffPct(s.minWeek.min, avg);
    sumMin.textContent = fmtD(s.minWeek.d) + ' 出發 · ' + fmt(s.minWeek.min);
    sumMinS.textContent = '比平均' + (dMin <= 0 ? '低' : '高') + ' ' + Math.abs(dMin) + '%';
    sumAvg.textContent = fmt(avg);
    sumPeak.textContent = fmtD(s.peakWeek.d) + ' 出發 · ' + fmt(s.peakWeek.min);
    sumPeakS.textContent = s.peakNote;
  }

  // ═══════════════ 狀態處理（§2.8） ═══════════════
  function setLoading(loading) {
    state.loading = loading;
    skeleton.hidden = !loading;
    if (loading) { setChartHidden(true); emptyBox.hidden = true; }
    // 載入完成後的 chart/emptyBox 顯示由 drawCurrentRoute 依資料決定（避免空航線顯示空白 SVG）
    setToolbarDisabled(loading);
  }

  function showError(code, detail) {
    errBox.hidden = false;
    setChartHidden(true);
    skeleton.hidden = true;
    const msgs = {
      ERR_ORIGIN_FORBIDDEN: ['來源不被允許', '請改用 GitHub Pages 網址開啟'],
      ERR_INDEX_FETCH: ['無法讀取資料目錄（HTTP 非 2xx）', '請稍後重試'],
      ERR_INDEX_INVALID: ['資料目錄格式異常', '請稍後重試'],
      ERR_NETWORK: ['網路連線失敗', '請檢查網路後重試'],
    };
    const [title, hint] = msgs[code] || ['資料載入失敗', '請稍後重試'];
    errDetail.textContent = title + '（' + code + '）— ' + hint;
  }

  function showStale(generatedAt) {
    staleBar.hidden = false;
    staleText.textContent = '資料可能過時，上次更新：' + formatGeneratedAt(generatedAt) + '（超過 ' + CONFIG.STALE_DAYS + ' 天）';
  }

  // ═══════════════ 初始流程 ═══════════════
  async function drawCurrentRoute() {
    skeleton.hidden = false;
    const weeks = await loadRoute(state.route, (loaded, total) => {
      progress.textContent = '已載入 ' + loaded + '/' + total;
    });
    if (!weeks) return; // 過期回應已丟棄
    routeCache.set(state.route, weeks);
    renderFlightSel(weeks);
    skeleton.hidden = true;
    // 圖表顯示/隱藏與空狀態由 buildChart 依資料決定（含 trips 全缺、全部無效價）
    setChartHidden(false);
    emptyBox.hidden = true;
    buildChart();
  }

  async function init() {
    errBox.hidden = true;
    emptyBox.hidden = true;
    staleBar.hidden = true;
    renderRouteTabs();
    renderRangeSeg();
    initControls();

    // 1. 來源檢查（E2）
    if (!originAllowed(location.origin)) {
      showError('ERR_ORIGIN_FORBIDDEN');
      return;
    }
    // 2. 載入 index
    setLoading(true);
    try {
      INDEX = await fetchIndex();
    } catch (e) {
      setLoading(false);
      showError(e.code || 'ERR_NETWORK');
      return;
    }
    // 3. 過舊警示 + 更新時間（F-10 / F-15）
    updText.textContent = '資料更新 ' + formatGeneratedAt(INDEX.generated_at) + ' · 每週五更新';
    if (isStale(INDEX.generated_at)) showStale(INDEX.generated_at);
    // 4. 空資料（E7）
    if (!INDEX.trips.length) {
      setLoading(false);
      emptyBox.hidden = false;
      setChartHidden(true);
      summary.hidden = true; // 無資料：Summary 三卡整區隱藏
      return;
    }
    // 5. 載入預設航線
    await drawCurrentRoute();
    setLoading(false);
  }

  // tooltip 事件（事件委派）
  chart.addEventListener('mousemove', e => {
    const c = e.target.closest ? e.target.closest('circle[data-i]') : null;
    if (!c) { hideTip(); return; }
    const i = +c.dataset.i;
    const weeks = routeCache.get(state.route) || [];
    const visible = filterRange(weeks, (CONFIG.RANGES.find(r => r.key === state.range) || {}).weeks);
    showTip(visible[i], e);
  });
  chart.addEventListener('mouseleave', hideTip);
  chart.addEventListener('focusin', e => {
    const c = e.target.closest ? e.target.closest('circle[data-i]') : null;
    if (!c) return;
    const i = +c.dataset.i;
    const weeks = routeCache.get(state.route) || [];
    const visible = filterRange(weeks, (CONFIG.RANGES.find(r => r.key === state.range) || {}).weeks);
    // 鍵盤 focus 無滑鼠座標 → 以資料點位置定位 tooltip（E2E-17）
    const rect = c.getBoundingClientRect();
    showTip(visible[i], { clientX: rect.left + rect.width / 2, clientY: rect.top });
  });
  chart.addEventListener('focusout', hideTip);

  // 啟動
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
