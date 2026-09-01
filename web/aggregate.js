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
      { id: 'TPE-OKA', name: '沖繩' },
      { id: 'TPE-NGO', name: '名古屋' },
      { id: 'TPE-PUS', name: '釜山' },
      { id: 'TPE-SGN', name: '胡志明' },
      { id: 'TPE-BKK', name: '曼谷' },
    ],
    // 地區分群（預設值；api/index.json 帶入時會覆寫）
    REGIONS: [
      { id: 'japan', name: '日本', routes: ['TPE-NRT', 'TPE-KIX', 'TPE-FUK', 'TPE-CTS', 'TPE-OKA', 'TPE-NGO'] },
      { id: 'other', name: '其他', routes: ['TPE-PUS', 'TPE-SGN', 'TPE-BKK'] },
    ],
    // 日期範圍：僅保留「全部」，不限制週數
    RANGES: [
      { key: 'all', label: '全部', weeks: null },
    ],
    // 旺季區間（以出發日期判斷；⚠️ 每年過年前手動更新）
    // 農曆過年全域；櫻花季花期隨地區不同 → 拆兩筆以 routes 限定航線（札幌比本州晚約 4 週）
    // 參考開花日：東京/大阪/福岡 ≈ 3 月底~4 月初（idx 32–33）；札幌 ≈ 4 月底~5 月初（idx 36–37）
    // routes 省略 → 全域（向後相容；資料帶入的 peaks 同樣適用，見 detectPeak）
    PEAKS: [
      { label: '農曆過年', from: '2027-01-30', to: '2027-02-06' }, // 40 週 idx 24–25
      { label: '櫻花季',   from: '2027-03-27', to: '2027-04-03', routes: ['TPE-NRT', 'TPE-KIX', 'TPE-FUK'] }, // 本州
      { label: '櫻花季',   from: '2027-04-24', to: '2027-05-01', routes: ['TPE-CTS'] }, // 北海道（札幌）
      { label: '櫻花季',   from: '2027-01-20', to: '2027-02-05', routes: ['TPE-OKA'] }, // 沖繩（寒緋櫻，比本州早 2 個月）
    ],
    Y: {},                            // Y 軸改為從資料動態計算（app.js buildChart）
    SVG: { W: 900, H: 330, M: { l: 60, r: 14, t: 22, b: 36 } },
    STALE_DAYS: 14,                  // generated_at 超過 14 天 → 過舊警示
    CONCURRENCY: 8,                  // trips 並行 fetch 上限
    NUM_WEEKS: Infinity,              // 不限制週數，顯示全部資料
    // 允許的頁面來源（file:// 或 localhost 供開發；Pages 網域為正式）
    ORIGIN_ALLOWLIST: [
      'https://yuhaoliaosideproject.github.io',
      'https://gh.mdevs.uk',
    ],
  };

  // ════════════════════════════════════════════════════════════
  // 聚合純函式
  // ════════════════════════════════════════════════════════════

  // 動態旺季區間（api/index.json.peaks 帶入；資料未帶/離線時 fallback 至上方 CONFIG.PEAKS）
  let activePeaks = CONFIG.PEAKS;

  /**
   * 以 index.json 的 peaks 更新旺季區間（來源決策：docs/tech-decisions/農曆過年旺季-2026-08-15.md D4）。
   * 合併語意：資料帶入的 label（如自動計算的農曆過年）覆蓋同 label；
   * 未出現的 label（如手動維護的櫻花季）保留 CONFIG.PEAKS 值 → 手動設定不被自動化覆蓋（D5）。
   * 非陣列／空陣列 → 不更新（保留 fallback，舊資料/畸形輸入安全）。
   * @param {Array<{label: string, from: string, to: string, routes?: string[]}>} peaks
   *   routes 可選：缺省 = 全域；有值 = 僅這些航線套用（與 detectPeak(d, routeId) 搭配）。
   */
  function setPeaks(peaks) {
    if (!Array.isArray(peaks) || peaks.length === 0) return;
    const byLabel = new Map(peaks.map(p => [p.label, p]));
    activePeaks = CONFIG.PEAKS
      .filter(p => !byLabel.has(p.label))   // 手動保留：資料沒帶的 label
      .concat(peaks)
      .sort((a, b) => String(a.from).localeCompare(String(b.from)));
  }

  /** 目前生效的旺季區間（含 fallback）。 */
  function getPeaks() {
    return activePeaks;
  }

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
   * 每航班取 history 次新一筆（倒數第二筆 scraped_at）：與 latestPrice 比較算漲跌。
   * history 只有 1 筆 → 回傳 null（無法比較）。
   * @param {object} flight - api/trips 檔內的航班物件
   * @returns {{ price: number|null, status: string|null }}
   */
  function prevLatestPrice(flight) {
    if (!flight || !Array.isArray(flight.history) || flight.history.length < 2) {
      return { price: null, status: null };
    }
    const sorted = [...flight.history].sort((a, b) => a.scraped_at.localeCompare(b.scraped_at));
    const prev = sorted[sorted.length - 2];
    const status = prev && prev.status ? prev.status : null;
    const p = prev && typeof prev.price_total === 'number' ? prev.price_total : null;
    return { price: status === 'Available' ? p : null, status };
  }

  /**
   * 計算單航班的價格漲跌（本次 vs 上次）。
   * @param {number|null} current - 本次價格
   * @param {number|null} prev - 上次價格
   * @returns {{ change: number|null, changePct: number|null }}
   */
  function calcChange(current, prev) {
    if (current === null || prev === null || prev === 0) {
      return { change: null, changePct: null };
    }
    const change = current - prev;
    const changePct = Math.round(change / prev * 100);
    return { change, changePct };
  }

  /**
   * 格式化漲跌顯示（供 tooltip 與表格共用）。
   * @param {number|null} changePct
   * @returns {string} 如 '↓ -8%' / '↑ +5%' / '—'
   */
  function formatChangePct(changePct) {
    if (changePct === null || changePct === undefined) return '—';
    if (changePct > 0) return '↑ +' + changePct + '%';
    if (changePct < 0) return '↓ ' + changePct + '%';
    return '— 0%';
  }

  /**
   * 聚合單週：min = 各航班最新價之最小值（非 null 才納入）。
   * 狀態判定：trip 檔不存在/無 flights → 'missing'；
   *          全部航班最新 status 皆非 Available → 'sold_out'；
   *          其餘 → 'ok'（min 可能為 null 表示部分航班缺價）。
   * 漲跌：fc = 每航班的價格變動；minPrev/minChange/minChangePct = 最低價航班的變動。
   * @param {object|null} tripJson - api/trips 單檔內容；null = 檔案不存在
   * @returns {object} Week 資料點
   */
  function aggregateWeek(tripJson) {
    if (!tripJson || !Array.isArray(tripJson.flights) || tripJson.flights.length === 0) {
      return { d: null, r: null, min: null, minPrev: null, minChange: null, minChangePct: null, f: {}, fc: {}, status: 'missing' };
    }
    const f = {};
    const fc = {};  // flight changes: flight_no → { prevPrice, change, changePct }
    let min = null;
    let minPrev = null;
    let anyAvailable = false;
    let anyFlight = false;
    for (const fl of tripJson.flights) {
      anyFlight = true;
      const { price, status } = latestPrice(fl);
      const prev = prevLatestPrice(fl);
      f[fl.outbound_flight_no] = price;
      // 計算漲跌
      const { change, changePct } = calcChange(price, prev.price);
      fc[fl.outbound_flight_no] = { prevPrice: prev.price, change, changePct };
      if (status === 'Available') anyAvailable = true;
      if (price !== null && (min === null || price < min)) {
        min = price;
        minPrev = prev.price;
      }
    }
    // 最低價航班的漲跌
    const minChangeObj = calcChange(min, minPrev);
    let status = 'ok';
    if (!anyFlight) status = 'missing';
    else if (!anyAvailable) status = 'sold_out';
    return {
      d: tripJson.outbound_date || null,
      r: tripJson.return_date || null,
      min,
      minPrev,
      minChange: minChangeObj.change,
      minChangePct: minChangeObj.changePct,
      f,
      fc,
      status,
    };
  }

  /**
   * 從 trip URL 檔名解析出發/回程日期：`api/trips/{route}/{dep}_{ret}.json`。
   * 缺資料週仍需佔位且排序正確，故日期由 URL 解析。
   * @param {string} url
   * @returns {{ d: string, r: string }}
   */
  function datesFromUrl(url) {
    const base = String(url).split('/').pop().replace(/\.json$/, ''); // 2026-08-22_2026-08-30
    const parts = base.split('_'); // [dep, ret]
    if (parts.length >= 2) {
      return { d: parts[0], r: parts[1] };
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
   * 是否有任何有效價格週（min 非 null）。
   * 航線 trips 全缺或全部無有效價格 → false（前端顯示空狀態，不渲染空網格圖表）。
   * @param {object[]} weeks - 全量週資料
   * @returns {boolean}
   */
  function hasAnyPrice(weeks) {
    return weeks.some(w => w.min !== null && w.min !== undefined);
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
   * 旺季判定：出發日期落在任一 PEAKS 區間（含邊界），且符合航線 → 回傳該區塊 label；否則 null。
   * 航線篩選：peak 帶 routes（如地區性櫻花季）時只套用於列出的航線；routes 缺省 → 全域。
   * routeId 省略 → 不篩選（與舊行為一致，供不帶航線語境呼叫）。
   * @param {string} d - 'YYYY-MM-DD'
   * @param {string} [routeId] - 'TPE-NRT' 等航線 id
   * @returns {string|null}
   */
  function detectPeak(d, routeId) {
    if (!d) return null;
    for (const p of activePeaks) {
      if (p.routes && routeId && !p.routes.includes(routeId)) continue;
      if (d >= p.from && d <= p.to) return p.label;
    }
    return null;
  }

  /**
   * 過舊判定：generated_at 距今超過 STALE_DAYS 天；恰等於 → false。
   * @param {string} generatedAt - ISO 字串
   * @param {number} [now] - 目前時間戳（預設 Date.now()）
   * @returns {boolean}
   */
  function isStale(generatedAt, now = Date.now()) {
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
   * 「上次更新 HH:MM」格式化（離線功能 §2.7 / F-15）：顯示訪客本地時區；null / 無效 → '--:--'。
   * @param {string|null} iso - syncedAt / generated_at ISO 字串
   * @returns {string}
   */
  function formatLastUpdated(iso) {
    if (!iso) return '--:--';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '--:--';
    return d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false });
  }

  /**
   * Summary 三卡純計算（一律以「每週最低價」為基準，不隨航班模式變動）：
   *  - minWeek：可見範圍最低價週（null 排除）
   *  - avg：全域平均（呼叫端傳入 globalAverage 結果）
   *  - peakWeek / peakNote：可見範圍 ∩ 旺季區間之最高價週；
   *    範圍內無旺季週 → 範圍內最高價週並標註「（非旺季區間）」
   * @param {object[]} weeks - 可見範圍週資料
   * @param {number|null} avg - 全域平均
   * @param {string} [routeId] - 航線 id（旺季高峰判定依航線篩選，如地區性櫻花季）
   * @returns {{ minWeek: object|null, avg: number|null, peakWeek: object|null, peakNote: string }}
   */
  function summaryData(weeks, avg, routeId) {
    const valid = weeks.filter(w => w.min !== null && w.min !== undefined);
    if (valid.length === 0) {
      return { minWeek: null, avg, peakWeek: null, peakNote: '' };
    }
    const minWeek = valid.reduce((a, b) => (b.min < a.min ? b : a), valid[0]);
    const inPeak = valid.filter(w => detectPeak(w.d, routeId) !== null);
    if (inPeak.length > 0) {
      const peakWeek = inPeak.reduce((a, b) => (b.min > a.min ? b : a), inPeak[0]);
      return { minWeek, avg, peakWeek, peakNote: detectPeak(peakWeek.d, routeId) };
    }
    const peakWeek = valid.reduce((a, b) => (b.min > a.min ? b : a), valid[0]);
    return { minWeek, avg, peakWeek, peakNote: '（非旺季區間）' };
  }

  /**
   * 判斷該週是否已過期：出發日期 < 今天（UTC 午夜）→ 已過期，不需顯示。
   * 用於前端過濾：資料保留，畫面不出現。
   * @param {string|null} d - 出發日期 'YYYY-MM-DD'（UTC）
   * @returns {boolean} true = 已過期
   */
  function isExpired(d) {
    if (!d) return false; // 缺日期 → 不過濾（保留）
    const today = new Date();
    const todayUTC = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    const depart = new Date(d + 'T00:00:00Z');
    return depart.getTime() < todayUTC;
  }

  /**
   * 範圍篩選（含過期過濾）：取前 n 週，再排除出發日期已過期的週。
   * @param {object[]} weeks
   * @param {*} n
   * @returns {object[]}
   */
  function filterRangeWithExpiry(weeks, n) {
    return weeks.slice(0, sanitizeRange(n)).filter(w => !isExpired(w.d));
  }

  return {
    CONFIG,
    latestPrice,
    prevLatestPrice,
    calcChange,
    formatChangePct,
    aggregateWeek,
    aggregateWeekly,
    hasAnyPrice,
    globalAverage,
    diffPct,
    filterRange,
    filterRangeWithExpiry,
    isExpired,
    sanitizeRange,
    minMark,
    setPeaks,
    getPeaks,
    detectPeak,
    isStale,
    originAllowed,
    formatGeneratedAt,
    formatLastUpdated,
    summaryData,
  };
});
