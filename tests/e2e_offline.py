#!/usr/bin/env python3
"""離線功能 — 離線 E2E 測試（T7）

對照：
  docs/development/離線功能.md（§6 邊界 E1–E8 / EC1–EC4；情境 A–E；§8 開發順序 T7）
  docs/test-plans/離線功能測試計畫.md（E2E-01~23 / INT-01~08 對應案例）
  docs/bdds/離線功能.feature（@error-handling E1–E8 / @edge-case / @business-rules）

架構：本機 http.server（repo root）＋ Python Playwright headless chromium
      （完全沿用 tests/e2e_smoke.py 風格：起伺服器、route 攔截、check() 累積結果）
模式：
  真實資料 — 直連本機伺服器讀取 repo 內 api/ 真實資料（情境 A / B / D）
  mocked    — page.route 攔截 api 回應（304 / 200 / 404 / abort / quota 注入）

關鍵技術（T6 實測）：
  - Playwright context.set_offline 於此環境會改變 navigator.onLine，但 reload 後
    add_init_script 的覆寫會重置 → 以 cookie `offline=1` 讓「離線 reload」持久生效
  - 資料層離線用 route abort / set_offline 依情境選擇
  - quota 模擬：init script 以 defineProperty 攔截 OfflineCache.saveCache，
    單位數超過門檻即拋 QuotaExceededError（E5）

執行：python tests/e2e_offline.py
"""
import functools
import http.server
import json
import os
import socketserver
import sys
import threading
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = None
chromium_path = '/usr/bin/chromium'

RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print(('  ✅ ' if cond else '  ❌ ') + name + ('' if cond else '  ← ' + str(detail)))


# ═══════════════ 本機伺服器 ═══════════════
class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def start_server():
    handler = functools.partial(QuietHandler, directory=ROOT)
    httpd = socketserver.TCPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# ═══════════════ 注入腳本 ═══════════════
# navigator.onLine 覆寫：初始值 = cookie offline=1（離線 reload 持久）否則真實值；
# __setOnline(on) 切換並觸發 offline/online 事件（E2/E7/EC3 動態切換用）。
INIT_ONLINE = r"""
(() => {
  const real = window.navigator.onLine;
  const offCookie = /(?:^|; )offline=1/.test(document.cookie);
  window.__online = offCookie ? false : real;
  try {
    Object.defineProperty(Navigator.prototype, 'onLine', {
      get: () => window.__online, configurable: true,
    });
  } catch (e) {}
  window.__setOnline = function (on) {
    window.__online = !!on;
    window.dispatchEvent(new Event(on ? 'online' : 'offline'));
  };
})();
"""

# E5 quota 模擬：以 defineProperty 攔截 window.OfflineCache 指派（cache.js UMD 匯出時觸發），
# 再包一層 saveCache——單位數超過 __quotaMax 即拋 QuotaExceededError → app 走 quotaDegrade。
INIT_QUOTA = r"""
(() => {
  let oc = window.OfflineCache;
  Object.defineProperty(window, 'OfflineCache', {
    configurable: true,
    get: () => oc,
    set: (v) => {
      oc = v;
      if (v && !window.__quotaPatched) {
        window.__quotaPatched = true;
        window.__quotaMode = false;
        window.__quotaMax = 5;
        const orig = v.saveCache.bind(v);
        v.saveCache = async function (storage, meta, units) {
          if (window.__quotaMode && Object.keys(units || {}).length > window.__quotaMax) {
            throw new DOMException('quota exceeded', 'QuotaExceededError');
          }
          return orig(storage, meta, units);
        };
      }
    },
  });
})();
"""

# E8 無痕 IDB 不可用模擬：讓 window.indexedDB 存取即拋錯（Safari 無痕 SecurityError /
# Chrome 私有模式行為；cache.js idbOpen 的 typeof indexedDB 檢查與 indexedDB.open 都會觸發
# → loadCache 拋錯 → app.js init 降級記憶體快取（E8：頁面照常運作，僅不持久化））。
# ① 直接以 defineProperty 換掉 window.indexedDB；若屬性不可設定 → ② 退路覆寫 IDBFactory.open。
INIT_NOIDB = r"""
(() => {
  try {
    Object.defineProperty(window, 'indexedDB', {
      configurable: true,
      get() {
        throw new DOMException('IndexedDB unavailable (private mode)', 'SecurityError');
      },
    });
    return;
  } catch (e) {}
  try {
    const proto = window.IDBFactory && window.IDBFactory.prototype;
    if (proto) {
      proto.open = function () {
        throw new DOMException('IndexedDB unavailable (private mode)', 'SecurityError');
      };
    }
  } catch (e) {}
})();
"""


# ═══════════════ Mock 資料（結構對照 api/trips/*.json 真實格式）═══════════════
def mock_trip(d, r, flights):
    return {'route_id': 'TPE-NRT', 'outbound_date': d, 'return_date': r,
            'flights': [{'outbound_flight_no': no, 'history': [
                {'scraped_at': '2026-08-14T12:00:00.000Z', 'price_total': price, 'status': status}]}
                for no, price, status in flights]}


