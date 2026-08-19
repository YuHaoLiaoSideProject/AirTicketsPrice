// 票價趨勢圖 — 聚合模組單元測試（TDD 紅燈階段）
// 對照：docs/test-plans/票價趨勢圖測試計畫.md F-01~F-20（純函式部分）
// 執行：node --test tests/unit/aggregate.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const Agg = require('../../web/aggregate.js');
const { CONFIG } = Agg;

// ── Fixtures（結構對照 api/trips/*.json 真實格式）──
const mkFlight = (no, price, status = 'Available') => ({
  outbound_flight_no: no,
  history: [{ scraped_at: '2026-08-14T12:21:03.000Z', price_total: price, status }],
});
const mkTrip = (d, r, flights) => ({
  route_id: 'TPE-NRT', outbound_date: d, return_date: r, flights,
});

// 東京 3 航班週（真實值：JX 800=32296 / JX 802=46072 / JX 804=33880）
const tripA = mkTrip('2026-08-15', '2026-08-23', [
  mkFlight('JX 800', 32296), mkFlight('JX 802', 46072), mkFlight('JX 804', 33880),
]);
// 售罄週（全非 Available）
const tripSoldOut = mkTrip('2026-08-22', '2026-08-30', [
  mkFlight('JX 800', null, 'SoldOut'), mkFlight('JX 802', null, 'SoldOut'),
]);
// 部分航班缺價週（JX 802 無效價）
const tripPartial = mkTrip('2026-08-29', '2026-09-06', [
  mkFlight('JX 800', 17176), mkFlight('JX 802', null), mkFlight('JX 804', 16546),
]);

// ── F-01 每週價格取該週所有航班最低價 ──
test('F-01 aggregateWeek 每週最低價 = 各航班最新價之最小值', () => {
  const w = Agg.aggregateWeek(tripA);
  assert.equal(w.min, 32296);
  assert.equal(w.status, 'ok');
  assert.equal(w.d, '2026-08-15');
  assert.equal(w.r, '2026-08-23');
});

test('F-01b 部分航班缺價 → 只納入有效價（min 仍為有效值之最小）', () => {
  const w = Agg.aggregateWeek(tripPartial);
  assert.equal(w.min, 16546);
  assert.equal(w.f['JX 802'], null);
});

test('F-01c 每航班取 history 最新一筆（scraped_at 最大）', () => {
  const trip = mkTrip('2026-09-05', '2026-09-13', [{
    outbound_flight_no: 'JX 800',
    history: [
      { scraped_at: '2026-08-07T10:00:00.000Z', price_total: 20000, status: 'Available' },
      { scraped_at: '2026-08-14T10:00:00.000Z', price_total: 17638, status: 'Available' },
    ],
  }]);
  assert.equal(Agg.aggregateWeek(trip).min, 17638);
});

// ── F-09 售罄週 ──
test('F-09 全部航班非 Available → status=sold_out、min=null', () => {
  const w = Agg.aggregateWeek(tripSoldOut);
  assert.equal(w.status, 'sold_out');
  assert.equal(w.min, null);
});

// ── F-08 / F-20 缺資料週與部分失敗佔位 ──
test('F-08 缺資料週（trip 檔不存在）→ status=missing、min=null、日期由 URL 解析', () => {
  const urls = [
    'api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
    'api/trips/TPE-NRT/2026-08-22_2026-08-30.json', // 此檔缺
    'api/trips/TPE-NRT/2026-08-29_2026-09-06.json',
  ];
  const jsons = [tripA, null, tripPartial];
  const weeks = Agg.aggregateWeekly(urls, jsons);
  assert.equal(weeks.length, 3);
  assert.equal(weeks[0].status, 'ok');
  assert.equal(weeks[1].status, 'missing');
  assert.equal(weeks[1].min, null);
  assert.equal(weeks[1].d, '2026-08-22'); // 日期由 URL 檔名解析
  assert.equal(weeks[2].status, 'ok');
});

