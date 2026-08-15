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
    minMark, detectPeak, isStale, originAllowed, formatGeneratedAt, formatLastUpdated, summaryData,
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
  const offBar = $('offBar');         // 離線橫幅（T4 / §2.3.1）
  const refreshBtn = $('refreshBtn'); // 手動更新按鈕（T5 / §2.3.1）
  const syncStatus = $('syncStatus'); // 更新時間旁的同步狀態文字（已是最新 / 更新失敗…，§2.3.1）
  const installBtn = $('installBtn'); // PWA 安裝按鈕（T4 / §2.5 ①；初始 hidden）
  const iosHint = $('iosHint');       // iOS「加到主畫面」提示（T4 / §2.5 ②；初始 hidden）

  // ⚠️ SVG 元素的 `.hidden = true` 只改 IDL property、不會反映成 hidden attribute（Chromium 怪癖），
  // 而 CSS `[hidden] { display:none }` 依賴 attribute → 圖表會"看似隱藏其實仍顯示"。
  // 統一用 setAttribute/removeAttribute 控制 #chart 顯示狀態。
  const setChartHidden = hidden => {
    if (hidden) chart.setAttribute('hidden', '');
    else chart.removeAttribute('hidden');
  };

  // ═══════════════ PWA 安裝入口（T4 / §2.6；pwa.js 掛全域 Pwa，§2.4） ═══════════════
  const Pwa = window.Pwa;
  const installState = Pwa.installStateMachine();

  /** standalone 判定（P1-C）：display-mode:standalone 或 iOS navigator.standalone */
  const isStandalone = () =>
    window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

  /**
   * 安裝 UI 渲染（F-01 / F-03 / P1-B）：
   *  - 安裝按鈕：僅 available 且非 standalone 且非 iOS → 顯示（iOS 走「加到主畫面」提示）
   *  - iOS 提示：isIOS 且非 standalone → 顯示（P1-B / BR5）；standalone → 兩者皆隱藏（P1-C）
   */
  function renderInstallUI() {
    const ios = Pwa.isIOS(navigator.userAgent);
    const showBtn = Pwa.shouldShowInstall(installState.state(), isStandalone());
    installBtn.hidden = !(showBtn && !ios);
    iosHint.hidden = !(ios && !isStandalone());
  }

  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();                        // 暫存 deferred prompt，點擊才呼叫（F-02 / BR40）
    installState.setPrompt(e);
    renderInstallUI();
  });

  window.addEventListener('appinstalled', () => {
    installState.reset();                      // 已安裝 → 清暫存；standalone 下按鈕永不顯示（P1-C）
    renderInstallUI();
  });

  installBtn.addEventListener('click', () => {
    // 只有 user gesture 才呼叫原生安裝流程（deferred prompt 暫存語意）
    installState.prompt().then(choice => {
      if (choice && choice.outcome === 'dismissed') renderInstallUI();  // 取消 → 按鈕保留可再觸發（P1-A）
    });
  });

  renderInstallUI();   // 初始狀態（無事件時按鈕/提示皆 hidden；iOS 非 standalone → 顯示提示）

  // ═══════════════ PWA 訂閱 toggle 與狀態 UI（T9 / §2.6，Phase 2） ═══════════════
  const subBtn = $('subBtn'), subStatus = $('subStatus');
  let vapidKey = null;                        // VAPID 公鑰快取（E3 失敗 → null → 按鈕停用）

  /** 流程暫時性結果（覆寫自然三態：E1/E2/E8/EC6/E3） */
  const FLOW_OVERRIDE_STATES = ['denied', 'error', 'ios-required', 'ios-unsupported', 'macos-required', 'unavailable'];

  /**
   * 訂閱 UI 渲染（F-05a~d / F-10 / F-20 / F-26）：
   *   subscriptionUI 三態 + flow 暫時性結果覆寫（hint 優先取自 flow，避免閃失）
   */
  function renderSubUI(permission, subscription, flow) {
    // 離線 → 不顯示 unavailable（還原本機三態，E1 本機真相優先）；線上才以 vapidReady 判服務可用性（E3）
    const vapidReady = navigator.onLine ? !!vapidKey : true;
    const loading = !!(flow && flow.state === 'loading');     // 流程中：點擊後立即回饋，不等 async（F-22 UI 層防連點）
    const ui = Pwa.subscriptionUI(permission, subscription, {
      vapidReady,
      ...(loading ? { state: 'loading' } : {}),
    });
    const override = flow && (FLOW_OVERRIDE_STATES.includes(flow.state) || loading);
    const state = override ? flow.state : ui.state;
    const hint = override ? (flow.hint || ui.hint) : ui.hint;
    subBtn.hidden = false;
    subBtn.textContent = ui.buttonLabel;                     // 開啟票價提醒 / 處理中… / 關閉票價提醒
    subBtn.disabled = !vapidKey || state === 'loading';      // E3 停用；loading 期間鎖定防連點
    subBtn.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');  // 無障礙：處理中狀態
    subStatus.hidden = !hint;
    subStatus.textContent = hint || '';
    subBtn.classList.toggle('subscribed', state === 'subscribed');   // §7 .sub-toggle.subscribed
    subBtn.classList.toggle('loading', state === 'loading');         // §7 spinner（點擊後立即回饋）
    subStatus.classList.toggle('warn', state === 'denied' || state === 'error');
    subStatus.classList.toggle('unavailable', state === 'unavailable');  // E3（§7 .sub-status.unavailable）
  }

  /**
   * 訂閱初始化（§2.6 / D5）：不彈權限詢問、以 getSubscription() 為唯一真相（F-20/F-26）；
   * 非 secure context（file://）→ 整個訂閱區維持 hidden（E14 / F-24）。fire-and-forget，不延後首繪。
   */
  async function initPwaPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) ||
        !/^https?:$/.test(location.protocol)) return;        // E14：file:// 降級
    try {
      // ① 抓 VAPID 公鑰（E3：失敗 → 停用＋「提醒功能暫時不可用」，其餘功能正常）
      vapidKey = await Pwa.fetchVapidPublicKey();
      // ② 還原三態（F-20/F-26；不重複訂閱、不彈權限詢問）
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      renderSubUI(Notification.permission, sub);
    } catch (e) {
      /* 降級：訂閱區維持 hidden（不影響其餘功能） */
    }
  }

  subBtn.addEventListener('click', async () => {
    // user gesture：訂閱或退訂（D5 唯一 requestPermission 入口；F-06）
    // ① 立即回饋：先渲染「處理中…」loading（async 鏈需 1~2s；不讓按鈕無反應）
    renderSubUI(Notification.permission, null, { state: 'loading' });
    try {
      const reg = await navigator.serviceWorker.ready;
      const existing = await reg.pushManager.getSubscription();
      let flow = null;
      if (existing) flow = await Pwa.unsubscribeFlow({ getRegistration: async () => reg });   // P2-C 退訂
      else flow = await Pwa.subscribeFlow({ vapidKey, getRegistration: async () => reg });     // P2-A 訂閱
      const sub = await reg.pushManager.getSubscription();
      renderSubUI(Notification.permission, sub, flow);
    } catch (e) {
      // 防呆：流程拋錯 → 以自然狀態渲染（不顯示錯誤卡）
      renderSubUI(Notification.permission, null);
    }
  });

  // ═══════════════ 應用狀態（單一來源） ═══════════════
  const state = {
    route: CONFIG.ROUTES[0].id,   // 預設東京 TPE-NRT（F-14）
    flight: 'all',                // 'all' = 每週最低價主線；否則航班號（如 'JX 800'）
    range: '3m',                  // 預設 3 個月（與 mockup 一致）
    loading: false,
  };

  let INDEX = null;               // index 結果（全域；供 loadRoute 篩 trips）
  let loadToken = 0;              // 競態防護：快速切航線只套用最新請求（F-21 / F-25）
  const abortCtl = new AbortController(); // 頁面卸載中止 fetch（F-22）
  const routeCache = new Map();   // Map<routeId, Week[]>（F-13）

  // ── 離線快取狀態（§2.3.1；cache.js 提供 OfflineCache）──
  const CACHE_VERSION = 1;                          // 與 cache.js DB_VERSION 連動（D7）；bump → 全量重同步
  const cacheStore = window.OfflineCache.createIdbStorage();  // 瀏覽器 IDB adapter（E8 失敗由 loadCache 拋錯降級）
  let CACHE = null;             // { meta, units } 記憶體投影（app.js 與圖表之間唯一快取視圖）
  let syncing = false;          // 同步進行中旗標（防並行增量同步，F-27）
  // 同步狀態機（§5.3）：'idle'|'first'|'offline'|'comparing'|'syncing'|'fresh'|'stale'|'compare_failed'|'partial'
  let syncState = 'idle';
  let retryTimer = null;              // E3：背景比對失敗 → 30s 排程重試 timer
  const RETRY_MS = 30_000;            // E3 / F-27：30 秒後自動重試背景比對

  // ═══════════════ 資料層（§2.4） ═══════════════
  const API_ROOT = new URL('../', document.baseURI);

  /**
   * 抓 index 並記錄 ETag（維持 cache:'no-cache' 強制重新驗證，D6 / §3.1）。
   * 既有 fetchIndex 的 shape 驗證 + 錯誤碼（ERR_NETWORK / ERR_INDEX_FETCH / ERR_INDEX_INVALID）不變。
   * @returns {Promise<{json: object, etag: string|null}>}
   */
  async function fetchIndexWithEtag() {
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
    return { json, etag: res.headers.get('etag') };
  }

  /**
   * 條件式 GET 單一 unit（D4）：本地有 etag → 送 If-None-Match；首次無本地 etag → 不帶。
   * 304/404 不讀 body（S4：統一以 status 判斷）；5xx 回傳 status（呼叫端轉 failed，E4）；
   * 網路錯誤向上拋（由 Promise pool 轉 failed，E4）。
   * @returns {Promise<{status: 200|304|404|number, json?: object|null, etag?: string|null}>}
   */
  async function fetchUnit(url, { etag, signal }) {
    const headers = {};
    if (etag) headers['If-None-Match'] = etag;      // 條件式請求（S1：Pages 回 304）
    let res;
    try {
      res = await fetch(new URL(url, API_ROOT), { headers, signal });
    } catch (e) {
      const err = new Error('網路層錯誤');
      err.code = 'ERR_NETWORK';
      throw err;
    }
    if (res.status === 304) return { status: 304, json: null, etag: null };   // 零 body 不讀
    if (res.status === 404) return { status: 404, json: null, etag: null };   // E6：以伺服器為準
    if (!res.ok) return { status: res.status, json: null, etag: null };       // 5xx → failed（E4）
    const json = await res.json();
    return { status: 200, json, etag: res.headers.get('etag') };
  }

  /**
   * 並行條件式 GET units：Promise pool（同時最多 CONCURRENCY，沿用既有）。
   * 逐筆失敗不拋錯 → 該筆 {status:'failed'}（E4：保留舊版）；同 URL 重複出現共用結果（與既有 fetchTrips 同）。
   * @returns {Promise<Array<{url: string, status: 200|304|404|'failed', json?: object|null, etag?: string|null}>>}
   */
  async function fetchUnitsConditional(urls, cachedUnits, onProgress, signal) {
    const results = new Array(urls.length);
    const dedupe = new Map();   // Map<url, result>
    let cursor = 0;
    async function worker() {
      while (cursor < urls.length) {
        const i = cursor++;
        const url = urls[i];
        let rec;
        if (dedupe.has(url)) {
          rec = dedupe.get(url);
        } else {
          const local = (cachedUnits && cachedUnits[url]) || {};
          try {
            const r = await fetchUnit(url, { etag: local.etag || null, signal });
            rec = { url, status: r.status, json: r.json, etag: r.etag };
          } catch (e) {
            rec = { url, status: 'failed', json: null, etag: null };   // E4：不拋錯，佔位
          }
          dedupe.set(url, rec);
        }
        results[i] = rec;
        if (onProgress) onProgress(i + 1, urls.length);
      }
    }
    const workers = Array.from({ length: Math.min(CONFIG.CONCURRENCY, urls.length) }, worker);
    await Promise.all(workers);
    return results;
  }

  /** 單筆結果 → 聚合用 json：200 用新內容；304 用本地快取內容；其餘（failed/404/5xx）null */
  function jsonForResult(r) {
    if (r.status === 200) return r.json;
    if (r.status === 304 && CACHE && CACHE.units[r.url]) return CACHE.units[r.url].json;
    return null;
  }

  /** routeId → unit URL 清單：以 INDEX.trips 快照篩；快照缺時回退 CACHE.units 鍵 */
  function unitsUrlsForRoute(routeId) {
    const pat = '/' + routeId + '/';
    let urls = INDEX.trips.filter(t => t.includes(pat));
    if (urls.length === 0 && CACHE) urls = Object.keys(CACHE.units).filter(u => u.includes(pat));
    return urls;
  }

  /** 以 IDB units 聚合某航線（零網路；離線可看範圍 = 已快取航線，EC1） */
  function weeksFromCache(routeId) {
    const urls = unitsUrlsForRoute(routeId);
    return aggregateWeekly(urls, urls.map(u => (CACHE.units[u] || {}).json || null));
  }

  /** 寫回 IDB：QuotaExceededError → quotaDegrade 只保留目前航線（E5）；其他錯誤 → 靜默降級記憶體快取（E8） */
  async function persistCacheSafe() {
    try {
      await OfflineCache.saveCache(cacheStore, CACHE.meta, CACHE.units);
    } catch (e) {
      if (e && e.name === 'QuotaExceededError') {
        const deg = OfflineCache.quotaDegrade(CACHE.units, CACHE.meta, state.route);
        CACHE.units = deg.units;
        CACHE.meta = deg.meta;
        try {
          await OfflineCache.saveCache(cacheStore, CACHE.meta, CACHE.units);
        } catch (e2) { /* 仍失敗 → 維持記憶體快取 */ }
      }
      // 其餘（IDB 不可用，E8）→ 不拋，頁面以記憶體快取照常運作
    }
  }

  /** 同步狀態文字（fresh / compare_failed / partial，§2.3.6）：text 為 null → 隱藏 */
  function setSyncStatus(text, warn) {
    syncStatus.hidden = !text;
    syncStatus.textContent = text || '';
    syncStatus.classList.toggle('warn', !!warn);
  }

  /** 手動更新按鈕狀態（E7 / F-16）：disabled + 文字；忙碌時「更新中…」 */
  function setRefreshDisabled(disabled, label) {
    refreshBtn.disabled = disabled;
    refreshBtn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    if (label !== undefined) refreshBtn.textContent = label;
  }

  /**
   * E3：背景比對失敗 → compare_failed 持久化（F-26）+ UI「更新失敗，稍後自動重試」
   * + 30s 排程重試（F-27）；不中斷瀏覽與圖表操作。
   */
  function markCompareFailed() {
    syncState = 'compare_failed';
    if (CACHE) {
      CACHE.meta.lastError = 'compare_failed';
      persistCacheSafe();
    }
    setSyncStatus('更新失敗，稍後自動重試', true);
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {                 // E3：30s 後自動重試；仍失敗 → 再排程
      retryTimer = null;
      if (navigator.onLine) backgroundCompare();
      else enterOffline();                           // 已離線 → 轉離線狀態（橫幅 + 按鈕停用）
    }, RETRY_MS);
  }

  // ═══════════════ 離線狀態層（T4 / §2.3.4） ═══════════════

  /**
   * 進入離線：橫幅「離線模式 · 顯示上次資料（HH:MM）」；不發請求；手動更新停用（E7）；
   * 無快取（E1 首次即離線）→ 由錯誤卡負責，不重複顯示橫幅。
   */
  function enterOffline() {
    syncState = 'offline';
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }   // 中斷自動重試（重連後重新比對）
    setRefreshDisabled(true, '離線中，無法更新');                       // 按鈕 disabled + 文字（E7）
    syncStatus.hidden = true;                                           // 離線狀態由橫幅 + 更新時間負責
    if (!CACHE || !CACHE.meta || !CACHE.meta.syncedAt) return;          // 無快取 → E1 錯誤卡（不重複）
    offBar.textContent = '離線模式 · 顯示上次資料（' + formatLastUpdated(CACHE.meta.syncedAt) + '）';
    offBar.hidden = false;
  }

  /**
   * 重連：online 事件 → 關橫幅、恢復按鈕；
   * 若上次為 offline / compare_failed / partial → 自動重跑背景比對（不需重新整理，EC3 / F-27）。
   */
  function onOnline() {
    offBar.hidden = true;
    setRefreshDisabled(false, '手動更新');
    if (syncState === 'offline' || syncState === 'compare_failed' || syncState === 'partial') {
      backgroundCompare();
    }
  }
  window.addEventListener('online', onOnline);
  window.addEventListener('offline', enterOffline);

  /**
   * E2：離線切到從未載入航線 → 該 tab 顯示「此航線尚未下載，需連網」（~3s 淡出）+ 停留原航線。
   * 不切換、不出錯誤卡、不白屏、不發請求（§2.3.4 / F-21）。
   */
  function showRouteHint(tabBtn) {
    let hint = tabBtn.querySelector('.hint');
    if (!hint) {
      hint = document.createElement('span');
      hint.className = 'hint';
      hint.setAttribute('role', 'status');
      hint.setAttribute('aria-live', 'polite');
      tabBtn.appendChild(hint);
    }
    hint.textContent = '此航線尚未下載，需連網';
    hint.classList.remove('fade');
    clearTimeout(tabBtn._hintTimer);
    tabBtn._hintTimer = setTimeout(() => {
      hint.classList.add('fade');
      setTimeout(() => hint.remove(), 350);          // fade 動畫結束後移除 DOM
    }, 3000);
  }

  /**
   * 手動更新（T5 / D6 / E7）：連網才可用（離線停用，BR3）；無快取（E1 後）→ 重跑首次載入。
   * force=true → 強制 no-cache 抓 index + 完整增量同步（不受自動比對結果影響）。
   * syncing 旗標與忙碌狀態由 incrementalSync 自管（F-27 防並行；避免自擋）。
   */
  async function manualUpdate() {
    if (syncing || state.loading || !navigator.onLine) return;   // E7 離線停用；F-27 防並行
    if (!CACHE) { await firstLoad(); return; }                   // E1 錯誤卡後的重試路徑
    await incrementalSync({ force: true });
  }
  refreshBtn.addEventListener('click', manualUpdate);

  /** updText →「資料更新 YYYY-MM-DD · 每週五更新」（INDEX 為最新 index） */
  function setUpdTextFromIndex() {
    updText.textContent = '資料更新 ' + formatGeneratedAt(INDEX.generated_at) + ' · 每週五更新';
  }

  /**
   * 同步完成後重建記憶體 routeCache：只重算「資料有變更」的航線（affected），
   * 目前航線未受影響 → 不重繪（F-25：同步不覆寫使用者正在看的視圖）。
   */
  function refreshRouteCache(affected) {
    const map = OfflineCache.routeUnitsFromIndex(INDEX);
    const currentAffected = (map[state.route] || []).some(u => affected.has(u));
    for (const [routeId, urls] of Object.entries(map)) {
      if (urls.some(u => affected.has(u))) {
        routeCache.set(routeId, aggregateWeekly(urls, urls.map(u => (CACHE.units[u] || {}).json || null)));
      }
    }
    // 航線整個離開 index 清單 → 自記憶體快取移除
    for (const routeId of Array.from(routeCache.keys())) {
      if (!map[routeId]) routeCache.delete(routeId);
    }
    if (currentAffected) {
      renderFlightSel(routeCache.get(state.route) || []);
      setUpdTextFromIndex();
      buildChart();
    }
  }

  /** 背景比對（情境 B/C）：抓 index → decideSync 三態分流（fresh = 0 個 trip 請求）；E3 失敗不中斷瀏覽 */
  async function backgroundCompare() {
    if (syncing) return;                 // 與手動更新互斥（F-27）
    syncState = 'comparing';
    try {
      const { json } = await fetchIndexWithEtag();
      await incrementalSync({ index: json });
    } catch (e) {
      markCompareFailed();               // E3：請求失敗即視為離線處理（EC2），不卡住
    }
  }

  /**
   * 增量同步（F-09 / D4）：
   *   decideSync 三態 → update：diffUnits → 條件式 GET 每個 unit（304 保留 / 200 覆寫+新 etag / 404 移除 / 失敗保留舊版）
   *   → mergeSyncResults → saveCache（meta.generatedAt / syncedAt / lastError / retryList）→ 記憶體 routeCache → 圖表更新。
   * @param {{force?: boolean, index?: object|null}} opts
   *   force=true（T5 手動更新呼叫）：強制 no-cache 重新抓 index 並跑完整增量，不受自動比對結果影響（D6 / BR3）；
   *   index 已由背景比對抓取 → 直接帶入避免重抓。
   */
  async function incrementalSync({ force = false, index = null } = {}) {
    if (syncing || !CACHE) return;       // F-27：防並行增量同步；無快取（E1 後）不增量
    syncing = true;
    syncState = 'syncing';
    setRefreshDisabled(true, '更新中…');  // 同步中按鈕忙碌狀態（§2.3.6 syncing）
    try {
      let json = index;
      if (!json) {
        const r = await fetchIndexWithEtag();   // force=true：強制重新驗證 index（D6）
        json = r.json;
      }
      const decision = OfflineCache.decideSync(CACHE.meta.generatedAt, json.generated_at);
      if (decision === 'fresh') {
        // 「已是最新」0 個 trip 請求（情境 B / E2E-07）；updText 改為資料更新日期（§2.3.6 fresh）
        syncState = 'fresh';
        updText.textContent = '資料更新 ' + formatGeneratedAt(json.generated_at) + ' · 每週五更新';
        setSyncStatus('已是最新');
        return;
      }
      if (decision === 'stale') {                                   // 伺服器較舊：保留本地新資料，不覆寫（D3）
        syncState = 'stale';
        showStale(CACHE.meta.generatedAt);
        return;
      }
      // update：增量同步（情境 C）
      INDEX = json;
      const map = OfflineCache.routeUnitsFromIndex(json);
      const allUrls = [];
      for (const urls of Object.values(map)) allUrls.push(...urls);
      const diff = OfflineCache.diffUnits(CACHE.units, allUrls);
      // removed：不在新清單 → 一律移除本地（E6 語意，40 週滑窗）
      let nextUnits = { ...CACHE.units };
      for (const url of diff.removed) {
        nextUnits = OfflineCache.applyUnitResult(nextUnits, url, 404).units;
      }
      // 待請求 = 新增 + 既有（帶本地 etag 條件式 GET）+ 上次失敗重試清單（E4 去重）
      const todo = Array.from(new Set([
        ...diff.added, ...diff.kept, ...((CACHE.meta && CACHE.meta.retryList) || []),
      ]));
      if (todo.length === 0) { syncState = 'fresh'; setSyncStatus('已是最新'); return; }
      progress.hidden = false;
      try {
        const results = await fetchUnitsConditional(todo, CACHE.units, (loaded, total) => {
          progress.textContent = '已載入 ' + loaded + '/' + total;
        }, abortCtl.signal);
        const merged = OfflineCache.mergeSyncResults(CACHE.meta, nextUnits, results, json.generated_at);
        if (diff.removed.length > 0) merged.meta.generatedAt = json.generated_at; // 移除亦是對齊新清單（避免下次重複重驗證）
        merged.meta.indexTrips = json.trips;                                     // 離線 unit 清單快照同步更新
        CACHE.meta = merged.meta;
        CACHE.units = merged.units;
        await persistCacheSafe();
        // 記憶體 routeCache 重建（只重算受影響航線；目前航線未變 → 不重繪，F-25）
        const affected = new Set(diff.removed);
        for (const r of results) if (r.status === 200 || r.status === 404) affected.add(r.url);
        refreshRouteCache(affected);
        syncState = merged.failed.length > 0 ? 'partial' : 'fresh';
        // E4：部分失敗 →「部分資料更新失敗」（成功者已更新）；全成功 →「已是最新」
        setSyncStatus(merged.failed.length > 0 ? '部分資料更新失敗' : '已是最新', merged.failed.length > 0);
      } finally {
        progress.hidden = true;
      }
    } catch (e) {
      markCompareFailed();               // E3：index 抓取失敗 → compare_failed（F-26）
    } finally {
      syncing = false;
      setRefreshDisabled(!navigator.onLine, navigator.onLine ? '手動更新' : '離線中，無法更新');
    }
  }

  /**
   * 載入整條航線：記憶體 routeCache → IDB 快取（hasCache，離線可用）→ 網路（條件式 GET + 寫回 IDB）；
   * 離線且無快取 → null（T4：tab「此航線尚未下載，需連網」提示兜底）。
   */
  async function loadRoute(routeId, onProgress) {
    if (routeCache.has(routeId)) return routeCache.get(routeId);   // F-13 命中快取
    // IDB 快取命中 → 直接聚合繪圖（F-14 / EC1：離線也可完整操作，不需網路）
    if (CACHE && OfflineCache.hasCache(CACHE.units, CACHE.meta, routeId)) {
      const weeks = weeksFromCache(routeId);
      routeCache.set(routeId, weeks);
      return weeks;
    }
    // 離線且無快取 → 不發任何請求
    if (!navigator.onLine) return null;
    const token = ++loadToken;            // 競態防護：快速切航線只套用最新請求（F-21 / F-25）
    // 依 index.trips 路徑中的航線目錄篩該航線 URL（值為 'api/trips/TPE-NRT/...'，航線在路徑段）
    const urls = INDEX.trips.filter(t => t.includes('/' + routeId + '/'));
    const fetched = await fetchUnitsConditional(urls, CACHE ? CACHE.units : {}, onProgress, abortCtl.signal);
    if (token !== loadToken) return null; // 過期回應丟棄（F-21）
    const weeks = aggregateWeekly(urls, fetched.map(jsonForResult));
    routeCache.set(routeId, weeks);
    // 寫回 IDB（連網切未載入航線 → 載入並寫入快取，F-23 / E2E-05）
    if (CACHE && fetched.some(r => r.status === 200)) {
      for (const r of fetched) {
        if (r.status === 200) CACHE.units[r.url] = { etag: r.etag || null, json: r.json };
      }
      CACHE.meta.routeLoadedAt = { ...(CACHE.meta.routeLoadedAt || {}), [routeId]: new Date().toISOString() };
      await persistCacheSafe();
    }
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
      // E2：離線切到從未載入航線 → tab 提示 + 停留原航線（不切換、不發請求、不出錯誤卡）
      if (!navigator.onLine && !OfflineCache.hasCache(CACHE ? CACHE.units : {}, CACHE ? CACHE.meta : null, b.dataset.route)) {
        showRouteHint(b);
        return;
      }
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
      ERR_OFFLINE_FIRST: ['需要網路才能首次載入資料', '請連網後點「重試」'],   // E1（T4）
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

  /** 首次載入（情境 A / E1）：全量載入預設航線 + 寫快取（meta 為 commit 點，§2.3.2） */
  async function firstLoad() {
    setLoading(true);
    try {
      const { json } = await fetchIndexWithEtag();
      INDEX = json;
      // 過舊警示 + 更新時間（F-10 / F-15）
      setUpdTextFromIndex();
      if (isStale(INDEX.generated_at)) showStale(INDEX.generated_at);
      // 空資料（E7）
      if (!INDEX.trips.length) {
        setLoading(false);
        emptyBox.hidden = false;
        setChartHidden(true);
        summary.hidden = true; // 無資料：Summary 三卡整區隱藏
        return;
      }
      // 全量載入預設航線（首次無本地 etag → 不帶 If-None-Match；收集 etag 供快取）
      const urls = INDEX.trips.filter(t => t.includes('/' + state.route + '/'));
      const fetched = await fetchUnitsConditional(urls, {}, (loaded, total) => {
        progress.textContent = '已載入 ' + loaded + '/' + total;
      }, abortCtl.signal);
      const weeks = aggregateWeekly(urls, fetched.map(jsonForResult));
      routeCache.set(state.route, weeks);
      renderFlightSel(weeks);
      skeleton.hidden = true;
      setChartHidden(false);
      emptyBox.hidden = true;
      buildChart();
      setLoading(false);   // 先解除 loading（IDB 寫入不阻塞 UI，E2E-14b）
      // 寫快取（meta.generatedAt / syncedAt / indexTrips / routeLoadedAt）
      const units = {};
      for (const r of fetched) {
        if (r.status === 200) units[r.url] = { etag: r.etag || null, json: r.json };
      }
      CACHE = {
        meta: {
          version: CACHE_VERSION,
          generatedAt: INDEX.generated_at,
          syncedAt: new Date().toISOString(),
          indexTrips: INDEX.trips,
          routeLoadedAt: { [state.route]: new Date().toISOString() },
          lastError: null,
          retryList: [],
        },
        units,
      };
      if (Object.keys(units).length > 0) await persistCacheSafe();   // 全失敗 → 不寫快取（避免空快取毒化下次秒開）
      syncState = fetched.some(r => r.status !== 200) ? 'partial' : 'fresh';
    } catch (e) {
      setLoading(false);
      // E1：首次訪問即離線（無快取）→ 錯誤卡「需要網路才能首次載入資料」+ 重試（連網後點重試重跑首次載入）
      if (!navigator.onLine || e.code === 'ERR_NETWORK') showError('ERR_OFFLINE_FIRST');
      else showError(e.code || 'ERR_NETWORK');
      return;
    }
  }

  /** 快取優先繪圖（F-14 秒開）：以 IDB units 直接聚合目前航線，零網路、無骨架閃爍 */
  function drawCurrentRouteFromCache() {
    const weeks = weeksFromCache(state.route);
    routeCache.set(state.route, weeks);
    renderFlightSel(weeks);
    setChartHidden(false);
    emptyBox.hidden = true;
    buildChart();
  }

  async function init() {
    // 0. deep-link（T9 / §2.6，E2E 發現的真 bug 修正）：notificationclick 開啟 /web/?route=XXX 時聚焦該航線
    //    （BDD P2-B「點擊通知開啟對應航線」/ E10 / EC3 子路徑；參數無效 → 忽略維持預設航線）
    const routeParam = new URLSearchParams(location.search).get('route');
    const routeRequested = routeParam && CONFIG.ROUTES.some(r => r.id === routeParam) ? routeParam : null;
    // 0.1 註冊 SW（T6 / §2.3.2 步驟 0）：僅 http(s) 且支援時註冊（localhost 為 secure context）；
    //    失敗靜默降級（file:// / 不支援 → 純記憶體快取，頁面仍可用，§9.1 / E8）
    if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
      navigator.serviceWorker.register('sw.js').catch(() => {}); // 靜默，不影響功能
    }
    errBox.hidden = true;
    emptyBox.hidden = true;
    staleBar.hidden = true;
    offBar.hidden = true;              // 離線橫幅 / 同步狀態 / 按鈕重置（重試重跑 init 時）
    syncStatus.hidden = true;
    setRefreshDisabled(false, '手動更新');
    renderRouteTabs();
    renderRangeSeg();
    initControls();
    initPwaPush();   // fire-and-forget：訂閱 UI 初始化不延後首繪（§2.6；不彈權限詢問，D5）

    // 1. 來源檢查（E2）
    if (!originAllowed(location.origin)) {
      showError('ERR_ORIGIN_FORBIDDEN');
      return;
    }

    // 2. cache-first 啟動（T3）：讀 IDB 快取；失敗（E8 無痕 / IDB 不可用）→ 視同無快取走首次載入
    let cached = null;
    try {
      cached = await OfflineCache.loadCache(cacheStore);
    } catch (e) {
      cached = null; // E8：降級為既有記憶體 routeCache 行為（頁面仍可用）
    }
    // 2.1 deep-link 目標有效性（需快取與連線狀態）：離線且目標未快取 → 顯示 tab 提示並停留原航線（BDD E9）
    if (routeRequested) {
      const hasTarget = cached && OfflineCache.hasCache(cached.units, cached.meta, routeRequested);
      if (!navigator.onLine && !hasTarget) {
        state.route = CONFIG.ROUTES[0].id;                    // E9：停留原航線（預設東京）
      } else {
        state.route = routeRequested;                          // P2-B / E10：聚焦該航線
      }
      renderRouteTabs();
      if (!navigator.onLine && !hasTarget) {
        const tab = routeTabs.querySelector('button[data-route="' + routeRequested + '"]');
        if (tab) showRouteHint(tab);                          // 「此航線尚未下載，需連網」（render 後才 append，避免被重繪清除）
      }
    }
    if (!cached) {
      syncState = 'first';
      await firstLoad();
      return;
    }

    // 3. 有快取 → 立即以快取繪圖（秒開，情境 B/C/D）+「上次更新 HH:MM」
    CACHE = cached;
    INDEX = { trips: CACHE.meta.indexTrips || [] };   // 離線切航線時以快取快照篩 unit（D7）
    updText.textContent = '上次更新 ' + formatLastUpdated(CACHE.meta.syncedAt);
    drawCurrentRouteFromCache();

    // 4. 離線 → 離線橫幅 + 不發任何請求 + E2 降級（情境 D / §2.3.4）
    if (!navigator.onLine) {
      enterOffline();
      return;
    }

    // 5. 連網 → 背景比對（情境 B/C；E3 失敗 → compare_failed，不中斷瀏覽）
    backgroundCompare();
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
