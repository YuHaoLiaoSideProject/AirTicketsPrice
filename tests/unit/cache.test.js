// 離線功能 — 快取層單元測試（TDD 紅燈階段）
// 對照：docs/test-plans/離線功能測試計畫.md F-01~F-28（純函式可測部分）
//       docs/development/離線功能.md §2.2（函式簽名以開發規格為準；命名對照表見 §2.2.3）
// 執行：node --test tests/unit/cache.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const Cache = require('../../web/cache.js');

// ── Fixtures（結構對照 api/index.json 與 api/trips/*.json 真實格式）──
const GEN = '2026-08-14T22:46:53.000Z';        // 本地快取 generated_at（真實 index 快照）
const SERVER_GEN = '2026-08-21T22:46:53.000Z'; // 伺服器較新版（每週五爬蟲後）
const T0 = '2026-08-14T23:00:00.000Z';
const T1 = '2026-08-15T10:00:00.000Z';

const UNIT_NRT = 'api/trips/TPE-NRT/2026-08-15_2026-08-23.json';
const UNIT_NRT2 = 'api/trips/TPE-NRT/2026-08-22_2026-08-30.json';
const UNIT_KIX = 'api/trips/TPE-KIX/2026-08-15_2026-08-23.json';
const UNIT_FUK = 'api/trips/TPE-FUK/2026-08-15_2026-08-23.json';

const mkMeta = (over = {}) => ({
  version: 1,
  generatedAt: GEN,
  syncedAt: T0,
  indexTrips: [UNIT_NRT, UNIT_NRT2, UNIT_KIX],
  routeLoadedAt: { 'TPE-NRT': T0 },
  lastError: null,
  retryList: [],
  ...over,
});

const mkUnits = () => ({
  [UNIT_NRT]: { etag: '"nrt1"', json: { route_id: 'TPE-NRT', outbound_date: '2026-08-15', return_date: '2026-08-23', flights: [{ no: 'JX 800', price: 32296 }] } },
  [UNIT_NRT2]: { etag: '"nrt2"', json: { route_id: 'TPE-NRT', outbound_date: '2026-08-22', return_date: '2026-08-30', flights: [] } },
  [UNIT_KIX]: { etag: '"kix1"', json: { route_id: 'TPE-KIX', outbound_date: '2026-08-15', return_date: '2026-08-23', flights: [] } },
});

// ── 測試用 storage adapter（in-memory Map，對應規格 §2.2.2 可注入介面）──
function mkAdapter(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    async get(key) { return map.has(key) ? map.get(key) : null; },
    async set(key, val) { map.set(key, val); },
    async delete(key) { map.delete(key); },
    async clear() { map.clear(); },
    async keys() { return [...map.keys()]; },
  };
}

/** 寫入拋 QuotaExceededError 的 adapter（F-11 / E5） */
function mkQuotaAdapter() {
  return {
    async get() { return null; },
    async set() { throw new DOMException('quota exceeded', 'QuotaExceededError'); },
    async delete() {},
    async clear() {},
    async keys() { return []; },
  };
}

// ── F-01 ~ F-03 快取持久化（saveCache / loadCache）──
test('F-01 saveCache 首次載入寫入 meta + units（meta 為 commit 點）', async () => {
  const adapter = mkAdapter();
  const m = mkMeta();
  const u = mkUnits();
  const res = await Cache.saveCache(adapter, m, u);
  assert.deepEqual(res, { status: 'ok' });
  assert.deepEqual(await adapter.get('meta'), m);
  assert.deepEqual(await adapter.get('units'), u);
});

test('F-01b saveCache 先寫 units 再寫 meta：meta 寫失敗 → 快取不可讀（下次全量重同步）', async () => {
  const calls = [];
  const adapter = {
    async get() { return null; },
    async set(key) {
      calls.push(key);
      if (key === 'meta') throw new Error('meta write failed');
    },
    async delete() {}, async clear() {}, async keys() { return []; },
  };
  await assert.rejects(() => Cache.saveCache(adapter, mkMeta(), mkUnits()), /meta write failed/);
  assert.deepEqual(calls, ['units', 'meta']);           // units 先寫、meta 為 commit 點
  assert.equal(await Cache.loadCache(adapter), null);   // 無 meta → 視同首次訪問
});