NRT = ['api/trips/TPE-NRT/2026-08-15_2026-08-23.json',
       'api/trips/TPE-NRT/2026-08-22_2026-08-30.json',
       'api/trips/TPE-NRT/2026-08-29_2026-09-06.json',
       'api/trips/TPE-NRT/2026-09-05_2026-09-13.json',
       'api/trips/TPE-NRT/2026-09-12_2026-09-20.json',
       'api/trips/TPE-NRT/2026-09-19_2026-09-27.json']
KIX = ['api/trips/TPE-KIX/2026-08-15_2026-08-23.json',
       'api/trips/TPE-KIX/2026-08-22_2026-08-30.json',
       'api/trips/TPE-KIX/2026-08-29_2026-09-06.json',
       'api/trips/TPE-KIX/2026-09-05_2026-09-13.json',
       'api/trips/TPE-KIX/2026-09-12_2026-09-20.json']
PRICES = [29800, 32200, 26800, 35600, 23100]   # NRT 每週最低價（變化值，避免水平折線 height=0）


def trip_map(urls, prices=None):
    """urls → {unitUrl: tripJson}；價格有變化（且 12000~42000 Y 範圍內）避免折線水平。"""
    out = {}
    for i, u in enumerate(urls):
        d = u.split('/')[-1].split('_')[0]
        r = u.split('/')[-1].split('_')[1].replace('.json', '')
        p = (prices or PRICES)[i % len(PRICES)]
        out[u] = mock_trip(d, r, [('JX 800', p, 'Available'), ('JX 802', p + 4000, 'Available')])
    return out


def mock_index(generated_at, trips, routes=('TPE-NRT', 'TPE-KIX')):
    """index 需含 routes/trips/generated_at（app.js fetchIndexWithEtag shape 驗證）。"""
    return {'generated_at': generated_at, 'routes': list(routes),
            'trips': list(trips), 'trip_count': len(trips)}


GEN_A = '2026-08-14T12:00:00.000Z'   # 本地快取版本
GEN_B = '2026-08-21T12:00:00.000Z'   # 伺服器較新版


def route_mock(page, index, trip_map_, etag_map=None, index_status=200, abort_keys=None):
    """攔截 /api/**：
       index.json → 200（可指定 index_status）/ trips → 帶 If-None-Match 且 etag 相符 → 304；
       abort_keys 內 → route.abort（網路錯誤，E1/E3/E4 用）；etag_map 缺 → 200 + 預設 etag。"""
    def handler(route):
        url = route.request.url
        if '/api/index.json' in url:
            if index_status != 200:
                route.fulfill(status=index_status, content_type='application/json', body='{}')
            else:
                route.fulfill(status=200, content_type='application/json', headers={'etag': '"idx"'},
                              body=json.dumps(index, ensure_ascii=False))
            return
        if '/api/trips/' in url:
            key = 'api/' + url.split('/api/')[-1]
            if abort_keys and key in abort_keys:
                route.abort('failed')
                return
            body = trip_map_.get(key)
            if body is None:
                route.fulfill(status=404, body='not found')
                return
            etag = (etag_map or {}).get(key)
            inm = route.request.headers.get('if-none-match', '')
            if etag and inm == etag:
                route.fulfill(status=304, headers={'etag': etag})
                return
            route.fulfill(status=200, content_type='application/json',
                          headers={'etag': etag or '"' + key + '"'},
                          body=json.dumps(body, ensure_ascii=False))
            return
        route.continue_()

    page.route('**/api/**', handler)


def route_abort_all_api(page):
    page.route('**/api/**', lambda route: route.abort('failed'))


# ═══════════════ 頁面 / 快取 helpers ═══════════════
def new_ctx(browser, online=True, quota=False, noidb=False, viewport=None):
    """獨立 context（儲存隔離 = E8 語意）；online=False → cookie offline=1（reload 後仍離線）；
    noidb=True → indexedDB.open 拋錯（E8 無痕 IDB 不可用模擬）。"""
    ctx = browser.new_context(viewport=viewport or {'width': 1280, 'height': 900})
    if not online:
        ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    ctx.add_init_script(INIT_ONLINE)
    if quota:
        ctx.add_init_script(INIT_QUOTA)
    if noidb:
        ctx.add_init_script(INIT_NOIDB)
    page = ctx.new_page()
    errs = []
    page.on('pageerror', lambda e: errs.append('PAGE: ' + str(e)))
    page.on('console', lambda m: errs.append('CONSOLE: ' + m.text) if m.type == 'error' else None)
    return ctx, page, errs


def set_online(page, on):
    page.evaluate('window.__setOnline(%s)' % ('true' if on else 'false'))


def read_cache(page):
    """頁內讀取 IDB 快取（OfflineCache.loadCache → {meta, units}）。"""
    return page.evaluate("""async () => {
      const s = OfflineCache.createIdbStorage();
      return await OfflineCache.loadCache(s);
    }""")


def wait_chart(page, timeout=10000):
    page.wait_for_selector('#chart path.price-line', timeout=timeout)
    page.wait_for_timeout(150)