test('F-20 部分 trip 失敗 → 失敗週為斷點，其餘週正常', () => {
  const urls = [
    'api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
    'api/trips/TPE-NRT/2026-08-22_2026-08-30.json',
    'api/trips/TPE-NRT/2026-08-29_2026-09-06.json',
    'api/trips/TPE-NRT/2026-09-05_2026-09-13.json',
  ];
  const jsons = [tripA, null, tripPartial, null];
  const weeks = Agg.aggregateWeekly(urls, jsons);
  assert.equal(weeks.length, 4);
  assert.equal(weeks.filter(w => w.status === 'ok').length, 2);
  assert.equal(weeks.filter(w => w.status === 'missing').length, 2);
  // 排序依出發日期
  assert.deepEqual(weeks.map(w => w.d), [
    '2026-08-15', '2026-08-22', '2026-08-29', '2026-09-05',
  ]);
});

// ── F-11 空資料狀態判定（含全部無效價擴充）──
test('F-11 hasAnyPrice：空週 / 全缺 / 全無效價 → false；任一有效價 → true', () => {
  assert.equal(Agg.hasAnyPrice([]), false);
  // trips 全缺（URL 有檔名但載入全失敗）→ 全部 missing、min=null
  const urls = [
    'api/trips/TPE-FUK/2026-08-15_2026-08-23.json',
    'api/trips/TPE-FUK/2026-08-22_2026-08-30.json',
  ];
  const allMissing = Agg.aggregateWeekly(urls, [null, null]);
  assert.equal(Agg.hasAnyPrice(allMissing), false);
  // 全售罄週（有航班但皆無效價）→ false
  const allSoldOut = Agg.aggregateWeekly(urls, [tripSoldOut, tripSoldOut]);
  assert.equal(Agg.hasAnyPrice(allSoldOut), false);
  // 任一週有有效價（含混合缺週）→ true
  const mixed = Agg.aggregateWeekly(urls, [tripA, null]);
  assert.equal(Agg.hasAnyPrice(mixed), true);
});

// ── F-02 / F-06 全域平均（不隨範圍漂移；排除 null 週）──
const WEEKS40 = (() => {
  const ws = [];
  const base = new Date('2026-08-15T00:00:00Z');
  for (let i = 0; i < 40; i++) {
    const d = new Date(base.getTime() + i * 7 * 86400000);
    const iso = d.toISOString().slice(0, 10);
    ws.push({
      d: iso,
      r: new Date(d.getTime() + 8 * 86400000).toISOString().slice(0, 10),
      min: i === 20 ? null : 14000 + i * 100, // 第 21 週缺資料
      f: { 'JX 800': 14000 + i * 100 },
      status: i === 20 ? 'missing' : 'ok',
    });
  }
  return ws;
})();

test('F-02 globalAverage = 全部有效週最低價之平均（四捨五入），null 週排除', () => {
  const sum = WEEKS40.filter(w => w.min !== null).reduce((s, w) => s + w.min, 0);
  const n = WEEKS40.filter(w => w.min !== null).length;
  const expected = Math.round(sum / n);
  assert.equal(Agg.globalAverage(WEEKS40), expected);
});

test('F-06 平均線不隨範圍漂移（全域平均值，範圍篩選不影響）', () => {
  const full = Agg.globalAverage(WEEKS40);
  const sliced = Agg.globalAverage(Agg.filterRange(WEEKS40, 12));
  // 規格：globalAverage 以「全部 40 週」為輸入；filter 後計算屬實作錯誤
  // 此處驗證 aggregateWeekly 產出之全量資料的 avg 即為圖表使用之全域值
  assert.equal(full, Agg.globalAverage(WEEKS40.slice(0, 40)));
  assert.notEqual(sliced, full); // 證明 filter 後算出來的值不同 → 圖表必須吃全量
});

// ── F-03 差幅 ──
test('F-03 diffPct 以全域平均為基準（負=便宜、正=貴，四捨五入整數%）', () => {
  assert.equal(Agg.diffPct(14139, 19480), -27); // (14139-19480)/19480 ≈ -27.4%
  assert.equal(Agg.diffPct(37720, 19480), 94);
  assert.equal(Agg.diffPct(19480, 19480), 0);
});