test('F-02 loadCache 回傳 { meta, units } 可供立即繪圖（秒開）', async () => {
  const adapter = mkAdapter({ meta: mkMeta(), units: mkUnits() });
  const cached = await Cache.loadCache(adapter);
  assert.ok(cached);
  assert.equal(cached.meta.generatedAt, GEN);
  assert.equal(cached.meta.routeLoadedAt['TPE-NRT'], T0);
  assert.deepEqual(cached.units[UNIT_NRT], mkUnits()[UNIT_NRT]);
});

test('F-03 全新 adapter loadCache → null（首次訪問 / E8 無痕 = 需連網全量載入）', async () => {
  assert.equal(await Cache.loadCache(mkAdapter()), null);
});

test('F-03b 版本不符（meta.version ≠ DB_VERSION）→ null（D7：bump 即全量重同步）', async () => {
  const adapter = mkAdapter({ meta: mkMeta({ version: 2 }), units: mkUnits() });
  assert.equal(await Cache.loadCache(adapter), null);
});

// ── F-04 ~ F-06 generated_at 比對（decideSync，D3 唯一比對基準）──
test('F-04 decideSync 相同 generated_at → fresh（已是最新，0 trip 請求）', () => {
  assert.equal(Cache.decideSync(GEN, GEN), 'fresh');
  // 同刻不同字串格式（Date.parse 相等）
  assert.equal(Cache.decideSync('2026-08-14T22:46:53Z', '2026-08-14T22:46:53.000Z'), 'fresh');
});

test('F-05 decideSync 伺服器較新 → update（進入增量補載）', () => {
  assert.equal(Cache.decideSync(GEN, SERVER_GEN), 'update');
});

test('F-06 decideSync 伺服器較舊 → stale（資料可能過時，不覆寫本地新資料）', () => {
  assert.equal(Cache.decideSync(SERVER_GEN, GEN), 'stale');
});

test('F-06b decideSync 任一方無法解析 → update（寧可多抓不可漏更）', () => {
  assert.equal(Cache.decideSync(null, SERVER_GEN), 'update');
  assert.equal(Cache.decideSync(GEN, 'garbage'), 'update');
  assert.equal(Cache.decideSync(null, null), 'update');
});

// ── F-07 增量清單（diffUnits，URL 集合差）──
test('F-07 diffUnits 增量清單：只回傳新增 [D]，未變更者 kept、滑出者 removed', () => {
  const cached = { A: 1, B: 2, C: 3 };
  const { added, kept, removed } = Cache.diffUnits(cached, ['A', 'B', 'C', 'D']);
  assert.deepEqual(added, ['D']);
  assert.deepEqual(kept, ['A', 'B', 'C']);
  assert.deepEqual(removed, []);
});

test('F-07b diffUnits 伺服器清單缺檔 / 為空 → removed 回報（E6 語意，40 週滑窗）', () => {
  const cached = { A: 1, B: 2 };
  assert.deepEqual(Cache.diffUnits(cached, ['A']).removed, ['B']);
  assert.deepEqual(Cache.diffUnits(cached, []).removed, ['A', 'B']);
  assert.deepEqual(Cache.diffUnits(cached, undefined).removed, ['A', 'B']);
});

// ── F-08 / F-19 / F-20 條件式 GET 結果套用與合併 ──
test('applyUnitResult 200 → 覆寫 json + 新 etag（updated，不動其他 unit）', () => {
  const units = mkUnits();
  const { units: u2, status } = Cache.applyUnitResult(units, UNIT_NRT, 200, { fresh: 1 }, '"new-etag"');
  assert.equal(status, 'updated');
  assert.deepEqual(u2[UNIT_NRT], { etag: '"new-etag"', json: { fresh: 1 } });
  assert.deepEqual(u2[UNIT_KIX], units[UNIT_KIX]);   // 其他 unit 不動
  assert.equal(units[UNIT_NRT].etag, '"nrt1"');      // 原物件不被污染
});