def wait_status(page, text, timeout=10000):
    page.wait_for_function(
        "(t) => document.getElementById('syncStatus').textContent === t", arg=text, timeout=timeout)


def wait_sw_ready(page):
    """等待 SW active 且 app shell precache 完成（情境 D 離線 reload 前必備）。"""
    page.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.ready;
      const keys = await caches.keys();
      return reg.active && keys.includes('airtickets-shell-v3');
    }""", timeout=10000)


class ApiCounter:
    def __init__(self, page):
        self.index = 0
        self.trips = 0
        page.on('request', self._on)

    def _on(self, r):
        u = r.url
        if 'api/index.json' in u:
            self.index += 1
        elif '/api/trips/' in u:
            self.trips += 1


def unit_price(cache, url):
    """從快取讀該 unit 的 json 最新價（E4/E6 斷言舊版/新版內容）。"""
    rec = cache['units'].get(url)
    if not rec or not rec.get('json') or not rec['json'].get('flights'):
        return None
    return rec['json']['flights'][0]['history'][-1]['price_total']


# ═══════════════ 測試 ═══════════════
def run_tests(browser):
    # ═══ 情境 A：首次訪問連網（真實資料）═══
    print('\n── 情境 A：首次訪問（真實資料）──')
    ctx, page, errs = new_ctx(browser)
    page.goto(URL + '/web/')
    wait_chart(page)
    check('OFF-A1 圖表折線繪出', page.locator('#chart path.price-line').count() == 1)
    check('OFF-A2 更新時間 badge', '資料更新 2026-08-14' in page.locator('#updText').inner_text())
    c = read_cache(page)
    n_units = len(c['units'])
    check('OFF-A3 快取已寫入（40 個 NRT unit）', c is not None and n_units == 40,
          f'units={n_units}')
    check('OFF-A4 快取記錄 generated_at',
          c['meta']['generatedAt'] == '2026-08-14T22:46:53.000Z', c['meta']['generatedAt'])
    check('OFF-A5 手動更新按鈕可用', not page.locator('#refreshBtn').is_disabled())
    est = page.evaluate("""async () => {
      const s = OfflineCache.createIdbStorage();
      const cc = await OfflineCache.loadCache(s);
      return OfflineCache.estimateSize(cc.units);
    }""")
    check('OFF-A6 全量容量在配額內（EC4）', est['overQuota'] is False and est['bytes'] > 0,
          repr(est))
    check('OFF-A7 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 情境 B：二次訪問・連網・無新資料（真實資料；快取秒開 0 trip 請求）═══
    print('\n── 情境 B：再次訪問無新資料（真實資料）──')
    ctx, page, errs = new_ctx(browser)
    page.goto(URL + '/web/')
    wait_chart(page)
    counter = ApiCounter(page)
    page.reload()
    wait_chart(page)
    check('OFF-B1 快取秒開（無骨架等待）',
          page.locator('#skeleton').evaluate('el => el.hidden') and
          page.locator('#chart path.price-line').count() == 1)
    wait_status(page, '已是最新')
    check('OFF-B2 背景只抓 index 1 次', counter.index == 1, f'index={counter.index}')
    check('OFF-B3 0 個 trip 請求（decideSync=fresh）', counter.trips == 0, f'trips={counter.trips}')
    check('OFF-B4 顯示「已是最新」', not page.locator('#syncStatus').evaluate('el => el.hidden'))
    check('OFF-B5 updText 更新為資料日期',
          '資料更新 2026-08-14' in page.locator('#updText').inner_text())
    check('OFF-B6 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 情境 C：再次訪問・連網・有新資料（mock 增量：只補載變更/新增）═══
    print('\n── 情境 C：再次訪問有新資料（mock 增量）──')
    ctx, page, errs = new_ctx(browser)
    trips_a = NRT[:4]
    route_mock(page, mock_index(GEN_A, trips_a), trip_map(trips_a),
               etag_map={u: '"' + u + '"' for u in trips_a})
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    # 第二輪：新版 index（+1 新增 trip），變更第 0 個（新價 15000）
    trips_b = NRT[:5]
    tm_b = trip_map(trips_b, [15000] + PRICES[1:])
    et_b = {u: '"' + u + '"' for u in trips_a}
    et_b[NRT[0]] = '"changed"'
    et_b[NRT[4]] = '"new"'
    page.unroute('**/api/**')
    route_mock(page, mock_index(GEN_B, trips_b), tm_b, etag_map=et_b)
    got200, got304 = [], []
    page.on('response', lambda r: got200.append(r.url.split('/')[-1])
            if r.status == 200 and '/api/trips/' in r.url else None)
    page.on('response', lambda r: got304.append(r.url.split('/')[-1])
            if r.status == 304 and '/api/trips/' in r.url else None)
    page.reload()
    wait_chart(page)
    wait_status(page, '已是最新')
    check('OFF-C1 只補載變更+新增 2 檔（200）',
          sorted(got200) == sorted([u.split('/')[-1] for u in [NRT[0], NRT[4]]]),
          sorted(got200))
    check('OFF-C2 未變更 3 檔條件式 304（零 body）', len(got304) == 3, sorted(got304))
    check('OFF-C3 圖表更新為 5 週', '共 5 週' in page.locator('#chartTitle').inner_text())
    c = read_cache(page)
    check('OFF-C4 快取 generated_at 推進', c['meta']['generatedAt'] == GEN_B, c['meta']['generatedAt'])
    check('OFF-C5 updText 顯示新版日期', '資料更新 2026-08-21' in page.locator('#updText').inner_text())
    check('OFF-C6 顯示「已是最新」', not page.locator('#syncStatus').evaluate('el => el.hidden'))
    check('OFF-C7 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 情境 D：離線冷啟動（SW 兜 shell + IDB 繪圖 + 0 api 請求，真實資料）═══
    print('\n── 情境 D：離線冷啟動（SW + IDB，真實資料）──')
    ctx, page, errs = new_ctx(browser)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    counter = ApiCounter(page)
    ctx.set_offline(True)
    ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    resp = page.reload()
    wait_chart(page)
    check('OFF-D1 頁面 shell 由 SW 提供',
          resp is not None and resp.from_service_worker, 'from_service_worker')
    check('OFF-D2 圖表以 IDB 快取繪出', page.locator('#chart path.price-line').count() == 1)
    off_text = page.locator('#offBar').inner_text()
    check('OFF-D3 離線橫幅顯示上次更新 HH:MM',
          not page.locator('#offBar').evaluate('el => el.hidden') and
          off_text.startswith('離線模式 · 顯示上次資料（') and
          len(off_text) > len('離線模式 · 顯示上次資料（') + 2, repr(off_text))
    check('OFF-D4 手動更新停用「離線中，無法更新」',
          page.locator('#refreshBtn').is_disabled() and
          page.locator('#refreshBtn').inner_text() == '離線中，無法更新')
    check('OFF-D5 0 個 api/ 請求', counter.index == 0 and counter.trips == 0,
          f'index={counter.index} trips={counter.trips}')
    check('OFF-D6 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ E1：首次訪問即離線 → 錯誤卡 + 重試（cookie offline + api abort；transport 保持連網讓 shell 可載入）═══
    print('\n── E1：首次訪問即離線 ──')
    ctx, page, errs = new_ctx(browser, online=False)
    page.route('**/api/index.json', lambda r: r.abort('failed'))
    page.goto(URL + '/web/')
    page.wait_for_selector('#errBox:not([hidden])', timeout=10000)
    check('OFF-E1-1 錯誤卡顯示', page.locator('#errBox').is_visible())
    detail = page.locator('#errDetail').inner_text()
    check('OFF-E1-2 ERR_OFFLINE_FIRST + 需要網路文案',
          'ERR_OFFLINE_FIRST' in detail and '需要網路才能首次載入資料' in detail, detail)
    check('OFF-E1-3 重試按鈕存在', page.locator('#retryBtn').count() == 1)
    check('OFF-E1-4 無 pageerror（錯誤卡不炸 JS）',
          len([e for e in errs if e.startswith('PAGE:')]) == 0)
    # 連網後重試 → 首次載入流程成功
    page.unroute('**/api/index.json')
    ctx.clear_cookies()
    set_online(page, True)
    route_mock(page, mock_index(GEN_A, NRT), trip_map(NRT))
    page.locator('#retryBtn').click()
    wait_chart(page)
    check('OFF-E1-5 重試後正常顯示圖表', page.locator('#chart path.price-line').count() == 1)
    check('OFF-E1-6 重試後更新時間顯示', '資料更新 2026-08-14' in page.locator('#updText').inner_text())
    check('OFF-E1-7 重試後手動更新可用', not page.locator('#refreshBtn').is_disabled())
    ctx.close()

    # ═══ E2：離線切到從未載入航線 → tab 提示 + 停留原航線 ═══
    print('\n── E2：離線切未載入航線 ──')
    ctx, page, errs = new_ctx(browser)
    route_mock(page, mock_index(GEN_A, NRT[:5]), trip_map(NRT[:5]))
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    set_online(page, False)
    route_abort_all_api(page)
    counter = ApiCounter(page)
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_selector('#routeTabs button[data-route="TPE-KIX"] .hint', timeout=5000)
    hint = page.locator('#routeTabs button[data-route="TPE-KIX"] .hint').inner_text()
    check('OFF-E2-1 tab 提示「此航線尚未下載，需連網」',
          '此航線尚未下載，需連網' in hint, repr(hint))
    check('OFF-E2-2 停留原航線（東京）',
          '東京' in page.locator('#chartTitle').inner_text())
    check('OFF-E2-3 不跳出錯誤卡/空狀態',
          page.locator('#errBox').evaluate('el => el.hidden') and
          page.locator('#emptyBox').evaluate('el => el.hidden'))
    check('OFF-E2-4 離線後 0 api 請求', counter.index == 0 and counter.trips == 0,
          f'index={counter.index} trips={counter.trips}')
    check('OFF-E2-5 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ EC1：離線切已快取航線仍可完整操作 ═══
    print('\n── EC1：離線切已快取航線 ──')
    ctx, page, errs = new_ctx(browser)
    route_mock(page, mock_index(GEN_A, NRT[:5] + KIX), trip_map(NRT[:5] + KIX))
    page.goto(URL + '/web/')
    wait_chart(page)
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_selector('#chartTitle:has-text("大阪")', timeout=10000)
    page.wait_for_timeout(400)
    set_online(page, False)
    route_abort_all_api(page)
    counter = ApiCounter(page)
    page.locator('#routeTabs button[data-route="TPE-NRT"]').click()
    page.wait_for_selector('#chartTitle:has-text("東京")', timeout=5000)
    check('OFF-EC1-1 離線切回東京圖表正常',
          page.locator('#chart path.price-line').count() == 1 and
          not page.locator('#summary').evaluate('el => el.hidden'))
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_selector('#chartTitle:has-text("大阪")', timeout=5000)
    check('OFF-EC1-2 離線切大阪圖表正常',
          page.locator('#chart path.price-line').count() == 1 and
          not page.locator('#summary').evaluate('el => el.hidden'))
    page.locator('#flightSel').select_option(label='航班 JX 800')
    page.wait_for_timeout(250)
    check('OFF-EC1-3 離線航班篩選正常', '航班 JX 800' in page.locator('#chartTitle').inner_text())
    page.locator('#flightSel').select_option(label='全部（每週最低價）')
    page.wait_for_timeout(200)
    check('OFF-EC1-4 離線切換 0 api 請求', counter.index == 0 and counter.trips == 0,
          f'index={counter.index} trips={counter.trips}')
    check('OFF-EC1-5 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ E3：背景比對 index 抓取失敗 → 不中斷瀏覽 + 更新失敗提示 + 手動更新恢復 ═══
    print('\n── E3：背景比對失敗 ──')
    ctx, page, errs = new_ctx(browser)
    tm = trip_map(NRT[:5])
    et = {u: '"' + u + '"' for u in NRT[:5]}
    route_mock(page, mock_index(GEN_A, NRT[:5]), tm, etag_map=et)
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    page.unroute('**/api/**')
    route_mock(page, mock_index(GEN_A, NRT[:5]), tm, etag_map=et, index_status=500)
    page.reload()
    wait_chart(page)
    page.wait_for_function(
        "document.getElementById('syncStatus').textContent === '更新失敗，稍後自動重試'", timeout=10000)
    check('OFF-E3-1 顯示「更新失敗，稍後自動重試」',
          '更新失敗，稍後自動重試' in page.locator('#syncStatus').inner_text())
    check('OFF-E3-2 圖表瀏覽不中斷', page.locator('#chart path.price-line').count() == 1)
    check('OFF-E3-3 不顯示錯誤卡', page.locator('#errBox').evaluate('el => el.hidden'))
    page.locator('#rangeSeg button[data-range="6m"]').click()
    page.wait_for_timeout(250)
    check('OFF-E3-4 圖表操作仍正常', '顯示 6 個月' in page.locator('#chartTitle').inner_text())
    # 恢復 → 手動更新重新嘗試成功
    page.unroute('**/api/**')
    route_mock(page, mock_index(GEN_A, NRT[:5]), tm, etag_map=et)
    page.locator('#refreshBtn').click()
    wait_status(page, '已是最新')
    check('OFF-E3-5 手動更新後「已是最新」',
          not page.locator('#syncStatus').evaluate('el => el.hidden'))
    check('OFF-E3-6 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ E4：增量補載部分失敗 → 保留舊版 + 部分更新失敗 + 下次重試 ═══
    print('\n── E4：增量部分失敗 ──')
    ctx, page, errs = new_ctx(browser)
    trips_a = NRT[:5]
    tm_a = trip_map(trips_a)
    et_a = {u: '"' + u + '"' for u in trips_a}
    route_mock(page, mock_index(GEN_A, trips_a), tm_a, etag_map=et_a)
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    # 第二輪：伺服器 6 檔（+1 新增），其中 NRT[2] 網路錯誤
    trips_b = NRT[:6]
    tm_b = trip_map(trips_b)
    et_b = {u: '"' + u + '"' for u in trips_b}
    page.unroute('**/api/**')
    route_mock(page, mock_index(GEN_B, trips_b), tm_b, etag_map=et_b, abort_keys={NRT[2]})
    page.reload()
    wait_chart(page)
    page.wait_for_function(
        "document.getElementById('syncStatus').textContent === '部分資料更新失敗'", timeout=10000)
    check('OFF-E4-1 顯示「部分資料更新失敗」',
          '部分資料更新失敗' in page.locator('#syncStatus').inner_text())
    c = read_cache(page)
    check('OFF-E4-2 成功的新檔已補載（6 units）', len(c['units']) == 6, f'units={len(c["units"])}')
    check('OFF-E4-3 失敗檔保留舊版內容',
          unit_price(c, NRT[2]) == PRICES[2], unit_price(c, NRT[2]))
    check('OFF-E4-4 失敗 URL 記入 retryList', NRT[2] in c['meta']['retryList'], c['meta']['retryList'])
    check('OFF-E4-5 圖表仍顯示（6 週）', '共 6 週' in page.locator('#chartTitle').inner_text())
    # 第三輪：下次更新（更新版）重試失敗檔 → 補齊
    et_c = {u: '"' + u + '"' for u in trips_b}
    et_c[NRT[2]] = '"fixed"'
    tm_c = trip_map(trips_b, [15000, 32200, 17500, 35600, 23100, 41000])
    page.unroute('**/api/**')
    route_mock(page, mock_index('2026-08-28T12:00:00.000Z', trips_b), tm_c, etag_map=et_c)
    page.reload()
    wait_chart(page)
    wait_status(page, '已是最新')
    c2 = read_cache(page)
    check('OFF-E4-6 下次更新重試成功（已是最新）',
          not page.locator('#syncStatus').evaluate('el => el.hidden'))
    check('OFF-E4-7 retryList 已清空', c2['meta']['retryList'] == [], c2['meta']['retryList'])
    check('OFF-E4-8 失敗檔已更新為新價格', unit_price(c2, NRT[2]) == 17500, unit_price(c2, NRT[2]))
    check('OFF-E4-9 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ E5：儲存空間不足 → 降級只保留目前航線 ═══
    print('\n── E5：空間不足降級 ──')
    ctx, page, errs = new_ctx(browser, quota=True)
    trips2 = NRT[:5] + KIX
    route_mock(page, mock_index(GEN_A, trips2), trip_map(trips2))
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    check('OFF-E5-0 quota 注入就緒', page.evaluate('window.__quotaPatched') is True)
    page.evaluate('window.__quotaMode = true; window.__quotaMax = 5;')
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_selector('#chartTitle:has-text("大阪")', timeout=10000)
    page.wait_for_timeout(600)
    check('OFF-E5-1 切 KIX 圖表正常', page.locator('#chart path.price-line').count() == 1)
    c = read_cache(page)
    routes_in_cache = sorted(set(u.split('/')[2] for u in c['units']))
    check('OFF-E5-2 降級只保留目前航線（KIX 5 units）',
          routes_in_cache == ['TPE-KIX'] and len(c['units']) == 5, f'{routes_in_cache} n={len(c["units"])}')
    check('OFF-E5-3 routeLoadedAt 只剩 KIX',
          list(c['meta']['routeLoadedAt'].keys()) == ['TPE-KIX'], c['meta']['routeLoadedAt'])
    check('OFF-E5-4 頁面仍正常運作（Summary 可見）',
          not page.locator('#summary').evaluate('el => el.hidden'))
    # 降級後 NRT 已捨棄：離線切 NRT → 提示（E2 語意兜底）
    set_online(page, False)
    page.locator('#routeTabs button[data-route="TPE-NRT"]').click()
    page.wait_for_selector('#routeTabs button[data-route="TPE-NRT"] .hint', timeout=5000)
    check('OFF-E5-5 捨棄的 NRT 離線顯示提示',
          '此航線尚未下載，需連網' in page.locator('#routeTabs button[data-route="TPE-NRT"] .hint').inner_text())
    check('OFF-E5-6 停留 KIX（未白屏）', '大阪' in page.locator('#chartTitle').inner_text())
    check('OFF-E5-7 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ E6：伺服器檔案 404 → 本地快取移除 ═══
    print('\n── E6：伺服器 404 移除本地 ──')
    ctx, page, errs = new_ctx(browser)
    trips_a = NRT[:5]
    tm_a = trip_map(trips_a)
    et_a = {u: '"' + u + '"' for u in trips_a}
    route_mock(page, mock_index(GEN_A, trips_a), tm_a, etag_map=et_a)
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    # 第二輪：同清單，但 NRT[3] 回 404（伺服器已移除）
    trips_b = NRT[:5]
    tm_b = {u: (tm_a[u] if u != NRT[3] else None) for u in trips_b}
    page.unroute('**/api/**')
    route_mock(page, mock_index(GEN_B, trips_b), tm_b, etag_map=et_a)
    page.reload()
    wait_chart(page)
    wait_status(page, '已是最新')
    c = read_cache(page)
    check('OFF-E6-1 404 檔已自本地移除（4 units）',
          len(c['units']) == 4 and NRT[3] not in c['units'], f'units={len(c["units"])}')
    check('OFF-E6-2 不顯示錯誤卡', page.locator('#errBox').evaluate('el => el.hidden'))
    check('OFF-E6-3 圖表照常顯示（移除週以 gap-dot 佔位）',
          page.locator('#chart path.price-line').count() == 1 and
          page.locator('#chart circle.gap-dot').count() >= 1 and
          '共 5 週' in page.locator('#chartTitle').inner_text(),
          page.locator('#chartTitle').inner_text())
    check('OFF-E6-4 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ E7：離線手動更新停用 + 恢復 ═══
    print('\n── E7：離線手動更新 ──')
    ctx, page, errs = new_ctx(browser)
    route_mock(page, mock_index(GEN_A, NRT[:5]), trip_map(NRT[:5]))
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    set_online(page, False)
    page.wait_for_selector('#offBar:not([hidden])', timeout=5000)
    check('OFF-E7-1 離線橫幅顯示',
          '離線模式 · 顯示上次資料（' in page.locator('#offBar').inner_text())
    check('OFF-E7-2 按鈕停用 + 文案「離線中，無法更新」',
          page.locator('#refreshBtn').is_disabled() and
          page.locator('#refreshBtn').inner_text() == '離線中，無法更新')
    page.locator('#refreshBtn').click(force=True)   # disabled 按鈕點擊不觸發
    page.wait_for_timeout(200)
    check('OFF-E7-3 離線下點擊不觸發更新（維持離線狀態）',
          page.locator('#offBar').is_visible() and page.locator('#refreshBtn').is_disabled())
    set_online(page, True)
    page.wait_for_function("document.getElementById('offBar').hidden", timeout=5000)
    check('OFF-E7-4 恢復連網按鈕可用',
          not page.locator('#refreshBtn').is_disabled() and
          page.locator('#refreshBtn').inner_text() == '手動更新')
    check('OFF-E7-5 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ E8：無痕 / 新 profile → 等同首次訪問（空快取）═══
    print('\n── E8：無痕/新 profile ──')
    ctx_a, page_a, errs_a = new_ctx(browser)
    route_mock(page_a, mock_index(GEN_A, NRT[:5]), trip_map(NRT[:5]))
    page_a.goto(URL + '/web/')
    wait_chart(page_a)
    check('OFF-E8-0 context A 正常載入', page_a.locator('#chart path.price-line').count() == 1)
    ctx_a.close()
    # context B：無痕語意（全新儲存）→ 首次訪問
    ctx_b, page_b, errs_b = new_ctx(browser)
    route_mock(page_b, mock_index(GEN_A, NRT[:5]), trip_map(NRT[:5]))
    page_b.goto(URL + '/web/')
    wait_chart(page_b)
    check('OFF-E8-1 新 profile 視為首次訪問（無「上次更新」）',
          '上次更新' not in page_b.locator('#updText').inner_text() and
          '資料更新 2026-08-14' in page_b.locator('#updText').inner_text())
    check('OFF-E8-2 首次訪問圖表正常', page_b.locator('#chart path.price-line').count() == 1)
    # B 載入一次後才具備快取：離線開啟有快取可用
    page_b.wait_for_timeout(300)
    ctx_b.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])   # reload 後仍離線
    route_abort_all_api(page_b)
    counter = ApiCounter(page_b)
    page_b.reload()
    wait_chart(page_b)
    check('OFF-E8-3 B 第二次訪問離線可用（快取繪圖 + 橫幅）',
          page_b.locator('#offBar').is_visible() and
          page_b.locator('#chart path.price-line').count() == 1)
    check('OFF-E8-4 離線 0 api 請求', counter.index == 0 and counter.trips == 0,
          f'index={counter.index} trips={counter.trips}')
    check('OFF-E8-5 無 pageerror', len([e for e in errs_b if e.startswith('PAGE:')]) == 0)
    ctx_b.close()

    # ═══ E8-無痕：indexedDB.open 拋錯（IDB 完全不可用）→ 記憶體快取降級 ═══
    # 頁面照常運作（連網載入繪圖）→ 同 session 離線用記憶體快取 → reload 回首次訪問（無「上次更新」）
    print('\n── E8-無痕：IDB 不可用 → 記憶體快取降級 ──')
    ctx, page, errs = new_ctx(browser, noidb=True)
    route_mock(page, mock_index(GEN_A, NRT[:5] + KIX), trip_map(NRT[:5] + KIX))
    page.goto(URL + '/web/')
    wait_chart(page)
    check('OFF-E8I-1 連網照常載入繪圖（IDB 不可用）',
          page.locator('#chart path.price-line').count() == 1)
    check('OFF-E8I-2 等同首次訪問（無「上次更新」，顯示資料更新）',
          '上次更新' not in page.locator('#updText').inner_text() and
          '資料更新 2026-08-14' in page.locator('#updText').inner_text(),
          page.locator('#updText').inner_text())
    threw = page.evaluate("""async () => {
      try {
        const s = OfflineCache.createIdbStorage();
        await OfflineCache.loadCache(s);
        return false;
      } catch (e) { return true; }
    }""")
    check('OFF-E8I-3 loadCache 拋錯（indexedDB.open 失敗）', threw is True)
    check('OFF-E8I-4 無 pageerror（降級不炸 JS）',
          len([e for e in errs if e.startswith('PAGE:')]) == 0)
    # 連網切 KIX → 本 session 載入記憶體（寫回 IDB 失敗被靜默吞掉）
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_selector('#chartTitle:has-text("大阪")', timeout=10000)
    page.wait_for_timeout(300)
    check('OFF-E8I-5 連網切 KIX 圖表正常',
          page.locator('#chart path.price-line').count() == 1)
    # 同 session 離線 → 記憶體快取（routeCache / CACHE）仍可繪圖，0 api 請求
    set_online(page, False)
    page.unroute('**/api/**')
    route_abort_all_api(page)
    counter = ApiCounter(page)
    page.locator('#routeTabs button[data-route="TPE-NRT"]').click()
    page.wait_for_selector('#chartTitle:has-text("東京")', timeout=5000)
    check('OFF-E8I-6 離線切回 NRT 以記憶體快取繪圖',
          page.locator('#chart path.price-line').count() == 1 and
          not page.locator('#summary').evaluate('el => el.hidden'))
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_selector('#chartTitle:has-text("大阪")', timeout=5000)
    check('OFF-E8I-7 離線切 KIX 圖表正常（本 session 已載入）',
          page.locator('#chart path.price-line').count() == 1)
    check('OFF-E8I-8 離線 0 api 請求', counter.index == 0 and counter.trips == 0,
          f'index={counter.index} trips={counter.trips}')
    check('OFF-E8I-9 離線橫幅顯示上次資料（記憶體 meta）',
          page.locator('#offBar').is_visible() and
          '離線模式 · 顯示上次資料（' in page.locator('#offBar').inner_text())
    check('OFF-E8I-10 手動更新停用',
          page.locator('#refreshBtn').is_disabled())
    check('OFF-E8I-11 無 pageerror',
          len([e for e in errs if e.startswith('PAGE:')]) == 0)
    # 重新載入（新 session，連網）→ IDB 仍不可用 → 回到首次訪問（無「上次更新」）
    page.unroute('**/api/**')
    route_mock(page, mock_index(GEN_A, NRT[:5] + KIX), trip_map(NRT[:5] + KIX))
    set_online(page, True)
    page.reload()
    wait_chart(page)
    check('OFF-E8I-12 新 session 回到首次訪問（無「上次更新」）',
          '上次更新' not in page.locator('#updText').inner_text() and
          '資料更新 2026-08-14' in page.locator('#updText').inner_text(),
          page.locator('#updText').inner_text())
    check('OFF-E8I-13 新 session 圖表正常（無痕等同首次訪問）',
          page.locator('#chart path.price-line').count() == 1)
    check('OFF-E8I-14 無 pageerror',
          len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ EC3：斷線重連 → 不需重新整理自動恢復更新 ═══
    print('\n── EC3：斷線重連自動恢復 ──')
    ctx, page, errs = new_ctx(browser)
    route_mock(page, mock_index(GEN_A, NRT[:5]), trip_map(NRT[:5]))
    page.goto(URL + '/web/')
    wait_chart(page)
    page.wait_for_timeout(300)
    set_online(page, False)
    route_abort_all_api(page)
    page.wait_for_selector('#offBar:not([hidden])', timeout=5000)
    check('OFF-EC3-1 斷線 → 離線橫幅 + 按鈕停用',
          page.locator('#offBar').is_visible() and page.locator('#refreshBtn').is_disabled())
    page.unroute('**/api/**')
    route_mock(page, mock_index(GEN_A, NRT[:5]), trip_map(NRT[:5]))
    set_online(page, True)
    wait_status(page, '已是最新')
    check('OFF-EC3-2 重連後自動恢復（無需 reload）',
          page.locator('#offBar').evaluate('el => el.hidden'))
    check('OFF-EC3-3 重連後按鈕恢復可用', not page.locator('#refreshBtn').is_disabled())
    check('OFF-EC3-4 更新狀態回到最新',
          not page.locator('#syncStatus').evaluate('el => el.hidden'))
    check('OFF-EC3-5 無 pageerror', len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ 手動更新：連網強制重新驗證 → 已是最新（index +1、0 trips）═══
    print('\n── 手動更新（force 重新驗證）──')
    ctx, page, errs = new_ctx(browser)
    route_mock(page, mock_index(GEN_A, NRT[:5]), trip_map(NRT[:5]))
    page.goto(URL + '/web/')
    wait_chart(page)
    page.reload()                       # 二次訪問：快取秒開 + 背景比對 → 已是最新
    wait_chart(page)
    wait_status(page, '已是最新')
    counter = ApiCounter(page)
    page.locator('#refreshBtn').click()
    wait_status(page, '已是最新')
    check('OFF-M1 強制重新抓取 index（+1）', counter.index == 1, f'index={counter.index}')
    check('OFF-M2 比對相同 0 個 trip 請求', counter.trips == 0, f'trips={counter.trips}')
    check('OFF-M3 顯示「已是最新」', not page.locator('#syncStatus').evaluate('el => el.hidden'))
    check('OFF-M4 資料維持原狀（更新日期不變）',
          '資料更新 2026-08-14' in page.locator('#updText').inner_text())
    check('OFF-M5 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()


def main():
    global URL
    httpd, port = start_server()
    URL = f'http://127.0.0.1:{port}'
    print(f'server: {URL}/web/')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chromium_path, args=['--no-sandbox'])
            run_tests(browser)
            browser.close()
    finally:
        httpd.shutdown()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f'\n═══ 離線 E2E 結果：{passed}/{total} 通過 ═══')
    for name, ok, detail in RESULTS:
        if not ok:
            print('  ❌', name, '←', detail)
    sys.exit(0 if passed == total else 1)


if __name__ == '__main__':
    main()