// ── F-04 / F-19 範圍篩選與防呆 ──
test('F-04 filterRange 週數映射（3m=12 / 6m=24 / 12m=40 / all=40）', () => {
  assert.equal(Agg.filterRange(WEEKS40, 12).length, 12);
  assert.equal(Agg.filterRange(WEEKS40, 24).length, 24);
  assert.equal(Agg.filterRange(WEEKS40, 40).length, 40);
  assert.equal(CONFIG.RANGES.find(r => r.key === '3m').weeks, 12);
  assert.equal(CONFIG.RANGES.find(r => r.key === '6m').weeks, 24);
  assert.equal(CONFIG.RANGES.find(r => r.key === '12m').weeks, 40);
  assert.equal(CONFIG.RANGES.find(r => r.key === 'all').weeks, 40);
});

test('F-19 sanitizeRange 防呆（空/負/NaN → 40；>40 → 40）', () => {
  assert.equal(Agg.sanitizeRange(undefined), 40);
  assert.equal(Agg.sanitizeRange(-5), 40);
  assert.equal(Agg.sanitizeRange(NaN), 40);
  assert.equal(Agg.sanitizeRange(999), 40);
  assert.equal(Agg.sanitizeRange(24), 24);
});

// ── F-05 最低價標記依可見範圍重算 ──
test('F-05 minMark 取可見範圍內最低價週（null 排除）；可能與全域不同', () => {
  const first12 = Agg.filterRange(WEEKS40, 12);
  const mark = Agg.minMark(first12);
  assert.equal(mark.d, WEEKS40[0].d); // 14000 為前 12 週最低
  const globalMark = Agg.minMark(WEEKS40);
  assert.equal(globalMark.d, WEEKS40[0].d); // 全域最低也在第 1 週（14000）
  // 證明重算：範圍內最低 = 範圍最小值
  const mins = first12.map(w => w.min);
  assert.equal(mark.min, Math.min(...mins));
});

// ── F-10 資料過舊判定 ──
test('F-10 isStale 邊界：>14 天 true、恰 14 天 false', () => {
  const now = Date.parse('2026-08-29T00:00:00Z');
  assert.equal(Agg.isStale('2026-08-14T00:00:00Z', now), true); // 15 天
  assert.equal(Agg.isStale('2026-08-15T00:00:00Z', now), false); // 14 天
  assert.equal(Agg.isStale('2026-08-28T00:00:00Z', now), false); // 1 天
});

test('F-10b isStale 不傳 now 時預設 Date.now()（真實呼叫路徑，防 NaN 回 false）', () => {
  const past = new Date(Date.now() - 15 * 86400000).toISOString();
  assert.equal(Agg.isStale(past), true);
  const recent = new Date(Date.now() - 1 * 86400000).toISOString();
  assert.equal(Agg.isStale(recent), false);
});

// ── F-12 網域來源判定（純邏輯部分）──
test('F-12 originAllowed 放行 file://、localhost、允許清單；拒絕其他', () => {
  assert.equal(Agg.originAllowed(''), true);            // file:// → origin ''
  assert.equal(Agg.originAllowed('null'), true);
  assert.equal(Agg.originAllowed('http://localhost:8000'), true);
  assert.equal(Agg.originAllowed('http://127.0.0.1:8000'), true);
  assert.equal(Agg.originAllowed('https://yuhaoliaosideproject.github.io'), true);
  assert.equal(Agg.originAllowed('https://evil.example.com'), false);
});

// ── F-14 預設航線 ──
test('F-14 預設航線 = CONFIG.ROUTES 第一條（東京 TPE-NRT）', () => {
  assert.equal(CONFIG.ROUTES[0].id, 'TPE-NRT');
  assert.equal(CONFIG.ROUTES[0].name, '東京');
});

test('F-14b 航線清單含東京/大阪/福岡/札幌（與 config.py ROUTES 同步）', () => {
  assert.deepEqual(CONFIG.ROUTES.map(r => r.id), ['TPE-NRT', 'TPE-KIX', 'TPE-FUK', 'TPE-CTS']);
});

// ── F-15 更新時間格式化 ──
test('F-15 formatGeneratedAt 取日期部分（UTC）', () => {
  assert.equal(Agg.formatGeneratedAt('2026-08-14T13:19:34.000Z'), '2026-08-14');
});

// ── F-15b 「上次更新 HH:MM」格式化（離線功能 §2.7；訪客本地時區）──
test('F-15b formatLastUpdated 回傳 HH:MM 形狀（以格式形狀驗證，避免時區 flaky）', () => {
  assert.match(Agg.formatLastUpdated('2026-08-14T22:46:53.000Z'), /^\d{2}:\d{2}$/);
});

