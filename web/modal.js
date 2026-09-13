/**
 * 詳情 Modal（003-ccard-detail）
 *
 * 職責：詳情 Modal 的開關、航班表格/卡片渲染、歷史走勢圖表、footer
 * 依賴：web/aggregate.js（全域 PriceAgg，純函式）
 */
window.PriceModal = (function () {
  'use strict';

  function createModal(state, deps) {
    const CACHE = deps.CACHE;
    const routeCache = deps.routeCache;
    const PriceAgg = deps.PriceAgg;
    const DOM_REFS = deps.DOM_REFS;

    // ── DOM refs ──
    const detailOverlay = DOM_REFS.detailOverlay;
    const detailPanel = detailOverlay.querySelector('.detail-panel');
    const detailBackdrop = detailOverlay.querySelector('.detail-backdrop');
    const detailTitle = DOM_REFS.detailTitle;
    const detailClose = DOM_REFS.detailClose;
    const detailFlightBody = DOM_REFS.detailFlightBody;
    const detailChartWrap = DOM_REFS.detailChartWrap;
    const detailChart = DOM_REFS.detailChart;
    const detailStats = DOM_REFS.detailStats;
    const detailFooter = DOM_REFS.detailFooter;
    const detailSkeleton = DOM_REFS.detailSkeleton;
    const detailError = DOM_REFS.detailError;
    const detailRetry = DOM_REFS.detailRetry;

    // ── 內部狀態 ──
    let lastTriggerCcard = null;
    let currentDetailWeek = null;
    let _currentVisible = [];

    // ── 格式化（僅 modal 內部使用）──
    const fmt = n => 'NT$' + n.toLocaleString('en-US');
    const fmtD = d => d ? d.split('-').slice(1).join('/') : '—';
    const esc = s => String(s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const NS = 'http://www.w3.org/2000/svg';
    const svgEl = (name, attrs) => {
      const e = document.createElementNS(NS, name);
      for (const k in attrs) e.setAttribute(k, attrs[k]);
      return e;
    };

    // ═══════════════ aggregateHistory ═══════════════

    /**
     * 從 CACHE.units 中聚合指定 outbound_date 的歷史走勢資料。
     */
    function aggregateHistory(outboundDate, routeId) {
      if (!CACHE || !CACHE.units) return [];
      const pat = '/' + routeId + '/';
      for (const [url, rec] of Object.entries(CACHE.units)) {
        if (!url.includes(pat)) continue;
        const { d } = PriceAgg.datesFromUrl(url);
        if (d !== outboundDate) continue;
        const json = rec.json;
        if (!json || !Array.isArray(json.flights)) return [];
        const scrapedDates = new Set();
        for (const fl of json.flights) {
          if (!Array.isArray(fl.history)) continue;
          for (const h of fl.history) {
            if (h.scraped_at) scrapedDates.add(h.scraped_at.split('T')[0]);
          }
        }
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

    // ═══════════════ getRawFlightsForWeek ═══════════════

    /**
     * 從 CACHE.units 取得該 week 的原始航班列表（含售罄）。
     */
    function getRawFlightsForWeek(week) {
      if (!CACHE || !CACHE.units || !week.d) return [];
      const pat = '/' + state.route + '/';
      for (const [url, rec] of Object.entries(CACHE.units)) {
        if (!url.includes(pat)) continue;
        const { d } = PriceAgg.datesFromUrl(url);
        if (d !== week.d) continue;
        const json = rec.json;
        if (json && Array.isArray(json.flights)) return json.flights;
      }
      return [];
    }

    // ═══════════════ buildSingleFlightHTML ═══════════════

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
        const diff = PriceAgg.diffPct(fl.price, avg);
        const cls = diff <= 0 ? 'diff-low' : 'diff-high';
        const arrow = diff < 0 ? '↓' : '↑';
        diffHTML = '<div class="' + cls + '">' + arrow + ' 比平均' + (diff <= 0 ? '便宜' : '貴') + ' ' + Math.abs(diff) + '%</div>';
      }

      return '<div class="detail-flight-card-no">' + esc(fl.no) + '</div>' +
        '<div class="detail-flight-card-time">' + esc((fl.depTime || '—') + ' → ' + (fl.arrTime || '—')) + '</div>' +
        '<div class="detail-flight-card-price">' + priceHTML + '</div>' +
        diffHTML;
    }

    // ═══════════════ open / close ═══════════════

    function openDetail(week, triggerCcard) {
      currentDetailWeek = week;
      lastTriggerCcard = triggerCcard;
      detailFlightBody.innerHTML = '';
      detailChart.innerHTML = '';
      detailStats.innerHTML = '';
      detailFooter.innerHTML = '';
      detailError.hidden = true;
      detailSkeleton.hidden = false;
      detailTitle.textContent = fmtD(week.d) + ' 出發 → ' + fmtD(week.r) + ' 回程';
      detailOverlay.hidden = false;
      document.body.style.overflow = 'hidden';
      detailClose.focus();
      requestAnimationFrame(() => {
        renderDetailFlight(week);
        renderDetailChart(week);
        renderDetailFooter(week);
        detailSkeleton.hidden = true;
      });
    }

    function closeDetail() {
      detailOverlay.hidden = true;
      document.body.style.overflow = '';
      currentDetailWeek = null;
      if (lastTriggerCcard) {
        lastTriggerCcard.focus();
        lastTriggerCcard = null;
      }
    }

    // ── Modal 事件綁定（Escape、focus trap、retry）──
    detailClose.addEventListener('click', closeDetail);
    detailBackdrop.addEventListener('click', closeDetail);
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !detailOverlay.hidden) closeDetail();
    });
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

    detailRetry.addEventListener('click', () => {
      if (currentDetailWeek) openDetail(currentDetailWeek, lastTriggerCcard);
    });

    // ═══════════════ renderDetailFlight ═══════════════

    function renderDetailFlight(week) {
      const rawFlights = getRawFlightsForWeek(week);
      const allFlights = rawFlights.map(fl => {
        const no = fl.outbound_flight_no;
        const { price, status } = PriceAgg.latestPrice(fl);
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

      const avg = PriceAgg.globalAverage(routeCache.get(state.route) || []);

      if (allFlights.length === 1) {
        const fl = allFlights[0];
        const card = document.createElement('div');
        card.className = 'detail-flight-card';
        card.innerHTML = buildSingleFlightHTML(fl, avg);
        detailFlightBody.appendChild(card);
        return;
      }

      allFlights.sort((a, b) => {
        if (a.price === null && b.price === null) return 0;
        if (a.price === null) return 1;
        if (b.price === null) return -1;
        return a.price - b.price;
      });

      const minPrice = allFlights.find(f => f.price !== null)?.price ?? null;
      const table = document.createElement('table');
      table.className = 'detail-flight-table';
      const thead = document.createElement('thead');
      thead.innerHTML = '<tr><th>航班</th><th>時間</th><th>價格</th><th>比平均</th></tr>';
      table.appendChild(thead);
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
          const diff = PriceAgg.diffPct(fl.price, avg);
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

      if (minPrice !== null) {
        const minFl = allFlights.find(f => f.price === minPrice);
        const summary = document.createElement('div');
        summary.className = 'detail-flight-summary';
        summary.textContent = '最低價：' + minFl.no + ' ' + fmt(minPrice);
        detailFlightBody.appendChild(summary);
      }
    }

    // ═══════════════ renderDetailChart ═══════════════

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
        detailChartWrap.hidden = true;
        return;
      }

      if (validHistory.length === 1) {
        renderDetailChartSinglePoint(validHistory[0], week);
        return;
      }

      renderDetailChartLine(validHistory, week);
    }

    function renderDetailChartSinglePoint(point, week) {
      const W = 480, H = 160;
      const cx = W / 2, cy = H / 2;
      detailChart.appendChild(svgEl('circle', { cx, cy, r: 6, fill: 'var(--accent, #1a73e8)' }));
      const label = svgEl('text', { x: cx, y: cy - 14, 'text-anchor': 'middle', class: 'detail-chart-label' });
      label.textContent = fmt(point.price);
      detailChart.appendChild(label);
      detailStats.innerHTML = '<div class="detail-single-hint">僅 1 個資料點，尚無走勢可比較</div>';
      detailStats.innerHTML += '<div class="detail-stats-row">資料日期：' + esc(point.scrapedAt) + '</div>';
    }

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
          stroke: 'var(--border, #e0e0e0)', 'stroke-width': 0.5, 'stroke-dasharray': '2,2',
        }));
        const t = svgEl('text', {
          x: M.l - 4, y: Y(v) + 4,
          'text-anchor': 'end', fill: 'var(--muted, #888)', 'font-size': '9px', 'font-family': 'var(--mono, monospace)',
        });
        t.textContent = (v / 1000).toFixed(v % 1000 === 0 ? 0 : 1) + 'K';
        detailChart.appendChild(t);
      }

      // X 軸日期標記
      const stepX = Math.max(1, Math.floor(n / 5));
      for (let i = 0; i < n; i += stepX) {
        const t = svgEl('text', {
          x: X(i), y: H - 4,
          'text-anchor': 'middle', fill: 'var(--muted, #888)', 'font-size': '8px', 'font-family': 'var(--mono, monospace)',
        });
        t.textContent = history[i].scrapedAt.slice(5);
        detailChart.appendChild(t);
      }

      // 折線
      let d = '';
      history.forEach((h, i) => {
        d += (i === 0 ? 'M' : ' L') + X(i) + ' ' + Y(h.price);
      });
      detailChart.appendChild(svgEl('path', {
        d, fill: 'none', stroke: 'var(--accent, #1a73e8)', 'stroke-width': 2, 'stroke-linejoin': 'round',
      }));

      // 找最低點與最新點
      let minIdx = 0;
      history.forEach((h, i) => { if (h.price < history[minIdx].price) minIdx = i; });
      const latestIdx = history.length - 1;

      // 最低點（綠色）
      detailChart.appendChild(svgEl('circle', {
        cx: X(minIdx), cy: Y(history[minIdx].price), r: 5,
        fill: 'var(--success, #228b22)', stroke: '#fff', 'stroke-width': 1.5,
      }));
      const minLabel = svgEl('text', {
        x: X(minIdx), y: Y(history[minIdx].price) - 10,
        'text-anchor': 'middle', fill: 'var(--success, #228b22)', 'font-size': '9px', 'font-weight': '600', 'font-family': 'var(--mono, monospace)',
      });
      minLabel.textContent = fmt(history[minIdx].price);
      detailChart.appendChild(minLabel);

      // 最新點（藍色；若與最低點重合則不重複標記）
      if (latestIdx !== minIdx) {
        detailChart.appendChild(svgEl('circle', {
          cx: X(latestIdx), cy: Y(history[latestIdx].price), r: 5,
          fill: 'var(--accent, #1a73e8)', stroke: '#fff', 'stroke-width': 1.5,
        }));
      }

      // 統計摘要
      const allPrices = history.map(h => h.price);
      const lowest = Math.min(...allPrices);
      const highest = Math.max(...allPrices);
      const lowestDate = history[allPrices.indexOf(lowest)].scrapedAt;
      const highestDate = history[allPrices.indexOf(highest)].scrapedAt;
      const dropPct = highest > 0 ? Math.round((highest - lowest) / highest * 100) : 0;
      const allSame = prices.every(p => p === prices[0]);

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

    // ═══════════════ renderDetailFooter ═══════════════

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

    // ═══════════════ 公開方法 ═══════════════

    return {
      openDetail,
      closeDetail,
      setCurrentVisible(visible) {
        _currentVisible = visible;
      },
      getCurrentVisible() {
        return _currentVisible;
      },
    };
  }

  return { createModal };
})();
