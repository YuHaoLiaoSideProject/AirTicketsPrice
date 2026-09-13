/**
 * controls.js — 地區下拉、航線 tab、航班 select、範圍按鈕的 DOM 渲染 + 事件綁定
 *
 * UMD 模式：IIFE + window.PriceControls = factory()
 * 依賴：web/aggregate.js（PriceAgg.isExpired）、web/cache.js（OfflineCache）
 */
window.PriceControls = (function () {
  'use strict';

  /**
   * @param {object} state — 共享狀態 { region, route, flight, range, loading }
   * @param {object} deps
   * @param {object} deps.CONFIG — CONFIG（ROUTES, REGIONS, RANGES）
   * @param {function} deps.getINDEX — 回傳目前 INDEX 的 getter
   * @param {object} deps.DOM_REFS — { regionSel, routeTabs, flightSel, rangeSeg, progress, toolbar }
   * @param {object} deps.cacheRef — { CACHE } 或 { getCache: () => CACHE }
   * @param {function} deps.showRouteHint — (tabBtn) => void
   * @param {function} deps.setAbort — (signal) => void （beforeunload 中止 fetch）
   * @param {function} deps.onInit — () => void （retryBtn 點擊 callback）
   * @param {object} callbacks — { onRegionChange, onRouteChange, onFlightChange, onRangeChange }
   */
  function createControls(state, deps) {
    const {
      CONFIG,
      getINDEX,
      DOM_REFS: { regionSel, routeTabs, flightSel, rangeSeg, progress },
      cacheRef,
      showRouteHint,
      setAbort,
      onInit,
      callbacks: { onRegionChange, onRouteChange, onFlightChange, onRangeChange },
    } = deps;

    let controlsBound = false;

    /** 取得目前生效的地區分群（API 帶入優先；fallback 至 CONFIG.REGIONS） */
    function getRegions() {
      const idx = getINDEX();
      return (idx && idx.regions) || CONFIG.REGIONS;
    }

    /** 地區 id → 該地區的航線 id 陣列 */
    function routesForRegion(regionId) {
      const regions = getRegions();
      const reg = regions.find(r => r.id === regionId);
      return reg ? reg.routes : [];
    }

    /** 航線 id → 所屬地區 id（找不到回傳第一個地區） */
    function regionForRoute(routeId) {
      const regions = getRegions();
      for (const reg of regions) {
        if (reg.routes.includes(routeId)) return reg.id;
      }
      return regions[0] ? regions[0].id : (CONFIG.REGIONS[0] ? CONFIG.REGIONS[0].id : 'japan');
    }

    function renderRegionSel() {
      const regions = getRegions();
      regionSel.innerHTML = '';
      regions.forEach(r => {
        const o = document.createElement('option');
        o.value = r.id;
        o.textContent = r.name;
        regionSel.appendChild(o);
      });
      regionSel.value = state.region;
    }

    function renderRouteTabs() {
      routeTabs.innerHTML = '';
      const routeIds = routesForRegion(state.region);
      const routes = CONFIG.ROUTES.filter(r => routeIds.includes(r.id));
      routes.forEach(r => {
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

    /** 航班下拉選項 = 該航線所有未過期週 flights 聯集；目前航班不存在 → 回退 all */
    function renderFlightSel(weeks) {
      const { isExpired } = window.PriceAgg;
      const flightSet = [];
      for (const w of weeks) {
        if (isExpired(w.d)) continue; // 過期週不出現在航班下拉選項
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

    function initControls() {
      if (controlsBound) return; // 重試（init 重跑）不重複綁定，避免 listener 堆疊
      controlsBound = true;

      regionSel.addEventListener('change', async () => {
        const newRegion = regionSel.value;
        if (newRegion === state.region) return;
        state.region = newRegion;
        localStorage.setItem('airtickets-region', newRegion);
        // onRegionChange callback（切換地區後，選該地區的第一條航線）
        if (onRegionChange) await onRegionChange(newRegion);
      });

      routeTabs.addEventListener('click', async e => {
        const b = e.target.closest('button[data-route]');
        if (!b || b.dataset.route === state.route || state.loading) return;
        // E2：離線切到從未載入航線 → tab 提示 + 停留原航線（不切換、不發請求、不出錯誤卡）
        const cache = cacheRef.CACHE;
        if (!navigator.onLine && !OfflineCache.hasCache(
          cache ? cache.units : {}, cache ? cache.meta : null, b.dataset.route
        )) {
          showRouteHint(b);
          return;
        }
        state.route = b.dataset.route;
        renderRouteTabs();
        if (onRouteChange) await onRouteChange();
      });

      flightSel.addEventListener('change', () => {
        state.flight = flightSel.value;
        if (onFlightChange) onFlightChange();
      });

      rangeSeg.addEventListener('click', e => {
        const b = e.target.closest('button[data-range]');
        if (!b) return;
        state.range = b.dataset.range;
        renderRangeSeg();
        if (onRangeChange) onRangeChange();
      });

      if (onInit) {
        document.getElementById('retryBtn').addEventListener('click', onInit);
      }
      if (setAbort) {
        window.addEventListener('beforeunload', () => setAbort()); // F-22
      }
    }

    return {
      renderRegionSel,
      renderRouteTabs,
      renderFlightSel,
      renderRangeSeg,
      initControls,
      setToolbarDisabled,
      getRegions,
      routesForRegion,
      regionForRoute,
    };
  }

  return { createControls };
})();
