/**
 * 離線功能 — 快取層（IndexedDB 薄 Promise 封裝 + 同步決策純函式）
 *
 * 職責：
 *  - 持久化：IndexedDB（meta / units 兩 store）薄封裝；storage adapter 可注入
 *    （瀏覽器用 createIdbStorage()；Node 單元測試注入 in-memory Map 假 adapter）
 *  - 決策：generated_at 比對 / 增量清單 / unit 結果合併 / 降級 / 容量估算等純函式
 *
 * UMD 匯出：瀏覽器掛全域 `OfflineCache`；Node 環境（node:test）走 module.exports。
 * 對照：docs/development/離線功能.md §2.2；docs/tech-decisions/離線功能-2026-08-15.md（D2/D3/D4/D7）
 * 測試：tests/unit/cache.test.js（F-01~F-28 純函式可測部分）
 */
(function (global, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    global.OfflineCache = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ════════════════════════════════════════════════════════════
  // 設定常數
  // ════════════════════════════════════════════════════════════
  const DB_NAME = 'airtickets-cache';
  const DB_VERSION = 1;              // 與 meta.version 連動；bump → 全量重同步（D7 / B1 相容）
  const META_KEY = 'meta';
  const UNIT_STORE = 'units';
  const QUOTA_BYTES = 4 * 1024 * 1024;   // 容量配額（約當位元組；EC4：全量 ~250KB 遠低於此）

  // ════════════════════════════════════════════════════════════
  // IndexedDB 薄 Promise 封裝（T1；僅瀏覽器路徑使用，Node 測試注入假 adapter）
  // ════════════════════════════════════════════════════════════

  let _dbPromise = null;   // module-level lazy open（createIdbStorage 共用）

  function idbOpen() {
    return new Promise((resolve, reject) => {
      if (typeof indexedDB === 'undefined') {
        reject(new Error('IndexedDB unavailable'));  // E8：無痕 / 不支援 → 呼叫端降級
        return;
      }
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(META_KEY)) db.createObjectStore(META_KEY);
        if (!db.objectStoreNames.contains(UNIT_STORE)) db.createObjectStore(UNIT_STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error('IndexedDB blocked'));
    });
  }

  function db() {
    if (!_dbPromise) _dbPromise = idbOpen();
    return _dbPromise;
  }

  function idbGet(store, key) {
    return db().then(d => new Promise((resolve, reject) => {
      const req = d.transaction(store, 'readonly').objectStore(store).get(key);
      req.onsuccess = () => resolve(req.result === undefined ? null : req.result);
      req.onerror = () => reject(req.error);
    }));
  }

  function idbPut(store, key, val) {
    return db().then(d => new Promise((resolve, reject) => {
      const tx = d.transaction(store, 'readwrite');
      tx.objectStore(store).put(val, key);   // QuotaExceededError → tx abort → reject（E5 由呼叫端處理）
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('IDB transaction aborted'));
    }));
  }

  function idbDelete(store, key) {
    return db().then(d => new Promise((resolve, reject) => {
      const tx = d.transaction(store, 'readwrite');
      tx.objectStore(store).delete(key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    }));
  }

  function idbClear(store) {
    return db().then(d => new Promise((resolve, reject) => {
      const tx = d.transaction(store, 'readwrite');
      tx.objectStore(store).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    }));
  }

  function idbKeys(store) {
    return db().then(d => new Promise((resolve, reject) => {
      const req = d.transaction(store, 'readonly').objectStore(store).getAllKeys();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }));
  }

  /** 鍵空間只有 'meta' / 'units'；store 名與記錄鍵同名（'meta' → store meta、'units' → store units） */
  function storeFor(key) {
    return key === META_KEY ? META_KEY : UNIT_STORE;
  }

  /**
   * storage adapter 介面（可注入；Node 測試以 in-memory Map 同介面注入）：
   *   { async get(key): any|null, async set(key, val), async delete(key),
   *     async clear(), async keys(): string[] }
   * 鍵空間：'meta' → meta store；'units' → units store（整包 units map，邏輯上以 unitUrl 為鍵，見 §2.2.1）。
   * IDB 開啟失敗（無痕 / 私有模式 / 不支援）→ 拋錯向上，由 app.js 降級記憶體快取（E8）。
   */
  function createIdbStorage() {
    return {
      async get(key) { return idbGet(storeFor(key), key); },
      async set(key, val) { return idbPut(storeFor(key), key, val); },
      async delete(key) { return idbDelete(storeFor(key), key); },
      async clear() { await idbClear(META_KEY); await idbClear(UNIT_STORE); },
      async keys() {
        const metaKeys = (await idbKeys(META_KEY)).map(() => META_KEY);
        const unitKeys = (await idbKeys(UNIT_STORE)).map(() => UNIT_STORE);
        return [...metaKeys, ...unitKeys];
      },
    };
  }

  // ════════════════════════════════════════════════════════════
  // 高階快取 API（app.js 使用）
  // ════════════════════════════════════════════════════════════

  /**
   * 讀取整份快取。
   * @param {object} storage - 注入的 storage adapter
   * @returns {Promise<{meta: object, units: Object<string, {etag: string|null, json: object}>}|null>}
   *   null = 無快取（首次訪問 / E8 無痕 / 版本不符 → 全量重同步）；IDB 開啟失敗 → 拋錯（app.js 降級記憶體快取）
   */
  async function loadCache(storage) {
    const meta = await storage.get(META_KEY);
    if (!meta || meta.version !== DB_VERSION) return null;   // 版本不符視同無快取（D7）
    const units = await storage.get(UNIT_STORE);
    return { meta, units: units || {} };
  }

  /**
   * 寫入整份快取（先寫 units 再寫 meta，meta 為 commit 點——meta 寫失敗時 loadCache 回 null → 下次全量重同步）。
   * QuotaExceededError → 向上拋（不吞），由 app.js 轉 quotaDegrade（E5）。
   * @returns {Promise<{status: 'ok'}>}
   */
  async function saveCache(storage, meta, units) {
    await storage.set(UNIT_STORE, units);
    await storage.set(META_KEY, meta);
    return { status: 'ok' };
  }

  // ════════════════════════════════════════════════════════════
  // 同步決策純函式（T2，全部 node --test 覆蓋）
  // ════════════════════════════════════════════════════════════

  /**
   * 比對 generated_at（D3，唯一比對基準）：
   *   Date.parse 兩者 → 相等 'fresh'（「已是最新」0 請求）/
   *   伺服器較新 'update'（增量同步）/ 伺服器較舊 'stale'（「資料可能過時」警示，不覆寫本地新資料）。
   *   任一方解析失敗 → 視為 'update'（寧可多抓不可漏更）。
   * @param {string|null} cachedGeneratedAt - 本地 meta.generatedAt
   * @param {string|null} serverGeneratedAt - 伺服器 index.generated_at
   * @returns {'fresh'|'update'|'stale'}
   */
  function decideSync(cachedGeneratedAt, serverGeneratedAt) {
    const a = Date.parse(cachedGeneratedAt);
    const b = Date.parse(serverGeneratedAt);
    if (Number.isNaN(a) || Number.isNaN(b)) return 'update';
    if (a === b) return 'fresh';
    return b > a ? 'update' : 'stale';
  }

  /**
   * 增量清單（D4 / 40 週滑窗）：以 URL 集合差計算。
   * @param {Object<string, object>} cachedUnits - 本地快取 units（key = unitUrl）
   * @param {string[]} serverUnitList - 伺服器最新 unit URL 清單
   * @returns {{added: string[], kept: string[], removed: string[]}}
   *   added/kept 依伺服器清單順序；removed = 本地有但不在伺服器清單 → 一律移除（E6 語意）
   */
  function diffUnits(cachedUnits, serverUnitList) {
    const cached = cachedUnits || {};
    const list = Array.isArray(serverUnitList) ? serverUnitList : [];
    const added = [];
    const kept = [];
    for (const url of list) {
      if (Object.prototype.hasOwnProperty.call(cached, url)) kept.push(url);
      else added.push(url);
    }
    const removed = Object.keys(cached).filter(url => !list.includes(url));
    return { added, kept, removed };
  }

  /**
   * 單一 unit 的條件式 GET 結果套用（D4）：
   *   200 → 覆寫 json + 新 etag；304 → 保留（零 body）；404 → 刪除（E6）；
   *   網路錯誤 / HTTP 異常（非 200/304/404）→ 保留舊版並回傳 failed（E4）。
   * 純函式：回傳新 units 物件，不改輸入。
   * @param {Object<string, object>} units
   * @param {string} url - unitUrl
   * @param {number|string} status - 200 | 304 | 404 | 'failed'（或任何非 200/304/404 數值）
   * @param {object|null} json - status=200 時的新內容
   * @param {string|null} etag - status=200 時伺服器回傳的 ETag
   * @returns {{units: object, status: 'updated'|'kept'|'removed'|'failed'}}
   */
  function applyUnitResult(units, url, status, json, etag) {
    const next = { ...(units || {}) };
    if (status === 200) {
      next[url] = { etag: etag || null, json };
      return { units: next, status: 'updated' };
    }
    if (status === 304) {
      return { units: next, status: 'kept' };   // 保留本地（零 body 不讀）
    }
    if (status === 404) {
      delete next[url];                          // E6：以伺服器為準
      return { units: next, status: 'removed' };
    }
    return { units: next, status: 'failed' };    // 網路錯誤 / 5xx → 保留舊版（E4）
  }

  /**
   * 增量同步合併（F-08 / F-19 / F-20）：
   *   依序 applyUnitResult → 更新 meta.generatedAt / syncedAt（有任一 updated 才推進 generatedAt）/
   *   lastError（部分失敗 → 'partial'；乾淨同步 → null）/ retryList（failed URL 清單，
   *   下次與增量清單合併去重；本次已成功 / 已移除的舊重試 URL 自清單移除）。
   * @param {object} meta - 本地 meta（可能含既有 retryList / lastError）
   * @param {Object<string, object>} units - 本地 units
   * @param {Array<{url: string, status: 200|304|404|'failed', json?: object, etag?: string|null}>} results
   * @param {string} serverGeneratedAt - 本次 index 的 generated_at
   * @returns {{meta: object, units: object, failed: string[]}}
   */
  function mergeSyncResults(meta, units, results, serverGeneratedAt) {
    let nextUnits = { ...(units || {}) };
    const retry = new Set((meta && meta.retryList) || []);
    const failed = [];
    let anyUpdated = false;
    for (const r of results || []) {
      const applied = applyUnitResult(nextUnits, r.url, r.status, r.json, r.etag);
      nextUnits = applied.units;
      if (applied.status === 'updated') { anyUpdated = true; retry.delete(r.url); }
      else if (applied.status === 'kept' || applied.status === 'removed') { retry.delete(r.url); }
      else failed.push(r.url);   // failed：保留舊版 + 記入重試清單（E4）
    }
    const nextMeta = {
      ...(meta || {}),
      generatedAt: anyUpdated ? serverGeneratedAt : (meta ? meta.generatedAt : null),
      syncedAt: new Date().toISOString(),        // 最後一次成功同步時間
      lastError: failed.length > 0 ? 'partial' : null,
      retryList: Array.from(new Set([...retry, ...failed])),
    };
    return { meta: nextMeta, units: nextUnits, failed };
  }

  /**
   * index → unit 清單映射（D7，唯一映射點）：
   *   今天：index.trips 依路徑段 '/{route}/' 篩出各航線 trip URL；
   *   B1 後：index.routes → api/routes/{route}.json（新增變體 + bump version → 全量重同步）。
   *   index 無 routes 欄位時由 trips URL 路徑段推導航線清單。
   * @param {object} index - api/index.json
   * @returns {Object<string, string[]>} { [routeId]: [unitUrl...] }
   */
  function routeUnitsFromIndex(index) {
    const result = {};
    if (!index) return result;
    if (Array.isArray(index.trips)) {
      const routeIds = Array.isArray(index.routes)
        ? index.routes
        : Array.from(new Set(index.trips.map(u => String(u).split('/')[2]).filter(Boolean)));
      for (const routeId of routeIds) {
        const pat = '/' + routeId + '/';
        result[routeId] = index.trips.filter(u => String(u).includes(pat));
      }
    } else if (Array.isArray(index.routes)) {
      // B1 變體：route 合併檔
      for (const routeId of index.routes) {
        result[routeId] = ['api/routes/' + routeId + '.json'];
      }
    }
    return result;
  }

  /**
   * 空間不足降級（E5，F-12）：只保留 keepRoute 的 units 與 routeLoadedAt，
   * 捨棄其他航線；保留 meta.generatedAt 與其餘 meta 欄位。回傳新的 { units, meta }（不丟例外）。
   * @param {Object<string, object>} units
   * @param {object} meta
   * @param {string} keepRoute - 目前航線 routeId（如 'TPE-NRT'）
   * @returns {{units: object, meta: object}}
   */
  function quotaDegrade(units, meta, keepRoute) {
    const nextUnits = {};
    const pat = '/' + keepRoute + '/';
    for (const [url, rec] of Object.entries(units || {})) {
      if (String(url).includes(pat)) nextUnits[url] = rec;
    }
    const routeLoadedAt = (meta && meta.routeLoadedAt && meta.routeLoadedAt[keepRoute])
      ? { [keepRoute]: meta.routeLoadedAt[keepRoute] }
      : {};
    return { units: nextUnits, meta: { ...(meta || {}), routeLoadedAt } };
  }

  /**
   * 容量估算（F-17 / F-28）：units 全部記錄（{etag, json}）JSON 字串長度和（約當位元組）。
   * 空 → 0；超過配額常數 QUOTA_BYTES（4MB）→ overQuota=true 供 UI 警示 / 降級。
   * @param {Object<string, object>|null} units
   * @returns {{bytes: number, overQuota: boolean}}
   */
  function estimateSize(units) {
    let bytes = 0;
    for (const url of Object.keys(units || {})) {
      bytes += JSON.stringify(units[url]).length;
    }
    return { bytes, overQuota: bytes > QUOTA_BYTES };
  }

  /**
   * 離線判定（@edge-case 連網誤判，F-13）：requestOk === false → 一律離線（請求結果優先）；
   * 否則以 navigator.onLine 為準。
   * @param {boolean} navigatorOnLine - navigator.onLine
   * @param {boolean} [requestOk=true] - 實際請求是否成功
   * @returns {boolean} 是否連網
   */
  function resolveOnlineStatus(navigatorOnLine, requestOk = true) {
    if (requestOk === false) return false;
    return !!navigatorOnLine;
  }

  /**
   * 航線是否已快取（F-21 / F-22）：units 中存在該 routeId 的 unit 且 meta.routeLoadedAt 有記錄 → true。
   * 離線切航線降級（E2）與已快取航線直接顯示（EC1）皆以此為準。
   * @param {Object<string, object>} units
   * @param {object} meta
   * @param {string} routeId
   * @returns {boolean}
   */
  function hasCache(units, meta, routeId) {
    if (!meta || !meta.routeLoadedAt || !meta.routeLoadedAt[routeId]) return false;
    const pat = '/' + routeId + '/';
    return Object.keys(units || {}).some(url => String(url).includes(pat));
  }

  return {
    createIdbStorage,
    loadCache,
    saveCache,
    decideSync,
    diffUnits,
    applyUnitResult,
    mergeSyncResults,
    routeUnitsFromIndex,
    quotaDegrade,
    estimateSize,
    resolveOnlineStatus,
    hasCache,
  };
});
