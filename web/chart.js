/**
 * 票價趨勢圖 — SVG 圖表渲染模組
 * 職責：主 SVG 圖表渲染（網格、旺季區塊、平均線、資料點、折線、dot、badge、最便宜標記）、Tooltip 顯示/隱藏
 *
 * 依賴：PriceAgg（全域）、CONFIG（全域）
 * 對照：docs/refactoring-plan.md §2.1
 */
window.PriceChart = (function () {
  'use strict';

  const {
    filterRangeWithExpiry,
    globalAverage,
    hasAnyPrice,
    minMark,
    getPeaks,
    summaryData,
    diffPct,
    formatChangePct,
  } = window.PriceAgg;

  // SVG 常數
  const NS = 'http://www.w3.org/2000/svg';

  // HTML 跳脫（API 資料進入 innerHTML 前必經，防 XSS）
  const esc = s => String(s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // SVG 元素工廠
  const svgEl = (name, attrs) => {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  };

  /**
   * 建構圖表實例
   * @param {object} state - 共享狀態（只讀：state.route, state.flight, state.range）
   * @param {object} deps - 依賴注入
   * @param {Map} deps.routeCache - Map<routeId, Week[]>
   * @param {object} deps.CONFIG - 全域設定
   * @param {object} deps.DOM_REFS - { chart, chartWrap, tip, chartTitle }
   * @param {Function} deps.setChartHidden - SVG hidden attribute 控制
   * @param {Function} deps.renderSummary - Summary 三卡渲染（保留在 app.js）
   * @param {Function} deps.renderChangeCards - 每週漲跌卡片渲染（保留在 app.js）
   */
  function createChart(state, deps) {
    const { routeCache, CONFIG, DOM_REFS, setChartHidden, renderSummary, renderChangeCards } = deps;
    const { chart, chartWrap, tip, chartTitle } = DOM_REFS;

    const { W, H, M } = CONFIG.SVG;

    const fmt = n => 'NT$' + n.toLocaleString('en-US');
    const fmtD = d => d ? d.split('-').slice(1).join('/') : '—';
    const rangeLabel = () => (CONFIG.RANGES.find(r => r.key === state.range) || CONFIG.RANGES[3]).label;

    // ── currentVisible：由 buildChart 計算，供 app.js 的 bindCcardClicks 使用 ──
    let currentVisible = [];

    function buildChart() {
      const weeks = routeCache.get(state.route) || [];
      // 航線無任何有效價格資料：一律空狀態
      if (!hasAnyPrice(weeks)) {
        setChartHidden(true);
        deps.emptyBox.hidden = false;
        chartTitle.textContent = '';
        chart.removeAttribute('aria-label');
        chart.innerHTML = '';
        // 無資料：Summary 三卡清空並整區隱藏
        deps.sumMin.textContent = '—'; deps.sumMinS.textContent = '';
        deps.sumAvg.textContent = '—';
        deps.sumPeak.textContent = '—'; deps.sumPeakS.textContent = '';
        deps.summary.hidden = true;
        deps.changeTableWrap.hidden = true;
        return;
      }
      currentVisible = filterRangeWithExpiry(weeks, (CONFIG.RANGES.find(r => r.key === state.range) || {}).weeks);
      // 同步給 modal（供詳情面板使用）
      if (deps.modal && deps.modal.setCurrentVisible) {
        deps.modal.setCurrentVisible(currentVisible);
      }
      const visible = currentVisible;
      const avg = globalAverage(weeks);
      const n = visible.length;
      if (n === 0) return;
      const X = i => M.l + i * (W - M.l - M.r) / Math.max(n - 1, 1);

      // ── 動態 Y 軸 ──
      const prices = visible.map(w => w.min).filter(v => v != null);
      const dataMin = Math.min(...prices);
      const dataMax = Math.max(...prices);
      const PAD = 1000;
      const YMIN = Math.max(0, Math.floor((dataMin - PAD) / 1000) * 1000);
      const YMAX = Math.ceil((dataMax + PAD) / 1000) * 1000;
      const yMin = Math.min(YMIN, YMAX - 6000);
      const yMax = Math.max(YMAX, YMIN + 6000);

      const Y = v => H - M.b - (v - yMin) / (yMax - yMin) * (H - M.t - M.b);
      const Yclamp = v => Math.max(Y(v), M.t);

      chart.innerHTML = '';
      const routeInfo = CONFIG.ROUTES.find(r => r.id === state.route) || { name: state.route };

      // Y 軸網格 + 標籤
      const range = yMax - yMin;
      const step = range <= 12000 ? 2000 : range <= 18000 ? 3000 : range <= 24000 ? 4000 : range <= 36000 ? 5000 : 6000;
      for (let v = yMin; v <= yMax; v += step) {
        chart.appendChild(svgEl('line', { x1: M.l, y1: Y(v), x2: W - M.r, y2: Y(v), 'class': 'grid-major' }));
        const t = svgEl('text', { x: M.l - 8, y: Y(v) + 4, 'class': 'tick-label', 'text-anchor': 'end' });
        t.textContent = (v / 1000) + 'K';
        chart.appendChild(t);
      }
      // X 軸標籤
      for (let i = 0; i < n; i += 4) {
        const t = svgEl('text', { x: X(i), y: H - M.b + 16, 'class': 'tick-label', 'text-anchor': 'middle' });
        t.textContent = fmtD(visible[i].d);
        chart.appendChild(t);
      }
      const cap = svgEl('text', { x: W - M.r, y: H - 4, 'class': 'axis-caption', 'text-anchor': 'end' });
      cap.textContent = '出發日期（週六）· 單位 TWD 來回';
      chart.appendChild(cap);

      // 旺季區塊
      getPeaks().forEach(p => {
        if (p.routes && !p.routes.includes(state.route)) return;
        const start = visible.findIndex(w => w.d >= p.from);
        if (start < 0) return;
        if (visible[start].d > p.to) return;
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

      // 資料點
      const pts = visible.map((w, i) => {
        let price = w.min;
        if (state.flight !== 'all') price = (w.f && w.f[state.flight] !== undefined) ? w.f[state.flight] : null;
        return { i, x: X(i), y: price === null ? null : Yclamp(price), price, w };
      });

      // 折線（斷點分段）
      let d = '', seg = false, hasLine = false;
      pts.forEach(p => {
        if (p.y === null) { seg = false; return; }
        if (seg) hasLine = true;
        d += (seg ? ' L' : ' M') + p.x + ' ' + p.y;
        seg = true;
      });
      if (hasLine) chart.appendChild(svgEl('path', { d, 'class': 'price-line' }));

      // 資料點 circle + 售罄/缺資料標示 + 漲跌 badge
      pts.forEach(p => {
        if (p.y === null) {
          chart.appendChild(svgEl('circle', { cx: p.x, cy: Y(avg ?? yMin), r: 4, 'class': 'gap-dot', 'data-i': p.i }));
          if (p.w.status === 'sold_out') {
            const t = svgEl('text', { x: p.x, y: H - M.b + 30, 'class': 'sold-out-label', 'text-anchor': 'middle' });
            t.textContent = '售罄';
            chart.appendChild(t);
          }
          return;
        }
        let dotClass = 'dot';
        if (p.w.minChangePct !== null && p.w.minChangePct !== undefined) {
          if (p.w.minChangePct < 0) dotClass += ' down';
          else if (p.w.minChangePct > 0) dotClass += ' up';
        }
        const c = svgEl('circle', { cx: p.x, cy: p.y, r: 4, 'class': dotClass, tabindex: '0', role: 'button' });
        c.setAttribute('data-i', p.i);
        c.setAttribute('aria-label', '出發 ' + p.w.d + '，價格 ' + fmt(p.price));
        chart.appendChild(c);

        // 漲跌 badge
        if (p.w.minChangePct !== null && p.w.minChangePct !== undefined && Math.abs(p.w.minChangePct) >= 1) {
          const isDrop = p.w.minChangePct < 0;
          const badgeClass = isDrop ? 'change-badge down' : (p.w.minChangePct > 0 ? 'change-badge up' : 'change-badge flat');
          const badgeText = (isDrop ? '↓' : '↑') + ' ' + Math.abs(p.w.minChangePct) + '%';
          const badgeY = isDrop ? p.y - 11 : p.y + 15;
          const badge = svgEl('text', { x: p.x, y: badgeY, 'class': badgeClass, 'text-anchor': 'middle' });
          badge.textContent = badgeText;
          chart.appendChild(badge);
        }
      });

      // 可見範圍最低價標記
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

      // chart-title
      const flightLabel = state.flight === 'all' ? '每週最低價' : '航班 ' + state.flight;
      chartTitle.innerHTML = '<b>' + esc(routeInfo.name) + ' ' + esc(state.route) + '</b> · ' +
        esc(flightLabel) + ' · 顯示 ' + esc(rangeLabel()) + '（共 ' + n + ' 週）';
      chart.setAttribute('aria-label', '票價趨勢圖：' + routeInfo.name + ' ' + state.route + '，' + flightLabel + '，' + rangeLabel());

      renderSummary(visible, avg, state.route);
      renderChangeCards(visible);
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
        if (state.flight !== 'all') {
          const fc = w.fc && w.fc[state.flight];
          if (fc && fc.changePct !== null && fc.changePct !== undefined) {
            const cls = fc.changePct <= 0 ? 't-low' : 't-high';
            const arrow = fc.changePct < 0 ? '↓' : '↑';
            html += '<div class="' + cls + '">較上次 ' + formatChangePct(fc.changePct) +
              '（' + arrow + ' ' + fmt(Math.abs(fc.change)) + '）</div>';
          }
        } else if (w.minChangePct !== null && w.minChangePct !== undefined) {
          const cls = w.minChangePct <= 0 ? 't-low' : 't-high';
          const arrow = w.minChangePct < 0 ? '↓' : '↑';
          html += '<div class="' + cls + '">較上次 ' + formatChangePct(w.minChangePct) +
            '（' + arrow + ' ' + fmt(Math.abs(w.minChange)) + '）</div>';
        }
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

    // ═══════════════ Tooltip 事件綁定 ═══════════════
    function bindChartEvents() {
      chart.addEventListener('mousemove', e => {
        const c = e.target.closest ? e.target.closest('circle[data-i]') : null;
        if (!c) { hideTip(); return; }
        const i = +c.dataset.i;
        showTip(currentVisible[i], e);
      });
      chart.addEventListener('mouseleave', hideTip);
      chart.addEventListener('focusin', e => {
        const c = e.target.closest ? e.target.closest('circle[data-i]') : null;
        if (!c) return;
        const i = +c.dataset.i;
        const rect = c.getBoundingClientRect();
        showTip(currentVisible[i], { clientX: rect.left + rect.width / 2, clientY: rect.top });
      });
      chart.addEventListener('focusout', hideTip);
    }

    return {
      buildChart,
      showTip,
      hideTip,
      bindChartEvents,
      getVisibleWeeks: () => currentVisible,
    };
  }

  return { createChart };
})();
