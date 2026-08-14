/**
 * 票價趨勢圖 — 聚合模組（純函式，不碰 DOM）
 *
 * 職責：把「該航線的 trip JSON」轉成圖表與 Summary 共用的週資料模型。
 * 圖表層（app.js）與 Summary 共用同一份資料，單點維護。
 *
 * UMD 匯出：瀏覽器掛全域 `PriceAgg`；Node 環境（node:test 單元測試）走 module.exports。
 * 對照：docs/development/票價趨勢圖.md §2.2 / §2.3
 */
(function (global, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    global.PriceAgg = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ════════════════════════════════════════════════════════════
  // 設定常數（集中管理；旺季日期每年更新一次）
  // ════════════════════════════════════════════════════════════
  const CONFIG = {
    // 航線顯示順序（東京在前為預設航線；不受 index.json routes 順序影響）
    ROUTES: [
      { id: 'TPE-NRT', name: '東京' },
      { id: 'TPE-KIX', name: '大阪' },
      { id: 'TPE-FUK', name: '福岡' },
      { id: 'TPE-CTS', name: '札幌' },
    ],
    // 日期範圍：label 用於按鈕與圖表標題；weeks = 取前 N 週
    RANGES: [
      { key: '3m',  label: '3 個月',  weeks: 12 },
      { key: '6m',  label: '6 個月',  weeks: 24 },
      { key: '12m', label: '12 個月', weeks: 40 }, // 資料僅 40 週，12 個月與全部同為 40 週
      { key: 'all', label: '全部',    weeks: 40 },
    ],
    // 旺季區間（以出發日期判斷；⚠️ 每年過年前手動更新，明年可由爬蟲設定自動帶出）
    PEAKS: [
      { label: '農曆過年', from: '2027-01-30', to: '2027-02-06' }, // 40 週 idx 24–25
      { label: '櫻花季',   from: '2027-03-27', to: '2027-04-03' }, // 40 週 idx 32–33
    ],
    Y: { MIN: 12000, MAX: 42000 },   // 四航線統一 Y 軸，可比性一致
    SVG: { W: 900, H: 330, M: { l: 60, r: 14, t: 22, b: 36 } },
    STALE_DAYS: 14,                  // generated_at 超過 14 天 → 過舊警示
    CONCURRENCY: 8,                  // trips 並行 fetch 上限
    NUM_WEEKS: 40,                   // 資料模型時間範圍（與 config.py NUM_WEEKS 一致）
    // 允許的頁面來源（file:// 或 localhost 供開發；Pages 網域為正式）
    ORIGIN_ALLOWLIST: [
      'https://yuhaoliaosideproject.github.io',
    ],
  };

  // ════════════════════════════════════════════════════════════
  // 聚合純函式
  // ════════════════════════════════════════════════════════════

  /**
   * 每航班取 history 最新一筆（scraped_at 最大）。
   * @param {object} flight - api/trips 檔內的航班物件
   * @returns {{ price: number|null, status: string|null }} status !== 'Available' 或 price_total 無效 → price null
   */
  function latestPrice(flight) {
    if (!flight || !Array.isArray(flight.history) || flight.history.length === 0) {
      return { price: null, status: null };
    }
    // history 依 scraped_at 排序取末筆（API 已依時間序；保守起見取最大）
    const last = flight.history.reduce((a, b) =>
      (a.scraped_at >= b.scraped_at ? a : b), flight.history[0]);
    const status = last && last.status ? last.status : null;
    const p = last && typeof last.price_total === 'number' ? last.price_total : null;
    return { price: status === 'Available' ? p : null, status };
  }

  /**
   * 聚合單週：min = 各航班最新價之最小值（非 null 才納入）。
   * 狀態判定：trip 檔不存在/無 flights → 'missing'；
   *          全部航班最新 status 皆非 Available → 'sold_out'；
   *          其餘 → 'ok'（min 可能為 null 表示部分航班缺價）。
   * @param {object|null} tripJson - api/trips 單檔內容；null = 檔案不存在
   * @returns {object} Week 資料點
   */
  function aggregateWeek(tripJson) {
    if (!tripJson || !Array.isArray(tripJson.flights) || tripJson.flights.length === 0) {
      return { d: null, r: null, min: null, f: {}, status: 'missing' };
    }
    const f = {};
    let min = null;
    let anyAvailable = false;
    let anyFlight = false;
    for (const fl of tripJson.flights) {
      anyFlight = true;
      const { price, status } = latestPrice(fl);
      f[fl.outbound_flight_no] = price;
      if (status === 'Available') anyAvailable = true;
      if (price !== null && (min === null || price < min)) min = price;
    }
    let status = 'ok';
    if (!anyFlight) status = 'missing';
    else if (!anyAvailable) status = 'sold_out';
    return {
      d: tripJson.outbound_date || null,
      r: tripJson.return_date || null,
      min,
      f,
      status,
    };
  }

  /**
   * 從 trip URL 檔名解析出發/回程日期：`api/trips/{route}_{dep}_{ret}.json`。
   * 缺資料週仍需佔位且排序正確，故日期由 URL 解析。
   * @param {string} url
   * @returns {{ d: string, r: string }}
   */
  function datesFromUrl(url) {
    const base = String(url).split('/').pop().replace(/\.json$/, ''); // TPE-NRT_2026-08-22_2026-08-30
    const parts = base.split('_'); // [route, dep, ret]
    if (parts.length >= 3) {
      return { d: parts[1], r: parts[2] };
    }
    return { d: null, r: null };
  }

  /**
   * 把該航線全部 trip 依出發日期排序 → Week[]（固定 urls.length 筆）。
   * fetch 失敗者以 null 佔位（該週 status='missing'），日期由 URL 檔名解析。
   * @param {string[]} urls - trip 檔 URL 清單（與 tripJsons 對應）
   * @param {Array<object|null>} tripJsons - 每個 URL 的載入結果（null = 失敗）
   * @returns {object[]} Week[]
   */
  function aggregateWeekly(urls, tripJsons) {
    const weeks = urls.map((url, i) => {
      const json = tripJsons[i];
      const w = aggregateWeek(json);
      if (w.status === 'missing' && w.d === null) {
        const { d, r } = datesFromUrl(url);
        w.d = d;
        w.r = r;
      }
      return w;
    });
    // 依出發日期排序（缺日期者排後）
    weeks.sort((a, b) => (a.d === null ? 1 : (b.d === null ? -1 : (a.d < b.d ? -1 : (a.d > b.d ? 1 : 0)))));
    return weeks;
  }

  /**
   * 全域平均 = 全部有效週（min !== null）每週最低價之平均（四捨五入至整數，與 mockup 一致）。
   * @param {object[]} weeks - 全量週資料（40 週）
   * @returns {number|null} 無有效週 → null
   */
  function globalAverage(weeks) {
    const valid = weeks.filter(w => w.min !== null && w.min !== undefined);
    if (valid.length === 0) return null;
    const sum = valid.reduce((s, w) => s + w.min, 0);
    return Math.round(sum / valid.length);
  }

  /**
   * 差幅 = (P − A) / A，正值貴、負值便宜（四捨五入到整數百分比）。
   * @param {number} price
   * @param {number} avg - 全域平均
   * @returns {number}
   */
  function diffPct(price, avg) {
    if (avg === null || avg === 0 || price === null) return 0;
    return Math.round((price - avg) / avg * 100);
  }

  /**
   * 範圍參數防呆：無效/負數 → 全部 40 週；>40 → 40 週。
   * @param {*} n
   * @returns {number}
   */
  function sanitizeRange(n) {
    const v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return CONFIG.NUM_WEEKS;
    return Math.min(Math.floor(v), CONFIG.NUM_WEEKS);
  }

  /**
   * 範圍篩選：取前 n 週；回傳新陣列，不修改原陣列。
   * @param {object[]} weeks
   * @param {*} n
   * @returns {object[]}
   */
  function filterRange(weeks, n) {
    return weeks.slice(0, sanitizeRange(n));
  }

  /**
   * 可見範圍最低價標記：取範圍內 min 最小之週（min === null 排除）；無 → null。
   * @param {object[]} weeks
   * @returns {object|null}
   */
  function minMark(weeks) {
    const valid = weeks.filter(w => w.min !== null && w.min !== undefined);
    if (valid.length === 0) return null;
    return valid.reduce((a, b) => (b.min < a.min ? b : a), valid[0]);
  }

  /**
   * 旺季判定：出發日期落在任一 PEAKS 區間（含邊界）→ 回傳該區塊 label；否則 null。
   * @param {string} d - 'YYYY-MM-DD'
   * @returns {string|null}
   */
  function detectPeak(d) {
    if (!d) return null;
    for (const p of CONFIG.PEAKS) {
      if (d >= p.from && d <= p.to) return p.label;
    }
    return null;
  }

  /**
   * 過舊判定：generated_at 距今超過 STALE_DAYS 天；恰等於 → false。
   * @param {string} generatedAt - ISO 字串
   * @param {number} now - 目前時間戳（預設 Date.now()）
   * @returns {boolean}
   */
  function isStale(generatedAt, now) {
    const t = Date.parse(generatedAt);
    if (Number.isNaN(t)) return false;
    const days = (now - t) / 86400000;
    return days > CONFIG.STALE_DAYS;
  }

  /**
   * 來源檢查（純邏輯）：origin 為 ''/'null'（file://）、localhost / 127.0.0.1、
   * 或允許清單 → 放行；否則拒絕。
   * @param {string} origin - location.origin（file:// 下為 '' 或 'null'）
   * @returns {boolean}
   */
  function originAllowed(origin) {
    const o = String(origin || '');
    if (o === '' || o === 'null') return true;                    // file://
    if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(o)) return true;
    return CONFIG.ORIGIN_ALLOWLIST.includes(o);
  }

  /**
   * 更新時間格式化：ISO → 'YYYY-MM-DD'（UTC 日期部分）。
   * @param {string} iso
   * @returns {string}
   */
  function formatGeneratedAt(iso) {
    if (!iso) return '—';
    const s = String(iso);
    return s.slice(0, 10);
  }

  /**
   * Summary 三卡純計算（一律以「每週最低價」為基準，不隨航班模式變動）：
   *  - minWeek：可見範圍最低價週（null 排除）
   *  - avg：全域平均（呼叫端傳入 globalAverage 結果）
   *  - peakWeek / peakNote：可見範圍 ∩ 旺季區間之最高價週；
   *    範圍內無旺季週 → 範圍內最高價週並標註「（非旺季區間）」
   * @param {object[]} weeks - 可見範圍週資料
   * @param {number|null} avg - 全域平均
   * @returns {{ minWeek: object|null, avg: number|null, peakWeek: object|null, peakNote: string }}
   */
  function summaryData(weeks, avg) {
    const valid = weeks.filter(w => w.min !== null && w.min !== undefined);
    if (valid.length === 0) {
      return { minWeek: null, avg, peakWeek: null, peakNote: '' };
    }
    const minWeek = valid.reduce((a, b) => (b.min < a.min ? b : a), valid[0]);
    const inPeak = valid.filter(w => detectPeak(w.d) !== null);
    if (inPeak.length > 0) {
      const peakWeek = inPeak.reduce((a, b) => (b.min > a.min ? b : a), inPeak[0]);
      return { minWeek, avg, peakWeek, peakNote: detectPeak(peakWeek.d) };
    }
    const peakWeek = valid.reduce((a, b) => (b.min > a.min ? b : a), valid[0]);
    return { minWeek, avg, peakWeek, peakNote: '（非旺季區間）' };
  }

  return {
    CONFIG,
    latestPrice,
    aggregateWeek,
    aggregateWeekly,
    globalAverage,
    diffPct,
    filterRange,
    sanitizeRange,
    minMark,
    detectPeak,
    isStale,
    originAllowed,
    formatGeneratedAt,
    summaryData,
  };
});