test('applyUnitResult 304 → 保留（kept，零 body 不讀）', () => {
  const units = mkUnits();
  const { units: u2, status } = Cache.applyUnitResult(units, UNIT_NRT, 304, null, null);
  assert.equal(status, 'kept');
  assert.deepEqual(u2[UNIT_NRT], units[UNIT_NRT]);
});

test('applyUnitResult 404 → 刪除（removed，E6 以伺服器為準）', () => {
  const units = mkUnits();
  const { units: u2, status } = Cache.applyUnitResult(units, UNIT_KIX, 404, null, null);
  assert.equal(status, 'removed');
  assert.equal(u2[UNIT_KIX], undefined);
  assert.ok(u2[UNIT_NRT]);
});

test('applyUnitResult 網路錯誤 / 5xx → 保留舊版（failed，E4）', () => {
  const units = mkUnits();
  const { units: u2, status } = Cache.applyUnitResult(units, UNIT_NRT, 500, null, null);
  assert.equal(status, 'failed');
  assert.deepEqual(u2[UNIT_NRT], units[UNIT_NRT]);
});

test('F-08 mergeSyncResults 增量合併：200 覆寫 + 304 保留 + generatedAt/syncedAt 推進', () => {
  const meta = mkMeta();
  const units = mkUnits();
  const newJson = { route_id: 'TPE-NRT', outbound_date: '2026-08-15', return_date: '2026-08-23', flights: [], updated: true };
  const res = Cache.mergeSyncResults(meta, units, [
    { url: UNIT_NRT, status: 200, json: newJson, etag: '"new-etag"' },
    { url: UNIT_KIX, status: 304 },
  ], SERVER_GEN);
  assert.equal(res.units[UNIT_NRT].etag, '"new-etag"');
  assert.equal(res.units[UNIT_NRT].json.updated, true);
  assert.deepEqual(res.units[UNIT_KIX], units[UNIT_KIX]);   // 304 保留舊版
  assert.equal(res.meta.generatedAt, SERVER_GEN);           // 有任一 updated 才推進
  assert.ok(!Number.isNaN(Date.parse(res.meta.syncedAt)));  // syncedAt 更新為現在
  assert.equal(res.meta.lastError, null);
  assert.deepEqual(res.meta.retryList, []);
  assert.deepEqual(res.failed, []);
});

test('F-19 增量部分失敗：成功者先更新、失敗者保留舊版、lastError=partial、retryList 記錄', () => {
  const meta = mkMeta();
  const units = mkUnits();
  const res = Cache.mergeSyncResults(meta, units, [
    { url: UNIT_NRT, status: 200, json: { fresh: 2 }, etag: '"n2"' },
    { url: UNIT_KIX, status: 'failed' },   // 網路錯誤 / 5xx
  ], SERVER_GEN);
  assert.equal(res.units[UNIT_NRT].etag, '"n2"');
  assert.deepEqual(res.units[UNIT_KIX], units[UNIT_KIX]);   // 失敗者保留舊版
  assert.deepEqual(res.failed, [UNIT_KIX]);
  assert.equal(res.meta.lastError, 'partial');
  assert.deepEqual(res.meta.retryList, [UNIT_KIX]);
  assert.equal(res.meta.generatedAt, SERVER_GEN);           // 有任一 updated 才推進
});

test('F-19b 全部失敗 → generatedAt 不推進（有任一 updated 才推進）', () => {
  const meta = mkMeta();
  const res = Cache.mergeSyncResults(meta, mkUnits(), [
    { url: UNIT_NRT, status: 'failed' },
    { url: UNIT_KIX, status: 'failed' },
  ], SERVER_GEN);
  assert.equal(res.meta.generatedAt, GEN);                  // 維持本地版本
  assert.deepEqual(res.meta.retryList, [UNIT_NRT, UNIT_KIX]);
  assert.equal(res.meta.lastError, 'partial');
});