test('F-15c formatLastUpdated null / 無效輸入 → "--:--"', () => {
  assert.equal(Agg.formatLastUpdated(null), '--:--');
  assert.equal(Agg.formatLastUpdated(undefined), '--:--');
  assert.equal(Agg.formatLastUpdated(''), '--:--');
  assert.equal(Agg.formatLastUpdated('not-a-date'), '--:--');
});

// ── F-16 旺季區間判定 ──
test('F-16 detectPeak 區間含邊界；區間外 null', () => {
  assert.equal(Agg.detectPeak('2027-01-30'), '農曆過年'); // 起點
  assert.equal(Agg.detectPeak('2027-02-06'), '農曆過年'); // 終點
  assert.equal(Agg.detectPeak('2027-02-07'), null);       // 終點外一天
  assert.equal(Agg.detectPeak('2027-03-27'), '櫻花季');
  assert.equal(Agg.detectPeak('2027-04-03'), '櫻花季');
  assert.equal(Agg.detectPeak('2026-09-19'), null);
});

// ── F-16a 地區性旺季（routes 限定航線；札幌誤標修正）──
test('F-16a detectPeak 帶 routeId：櫻花季依航線分區（本州 vs 札幌）', () => {
  // 本州區間（3/27~4/3）只標東京/大阪/福岡；札幌不標
  assert.equal(Agg.detectPeak('2027-03-27', 'TPE-NRT'), '櫻花季');
  assert.equal(Agg.detectPeak('2027-04-03', 'TPE-KIX'), '櫻花季');
  assert.equal(Agg.detectPeak('2027-03-27', 'TPE-CTS'), null); // 札幌花期較晚，不誤標
  assert.equal(Agg.detectPeak('2027-04-03', 'TPE-CTS'), null);
  // 札幌區間（4/24~5/1）只標札幌
  assert.equal(Agg.detectPeak('2027-04-24', 'TPE-CTS'), '櫻花季');
  assert.equal(Agg.detectPeak('2027-05-01', 'TPE-CTS'), '櫻花季');
  assert.equal(Agg.detectPeak('2027-04-24', 'TPE-NRT'), null);
  assert.equal(Agg.detectPeak('2027-04-24', 'TPE-KIX'), null);
  assert.equal(Agg.detectPeak('2027-05-01', 'TPE-FUK'), null);
  // 全域 peak（農曆過年，無 routes）不受 routeId 影響
  assert.equal(Agg.detectPeak('2027-01-30', 'TPE-CTS'), '農曆過年');
  assert.equal(Agg.detectPeak('2027-02-06', 'TPE-NRT'), '農曆過年');
});

// ── F-16b 動態旺季區間（api/index.json.peaks 帶入；fallback 設計）──
test('F-16b getPeaks 預設為 CONFIG.PEAKS（fallback）', () => {
  assert.equal(Agg.getPeaks(), CONFIG.PEAKS);
});

test('F-16b setPeaks 後同 label 被覆蓋、未帶 label 保留（合併語意）', () => {
  const saved = Agg.getPeaks();
  try {
    // 資料帶入：2028 過年（自動計算）＋同名的農曆過年被覆蓋為 2028 區間
    Agg.setPeaks([{ label: '農曆過年', from: '2028-01-22', to: '2028-01-29' }]);
    assert.equal(Agg.detectPeak('2028-01-22'), '農曆過年'); // 起點
    assert.equal(Agg.detectPeak('2028-01-29'), '農曆過年'); // 終點
    assert.equal(Agg.detectPeak('2028-01-30'), null);
    assert.equal(Agg.detectPeak('2027-02-06'), null);       // 2027 過年已被 2028 覆蓋
    // 未帶的 label（手動櫻花季）保留：合併語意，不因自動化消失（D5）
    assert.equal(Agg.detectPeak('2027-03-27'), '櫻花季');
    assert.equal(Agg.detectPeak('2027-04-03'), '櫻花季');
  } finally {
    Agg.setPeaks(saved);
  }
});