test('F-20 重試清單：下次與增量清單合併去重，只重試失敗檔 + 新增檔', () => {
  const units = mkUnits();
  const prevMeta = Cache.mergeSyncResults(mkMeta(), units, [
    { url: UNIT_KIX, status: 'failed' },
  ], SERVER_GEN).meta;
  assert.deepEqual(prevMeta.retryList, [UNIT_KIX]);          // 失敗檔記入 retryList
  const serverList = [UNIT_NRT, UNIT_NRT2, UNIT_KIX, UNIT_FUK]; // FUK 新增
  const diff = Cache.diffUnits(units, serverList);
  const nextLoad = [...new Set([...diff.added, ...prevMeta.retryList])];
  assert.deepEqual(nextLoad.sort(), [UNIT_KIX, UNIT_FUK].sort()); // 只重試失敗檔 + 新增檔，其餘不重抓
});

test('F-20b 重試檔下次成功 → 自 retryList 移除', () => {
  const meta = mkMeta({ retryList: [UNIT_KIX] });
  const res = Cache.mergeSyncResults(meta, mkUnits(), [
    { url: UNIT_KIX, status: 200, json: { fresh: 3 }, etag: '"k2"' },
    { url: UNIT_NRT, status: 304 },
  ], SERVER_GEN);
  assert.deepEqual(res.meta.retryList, []);
  assert.deepEqual(res.failed, []);
});

// ── F-10 / F-11 / F-12 快取單位、配額與降級 ──
test('F-10 快取以瀏覽器為單位：全新 adapter（瀏覽器 B / 無痕）→ null；載入一次後才有快取', async () => {
  const adapterA = mkAdapter();
  const adapterB = mkAdapter();                 // 獨立 storage，不共享
  await Cache.saveCache(adapterA, mkMeta(), mkUnits());
  assert.equal(await Cache.loadCache(adapterB), null);      // B 等同首次訪問需連網
  await Cache.saveCache(adapterB, mkMeta(), mkUnits());     // B 載入一次後
  assert.ok(await Cache.loadCache(adapterB));               // B 才具備快取（離線能力自二次訪問生效）
});

test('F-11 saveCache 遇 QuotaExceededError 向上拋（規格 §2.2.2；由 app.js 轉 quotaDegrade，E5）', async () => {
  await assert.rejects(
    () => Cache.saveCache(mkQuotaAdapter(), mkMeta(), mkUnits()),
    (err) => err && err.name === 'QuotaExceededError'
  );
});

test('F-12 quotaDegrade 只保留 keepRoute 的 units 與 routeLoadedAt；保留 generatedAt', () => {
  const units = mkUnits();   // TPE-NRT ×2 + TPE-KIX
  const meta = mkMeta({ routeLoadedAt: { 'TPE-NRT': T0, 'TPE-KIX': T1 } });
  const { units: u2, meta: m2 } = Cache.quotaDegrade(units, meta, 'TPE-KIX');
  assert.deepEqual(Object.keys(u2), [UNIT_KIX]);            // 只保留目前航線
  assert.equal(u2[UNIT_KIX], units[UNIT_KIX]);
  assert.deepEqual(Object.keys(m2.routeLoadedAt), ['TPE-KIX']);
  assert.equal(m2.generatedAt, GEN);                        // 保留 meta.generatedAt
  assert.equal(m2.version, 1);
});

// ── F-13 離線判定（連網誤判，請求結果優先）──
test('F-13 resolveOnlineStatus：requestOk=false 一律離線（優先於 onLine）；否則以 navigator.onLine 為準', () => {
  assert.equal(Cache.resolveOnlineStatus(true, false), false); // EC2：onLine=true 但請求失敗 → 離線
  assert.equal(Cache.resolveOnlineStatus(false, true), false); // 瀏覽器回報離線 → 離線
  assert.equal(Cache.resolveOnlineStatus(true, true), true);
  assert.equal(Cache.resolveOnlineStatus(true), true);         // 預設 requestOk=true
  assert.equal(Cache.resolveOnlineStatus(false), false);
});