test('F-16b setPeaks 合併後依 from 排序', () => {
  const saved = Agg.getPeaks();
  try {
    Agg.setPeaks([{ label: '農曆過年', from: '2028-01-22', to: '2028-01-29' }]);
    const merged = Agg.getPeaks();
    const froms = merged.map(p => p.from);
    assert.deepEqual(froms, [...froms].sort());
    assert.ok(merged.some(p => p.label === '櫻花季'), '櫻花季應保留');
  } finally {
    Agg.setPeaks(saved);
  }
});

test('F-16b setPeaks 非陣列／空陣列不更新（保留 fallback）', () => {
  const saved = Agg.getPeaks();
  try {
    Agg.setPeaks(null);
    assert.equal(Agg.getPeaks(), saved);
    Agg.setPeaks('bad');
    assert.equal(Agg.getPeaks(), saved);
    Agg.setPeaks([]);
    assert.equal(Agg.getPeaks(), saved);
  } finally {
    Agg.setPeaks(saved);
  }
});

// ── F-17 Summary 純計算 ──
test('F-17 summaryData 最便宜週（範圍內）/ 全域平均 / 旺季高峰', () => {
  const weeks = Agg.aggregateWeekly(
    ['api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
     'api/trips/TPE-NRT/2026-08-22_2026-08-30.json',
     'api/trips/TPE-NRT/2026-08-29_2026-09-06.json'],
    [tripA, tripSoldOut, tripPartial]
  );
  const avg = Agg.globalAverage(weeks);
  const s = Agg.summaryData(weeks, avg);
  // tripA min=32296、soldOut=null、tripPartial min=16546 → 範圍內最低 = 08-29
  assert.equal(s.minWeek.d, '2026-08-29');
  assert.equal(s.minWeek.min, 16546);
  assert.equal(s.avg, avg);
  // 旺季高峰：範圍內（無旺季週）→ 取範圍內最高價週並標註非旺季
  assert.equal(s.peakWeek.d, '2026-08-15');
  assert.equal(s.peakNote, '（非旺季區間）');
});

// ── F-17b summaryData 帶 routeId：旺季高峰判定依航線（地區性櫻花季）──
test('F-17b summaryData 帶 routeId：札幌 4/24 週為櫻花季、東京視為非旺季', () => {
  const weeks = [
    { d: '2027-04-17', min: 20000 },
    { d: '2027-04-24', min: 40000 }, // 札幌櫻花季區間（routes: TPE-CTS）
    { d: '2027-05-01', min: 38000 }, // 札幌櫻花季區間
  ];
  const avg = 30000;
  // TPE-CTS：4/24 為札幌櫻花季週 → 旺季高峰標櫻花季
  let s = Agg.summaryData(weeks, avg, 'TPE-CTS');
  assert.equal(s.peakWeek.d, '2027-04-24');
  assert.equal(s.peakNote, '櫻花季');
  // TPE-NRT：4/24 不在本州櫻花季區間 → 非旺季（取範圍內最高價週）
  s = Agg.summaryData(weeks, avg, 'TPE-NRT');
  assert.equal(s.peakWeek.d, '2027-04-24');
  assert.equal(s.peakNote, '（非旺季區間）');
  // routeId 省略：與舊行為一致（不篩選 → 4/24 落在札幌區間 → 櫻花季）
  s = Agg.summaryData(weeks, avg);
  assert.equal(s.peakNote, '櫻花季');
});

// ── 聚合輸出排序（aggregateWeekly 依出發日期排序）──
test('aggregateWeekly 輸出依出發日期排序（不依傳入順序）', () => {
  const urls = [
    'api/trips/TPE-NRT/2026-08-22_2026-08-30.json',
    'api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
  ];
  const jsons = [tripSoldOut, tripA];
  const weeks = Agg.aggregateWeekly(urls, jsons);
  assert.deepEqual(weeks.map(w => w.d), ['2026-08-15', '2026-08-22']);
});

// ═══════════════ 漲跌計算（scrape-vs-scrape）═══════════════

// F-C1 prevLatestPrice：取 history 次新一筆
test('F-C1 prevLatestPrice 取倒數第二筆（上次抓取價格）', () => {
  const flight = {
    outbound_flight_no: 'JX 800',
    history: [
      { scraped_at: '2026-08-07T10:00:00.000Z', price_total: 26008, status: 'Available' },
      { scraped_at: '2026-08-14T10:00:00.000Z', price_total: 24120, status: 'Available' },
    ],
  };
  const prev = Agg.prevLatestPrice(flight);
  assert.equal(prev.price, 26008);
});