// ── F-17 / F-28 容量估算（EC4）──
test('F-17 estimateSize 全量（159 檔 × 約 1.5KB）於配額內，且為各記錄 JSON 字串長度和', () => {
  const units = {};
  const payload = 'x'.repeat(1500);
  for (let i = 0; i < 159; i++) {
    units['api/trips/TPE-NRT/' + i + '.json'] = { etag: '"e' + i + '"', json: { payload } };
  }
  const { bytes, overQuota } = Cache.estimateSize(units);
  const expected = Object.values(units).reduce((s, r) => s + JSON.stringify(r).length, 0);
  assert.equal(bytes, expected);
  assert.ok(bytes > 100 * 1024, '全量約數百 KB 量級');
  assert.ok(bytes < 4 * 1024 * 1024, '遠低於配額（≳5MB 的正常瀏覽器配額）');
  assert.equal(overQuota, false);
});

test('F-28 estimateSize 邊界：空 → 0；極大（1000 檔 × 10KB）→ overQuota true（觸發降級訊號）', () => {
  assert.deepEqual(Cache.estimateSize({}), { bytes: 0, overQuota: false });
  assert.equal(Cache.estimateSize(null).bytes, 0);
  const units = {};
  for (let i = 0; i < 1000; i++) {
    units['api/trips/TPE-NRT/u' + i + '.json'] = { etag: null, json: { payload: 'y'.repeat(10 * 1024) } };
  }
  const r = Cache.estimateSize(units);
  assert.equal(r.overQuota, true);
  assert.ok(r.bytes > 4 * 1024 * 1024);
});

// ── F-18 伺服器檔案移除（E6）──
test('F-18 伺服器 404 → applyUnitResult removed 本地一併移除（不顯示錯誤卡）', () => {
  const units = mkUnits();
  const { units: u2, status } = Cache.applyUnitResult(units, UNIT_KIX, 404, null, null);
  assert.equal(status, 'removed');
  assert.equal(u2[UNIT_KIX], undefined);
});

test('F-18b diffUnits.removed：不在新 index.trips 清單 → 一律移除（40 週滑窗 / E6 語意）', () => {
  const units = mkUnits();
  const { removed } = Cache.diffUnits(units, [UNIT_NRT, UNIT_NRT2]);
  assert.deepEqual(removed, [UNIT_KIX]);
});

// ── F-21 / F-22 航線快取判定（E2 / EC1）──
test('F-21 hasCache 未載入航線：units 或 routeLoadedAt 缺一 → false（離線切 tab 需提示）', () => {
  const units = mkUnits();                          // 只有 NRT、KIX 的 units
  const meta = mkMeta({ routeLoadedAt: { 'TPE-NRT': T0 } }); // 只載入過 NRT
  assert.equal(Cache.hasCache(units, meta, 'TPE-KIX'), false); // 有 units 但 routeLoadedAt 無記錄
  assert.equal(Cache.hasCache(units, meta, 'TPE-FUK'), false); // 兩者皆無
  assert.equal(Cache.hasCache(units, meta, 'TPE-NRT'), true);
});

test('F-21b hasCache：routeLoadedAt 有記錄但 units 無該航線 → false', () => {
  const meta = mkMeta({ routeLoadedAt: { 'TPE-NRT': T0, 'TPE-FUK': T1 } });
  assert.equal(Cache.hasCache(mkUnits(), meta, 'TPE-FUK'), false);
});

test('F-22 hasCache 已快取雙航線 → 皆 true（離線切換仍可完整操作，EC1）', () => {
  const meta = mkMeta({ routeLoadedAt: { 'TPE-NRT': T0, 'TPE-KIX': T1 } });
  assert.equal(Cache.hasCache(mkUnits(), meta, 'TPE-NRT'), true);
  assert.equal(Cache.hasCache(mkUnits(), meta, 'TPE-KIX'), true);
});