test('F-C1b prevLatestPrice 只有 1 筆 history → null', () => {
  const flight = {
    outbound_flight_no: 'JX 800',
    history: [
      { scraped_at: '2026-08-14T10:00:00.000Z', price_total: 24120, status: 'Available' },
    ],
  };
  const prev = Agg.prevLatestPrice(flight);
  assert.equal(prev.price, null);
});

test('F-C1c prevLatestPrice 空 history / null 輸入 → null', () => {
  assert.equal(Agg.prevLatestPrice(null).price, null);
  assert.equal(Agg.prevLatestPrice({ history: [] }).price, null);
  assert.equal(Agg.prevLatestPrice({ history: null }).price, null);
});

// F-C2 calcChange：漲跌計算
test('F-C2 calcChange 正常計算（降價 / 漲價 / 不變）', () => {
  let r = Agg.calcChange(24120, 26008);
  assert.equal(r.change, -1888);
  assert.equal(r.changePct, -7);  // round(-1888/26008*100) = -7
  r = Agg.calcChange(30000, 26008);
  assert.equal(r.change, 3992);
  assert.equal(r.changePct, 15);  // round(3992/26008*100) = 15
  r = Agg.calcChange(26008, 26008);
  assert.equal(r.change, 0);
  assert.equal(r.changePct, 0);
});

test('F-C2b calcChange 任一為 null / prev=0 → change null', () => {
  assert.deepEqual(Agg.calcChange(null, 26008), { change: null, changePct: null });
  assert.deepEqual(Agg.calcChange(24120, null), { change: null, changePct: null });
  assert.deepEqual(Agg.calcChange(24120, 0), { change: null, changePct: null });
});

// F-C3 formatChangePct：格式化顯示
test('F-C3 formatChangePct 降價/漲價/不變/null', () => {
  assert.equal(Agg.formatChangePct(-7), '↓ -7%');
  assert.equal(Agg.formatChangePct(15), '↑ +15%');
  assert.equal(Agg.formatChangePct(0), '— 0%');
  assert.equal(Agg.formatChangePct(null), '—');
  assert.equal(Agg.formatChangePct(undefined), '—');
});

// F-C4 aggregateWeek 新增漲跌欄位
test('F-C4 aggregateWeek 包含 minPrev/minChange/minChangePct/fc', () => {
  const trip = mkTrip('2026-09-05', '2026-09-13', [{
    outbound_flight_no: 'JX 800',
    history: [
      { scraped_at: '2026-08-07T10:00:00.000Z', price_total: 20000, status: 'Available' },
      { scraped_at: '2026-08-14T10:00:00.000Z', price_total: 17638, status: 'Available' },
    ],
  }]);
  const w = Agg.aggregateWeek(trip);
  assert.equal(w.min, 17638);
  assert.equal(w.minPrev, 20000);
  assert.equal(w.minChange, -2362);
  assert.ok(w.minChangePct < 0);  // 降價
  // fc 也包含變動
  assert.deepEqual(w.fc['JX 800'].prevPrice, 20000);
  assert.equal(w.fc['JX 800'].change, -2362);
});

test('F-C4b aggregateWeek 只有 1 筆 history → 漲跌欄位為 null', () => {
  const trip = mkTrip('2026-09-05', '2026-09-13', [
    mkFlight('JX 800', 20000),  // mkFlight 只有 1 筆 history
  ]);
  const w = Agg.aggregateWeek(trip);
  assert.equal(w.min, 20000);
  assert.equal(w.minPrev, null);
  assert.equal(w.minChange, null);
  assert.equal(w.minChangePct, null);
  assert.equal(w.fc['JX 800'].prevPrice, null);
});

test('F-C4c aggregateWeek 缺資料週 → 漲跌欄位皆為 null', () => {
  const w = Agg.aggregateWeek(null);
  assert.equal(w.min, null);
  assert.equal(w.minPrev, null);
  assert.equal(w.minChange, null);
  assert.equal(w.minChangePct, null);
  assert.deepEqual(w.fc, {});
});