// ── F-23 / F-24 / F-26（純函式部分；連網 / UI / 排程部分屬 T3/T4 整合層）──
test('F-23 連網切未載入航線（純函式部分）：saveCache 寫入新航線 units + routeLoadedAt 可回讀', async () => {
  const adapter = mkAdapter();
  await Cache.saveCache(adapter, mkMeta(), mkUnits());
  const m2 = mkMeta({ routeLoadedAt: { 'TPE-NRT': T0, 'TPE-FUK': T1 } });
  const u2 = {
    ...mkUnits(),
    [UNIT_FUK]: { etag: '"f1"', json: { route_id: 'TPE-FUK', outbound_date: '2026-08-15', return_date: '2026-08-23', flights: [] } },
  };
  await Cache.saveCache(adapter, m2, u2);
  const cached = await Cache.loadCache(adapter);
  assert.ok(cached.units[UNIT_FUK]);
  assert.equal(cached.meta.routeLoadedAt['TPE-FUK'], T1);
});

test('F-24 手動更新無新版（純函式部分）：decideSync=fresh + 空結果 → 資料維持原狀', () => {
  assert.equal(Cache.decideSync(GEN, GEN), 'fresh');
  const meta = mkMeta();
  const units = mkUnits();
  const res = Cache.mergeSyncResults(meta, units, [], GEN);
  assert.deepEqual(res.units, units);               // units 不變
  assert.equal(res.meta.generatedAt, GEN);          // generatedAt 不推進
  assert.equal(res.meta.lastError, null);
});

test('F-26 背景比對失敗標記持久化（純函式部分）：lastError=compare_failed 經 saveCache/loadCache 保留', async () => {
  const adapter = mkAdapter();
  await Cache.saveCache(adapter, mkMeta({ lastError: 'compare_failed' }), mkUnits());
  const cached = await Cache.loadCache(adapter);
  assert.equal(cached.meta.lastError, 'compare_failed'); // 下次開啟頁面沿用（E3）
});

// ── D7 routeUnitsFromIndex（index → unit 清單的唯一映射點；B1 相容）──
test('D7 routeUnitsFromIndex：index.trips 依路徑段 /{route}/ 篩出各航線 unit 清單', () => {
  const index = {
    routes: ['TPE-CTS', 'TPE-FUK', 'TPE-KIX', 'TPE-NRT'],
    trips: [
      'api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
      'api/trips/TPE-NRT/2026-08-22_2026-08-30.json',
      'api/trips/TPE-KIX/2026-08-15_2026-08-23.json',
      'api/trips/TPE-CTS/2026-08-15_2026-08-23.json',
    ],
  };
  const byRoute = Cache.routeUnitsFromIndex(index);
  assert.deepEqual(byRoute['TPE-NRT'], index.trips.slice(0, 2));
  assert.deepEqual(byRoute['TPE-KIX'], [index.trips[2]]);
  assert.deepEqual(byRoute['TPE-CTS'], [index.trips[3]]);
  assert.deepEqual(byRoute['TPE-FUK'], []);   // 該航線無 trip 檔 → 空清單
});

test('D7b routeUnitsFromIndex B1 變體：index.routes（無 trips）→ api/routes/{route}.json', () => {
  const byRoute = Cache.routeUnitsFromIndex({ routes: ['TPE-NRT', 'TPE-KIX'] });
  assert.deepEqual(byRoute['TPE-NRT'], ['api/routes/TPE-NRT.json']);
  assert.deepEqual(byRoute['TPE-KIX'], ['api/routes/TPE-KIX.json']);
});

test('D7c routeUnitsFromIndex：index 無 routes 欄位 → 由 trips URL 路徑段推導航線', () => {
  const byRoute = Cache.routeUnitsFromIndex({
    trips: [
      'api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
      'api/trips/TPE-KIX/2026-08-15_2026-08-23.json',
      'api/trips/TPE-NRT/2026-08-22_2026-08-30.json',
    ],
  });
  assert.deepEqual(byRoute['TPE-NRT'], [
    'api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
    'api/trips/TPE-NRT/2026-08-22_2026-08-30.json',
  ]);
  assert.deepEqual(byRoute['TPE-KIX'], ['api/trips/TPE-KIX/2026-08-15_2026-08-23.json']);
});
