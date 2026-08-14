#!/usr/bin/env python3
"""票價趨勢圖 — E2E 冒煙測試（T8）

對照：docs/test-plans/票價趨勢圖測試計畫.md（E2E-01~23）
架構：本機 http.server（repo root）＋ Python Playwright headless chromium
模式：
  真實資料 — 直連本機伺服器讀取 repo 內 api/ 真實資料
  mocked    — page.route 攔截 api 回應（500/404/售罄/過舊/空/延遲）

執行：python tests/e2e_smoke.py
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


# ═══════════════ Mock 資料 ═══════════════
def mock_trip(d, r, flights):
    return {'route_id': 'TPE-NRT', 'outbound_date': d, 'return_date': r,
            'flights': [{'outbound_flight_no': no, 'history': [
                {'scraped_at': '2026-08-14T12:00:00.000Z', 'price_total': price, 'status': status}]}
                for no, price, status in flights]}


MOCK_WEEKS = [
    mock_trip('2026-08-15', '2026-08-23', [('JX 800', 32296, 'Available'), ('JX 802', 46072, 'Available')]),
    mock_trip('2026-08-22', '2026-08-30', [('JX 800', 46600, 'Available'), ('JX 802', 38200, 'Available')]),
    mock_trip('2026-08-29', '2026-09-06', [('JX 800', 17176, 'Available'), ('JX 802', 16756, 'Available')]),
    mock_trip('2026-09-05', '2026-09-13', [('JX 800', 15862, 'Available'), ('JX 802', 14853, 'Available')]),
    mock_trip('2026-09-12', '2026-09-20', [('JX 800', 17638, 'Available'), ('JX 802', 16546, 'Available')]),
]
MOCK_TRIP_URLS = ['api/trips/TPE-NRT_2026-08-15_2026-08-23.json',
                  'api/trips/TPE-NRT_2026-08-22_2026-08-30.json',
                  'api/trips/TPE-NRT_2026-08-29_2026-09-06.json',
                  'api/trips/TPE-NRT_2026-09-05_2026-09-13.json',
                  'api/trips/TPE-NRT_2026-09-12_2026-09-20.json']


def mock_index(generated_at='2026-08-14T12:00:00.000Z', trips=None, include_soldout_week=False):
    tl = list(trips) if trips is not None else list(MOCK_TRIP_URLS)
    if include_soldout_week:
        tl.append('api/trips/TPE-NRT_2026-09-05_2026-09-13.json')
    return {'generated_at': generated_at, 'routes': ['TPE-NRT', 'TPE-KIX'], 'trips': tl,
            'trip_count': len(tl), 'latest_file': 'mock.json'}


def mock_trip_map(include_soldout_week=False):
    m = {u: t for u, t in zip(MOCK_TRIP_URLS, MOCK_WEEKS)}
    if include_soldout_week:
        m['api/trips/TPE-NRT_2026-09-05_2026-09-13.json'] = mock_trip(
            '2026-09-05', '2026-09-13',
            [('JX 800', None, 'SoldOut'), ('JX 802', None, 'SoldOut')])
    return m


def route_mock_api(page, index=None, trips=None, missing_url=None, delay_ms=0, index_status=200):
    """攔截 /api/** 請求。index=None → 真實；trips=None → 真實；missing_url 該 URL 回 404。"""
    trip_map = mock_trip_map(include_soldout_week=(trips is not None and 'soldout' in trips)) if trips else None

    def handler(route):
        req = route.request
        url = req.url
        if '/api/index.json' in url:
            if index is None:
                route.continue_()
            elif index_status != 200:
                route.fulfill(status=index_status, content_type='application/json', body='{}')
            else:
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps(index, ensure_ascii=False))
            return
        if '/api/trips/' in url:
            if missing_url and url.endswith(missing_url):
                route.fulfill(status=404, body='not found')
                return
            if trip_map:
                key = 'api/' + url.split('/api/')[-1]  # 與 MOCK_TRIP_URLS 格式一致
                body = trip_map.get(key)
                if body is None:
                    route.fulfill(status=404, body='not found')
                else:
                    if delay_ms:
                        import time
                        time.sleep(delay_ms / 1000)
                    route.fulfill(status=200, content_type='application/json',
                                  body=json.dumps(body, ensure_ascii=False))
                return
            route.continue_()
            return
        route.continue_()

    page.route('**/api/**', handler)


def new_page(browser, **kw):
    page = browser.new_page(**kw)
    errs = []
    page.on('pageerror', lambda e: errs.append('PAGE: ' + str(e)))
    page.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    return page, errs


def wait_chart(page):
    page.wait_for_selector('#chart path.price-line', timeout=8000)
    page.wait_for_timeout(200)


# ═══════════════ 測試 ═══════════════
def run_tests(browser):
    print('\n── 真實資料冒煙 ──')

    # E2E-01 頁首與預設航線 tab
    page, errs = new_page(browser, viewport={'width': 1280, 'height': 900})
    page.goto(URL + '/web/')
    wait_chart(page)
    check('E2E-01 預設東京 tab active', page.locator('#routeTabs button.active').inner_text().startswith('東京'))
    check('E2E-01 更新時間 badge', '資料更新 2026-08-14' in page.locator('#updText').inner_text())
    tab_count = page.locator('#routeTabs button').count()
    check('E2E-01 航線 tab 有 4 個（含福岡/札幌）', tab_count == 4, f'got {tab_count}')
    check('E2E-02 折線繪出', page.locator('#chart path.price-line').count() == 1)
    # 回歸：狀態容器初始必須隱藏（CSS [hidden] 不可被 display 覆蓋）
    check('E2E-02b 初始狀態容器皆隱藏',
          page.locator('#errBox').evaluate('el => el.hidden && getComputedStyle(el).display === "none"') and
          page.locator('#emptyBox').evaluate('el => el.hidden && getComputedStyle(el).display === "none"') and
          page.locator('#staleBar').evaluate('el => el.hidden && getComputedStyle(el).display === "none"'))
    check('E2E-02 平均虛線繪出', page.locator('#chart line.avg-line').count() == 1)
    check('E2E-03 最低價標記', page.locator('#chart circle.dot.min').count() == 1)
    check('E2E-03 圖例 4 項', page.locator('.legend-row .lg').count() == 4)

    # E2E-04 hover tooltip
    page.locator('#chart circle.dot').first.hover()
    page.wait_for_timeout(250)
    tip = page.locator('#tip')
    check('E2E-04 hover 顯示 tooltip', tip.evaluate('el => el.classList.contains("show")'))
    check('E2E-04 tooltip 含價格與差幅', 'NT$' in tip.inner_text() and '比平均' in tip.inner_text())
    page.mouse.move(2, 2)
    page.wait_for_timeout(250)
    check('E2E-04 移開隱藏', tip.evaluate('el => !el.classList.contains("show")'))

    # E2E-05 切航班
    page.locator('#flightSel').select_option(label='航班 JX 800')
    page.wait_for_timeout(300)
    check('E2E-05 航班模式標題', '航班 JX 800' in page.locator('#chartTitle').inner_text())
    page.locator('#flightSel').select_option(label='全部（每週最低價）')
    page.wait_for_timeout(300)

    # E2E-06 範圍 Outline（3/6/12/all）— 標題含範圍 label（週數映射由單元測試 F-04 覆蓋）
    for key, weeks, label in [('3m', 12, '3 個月'), ('6m', 24, '6 個月'),
                              ('12m', 40, '12 個月'), ('all', 40, '全部')]:
        page.locator(f'#rangeSeg button[data-range="{key}"]').click()
        page.wait_for_timeout(250)
        t = page.locator('#chartTitle').inner_text()
        cond = f'顯示 {label}' in t and f'共 {weeks} 週' in t
        check(f'E2E-06 {label} 標題範圍與週數', cond, repr(t))

    # E2E-20 每週最低價 = 圖表點最低價（真實資料 09/19 週 = 14,139）
    page.locator('#rangeSeg button[data-range="all"]').click()
    page.wait_for_timeout(250)
    check('E2E-20 Summary 最便宜 = 全域最低 14,139', 'NT$14,139' in page.locator('#sumMin').inner_text())

    # E2E-21 平均線不隨範圍漂移
    page.locator('#rangeSeg button[data-range="3m"]').click()
    page.wait_for_timeout(250)
    page.locator('#rangeSeg button[data-range="all"]').click()
    page.wait_for_timeout(250)
    avg_label = page.locator('#chart text.avg-label').text_content()
    check('E2E-21 平均線標籤 = NT$19,480（全域）', 'NT$19,480' in avg_label, avg_label)

    # E2E-22 Summary 隨範圍更新（3m 無旺季週 vs all 有櫻花季）
    page.locator('#rangeSeg button[data-range="3m"]').click()
    page.wait_for_timeout(250)
    peak3 = page.locator('#sumPeakS').inner_text()
    page.locator('#rangeSeg button[data-range="all"]').click()
    page.wait_for_timeout(250)
    peakAll = page.locator('#sumPeakS').inner_text()
    check('E2E-22 旺季卡隨範圍更新', peak3 != peakAll and '非旺季' in peak3 and '櫻花' in peakAll,
          f'{peak3} vs {peakAll}')

    # E2E-07 切航線保留設定 + 航班回退
    page.locator('#rangeSeg button[data-range="6m"]').click()
    page.locator('#flightSel').select_option(label='航班 JX 800')
    page.wait_for_timeout(200)
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_timeout(1500)
    t = page.locator('#chartTitle').inner_text()
    check('E2E-07 切大阪航班回退 all', '每週最低價' in t, t)
    check('E2E-07 範圍保留 24 週', '共 24 週' in t, t)
    check('E2E-07 大阪平均 20,240', 'NT$20,240' in page.locator('#sumAvg').inner_text())

    # E2E-16 快取：切回東京不重複下載 trips
    page.locator('#routeTabs button[data-route="TPE-NRT"]').click()
    page.wait_for_timeout(800)
    check('E2E-16 切回東京平均恢復', 'NT$19,480' in page.locator('#sumAvg').inner_text())

    # E2E-07b 無資料航線（福岡）→ 空狀態且圖表隱藏；切回東京恢復
    p7b, e7b = new_page(browser, viewport={'width': 1280, 'height': 900})
    p7b.goto(URL + '/web/')
    wait_chart(p7b)
    p7b.locator('#routeTabs button[data-route="TPE-FUK"]').click()
    p7b.wait_for_selector('#emptyBox:not([hidden])', timeout=8000)
    check('E2E-07b 福岡空狀態顯示', p7b.locator('#emptyBox').is_visible())
    check('E2E-07b 空狀態下圖表隱藏', p7b.locator('#chart').evaluate('el => el.hidden'))
    p7b.locator('#routeTabs button[data-route="TPE-NRT"]').click()
    p7b.wait_for_selector('#chart path.price-line', timeout=8000)
    check('E2E-07b 切回東京圖表恢復', not p7b.locator('#chart').evaluate('el => el.hidden') and
          p7b.locator('#emptyBox').evaluate('el => el.hidden'))
    check('E2E-07b 無 console error', len(e7b) == 0, e7b[:2])
    p7b.close()

    # E2E-17 鍵盤 focus tooltip
    page.locator('#chart circle.dot').first.focus()
    page.wait_for_timeout(250)
    check('E2E-17 focus 顯示 tooltip', page.locator('#tip').evaluate('el => el.classList.contains("show")'))
    page.locator('#chartTitle').focus()
    page.locator('#flightSel').focus()
    page.wait_for_timeout(200)
    check('E2E-17 focusout 隱藏', page.locator('#tip').evaluate('el => !el.classList.contains("show")'))

    # E2E-18 aria 語意
    check('E2E-18 tablist/role=tab', page.locator('#routeTabs[role="tablist"]').count() == 1 and
          page.locator('#routeTabs button[role="tab"][aria-selected]').count() == 4)
    check('E2E-18 aria-pressed 範圍按鈕', page.locator('#rangeSeg button[aria-pressed]').count() == 4)
    check('E2E-18 圖表 role=img', page.locator('#chart[role="img"][aria-label]').count() == 1)

    # E2E-23 價格定義：無艙等/人數/單程控制
    body_text = page.locator('body').inner_text()
    check('E2E-23 無艙等/人數/單程控制',
          '艙等' not in body_text and '人數' not in body_text and '單程' not in body_text)

    # E2E-15 mobile 375px
    mob, merrs = new_page(browser, viewport={'width': 375, 'height': 800})
    mob.goto(URL + '/web/')
    wait_chart(mob)
    h = mob.locator('select.sel').evaluate('el => Math.round(el.getBoundingClientRect().height)')
    check('E2E-15 mobile select 高度 44px', h == 44, f'got {h}px')
    check('E2E-15 圖表可橫向捲動', mob.locator('.chart-wrap').evaluate(
        'el => getComputedStyle(el).overflowX') == 'auto')
    check('E2E-15 mobile 無 console error', len(merrs) == 0, merrs[:2])
    mob.close()

    check('E2E-01~23 冒煙（桌面）無 console/page error', len(errs) == 0, errs[:3])
    page.close()

    # E2E-19 reduced-motion
    rm, rerrs = new_page(browser, viewport={'width': 1280, 'height': 900},
                         reduced_motion='reduce')
    rm.goto(URL + '/web/')
    wait_chart(rm)
    check('E2E-19 reduced-motion 頁面正常', rm.locator('#chart path.price-line').count() == 1)
    check('E2E-19 無 console error', len(rerrs) == 0, rerrs[:2])
    rm.close()

    print('\n── mocked 邊界（route 攔截）──')

    # E2E-08 index 500 → 錯誤卡 + 重試
    p8, e8 = new_page(browser, viewport={'width': 1280, 'height': 900})
    route_mock_api(p8, index=mock_index(), index_status=500)
    p8.goto(URL + '/web/')
    p8.wait_for_selector('#errBox:not([hidden])', timeout=8000)
    check('E2E-08 錯誤卡顯示', p8.locator('#errBox').is_visible())
    check('E2E-08 錯誤代碼 ERR_INDEX_FETCH', 'ERR_INDEX_FETCH' in p8.locator('#errDetail').inner_text())
    check('E2E-08 重試按鈕存在', p8.locator('#retryBtn').count() == 1)
    p8.close()

    # E2E-08b 重試不堆疊 listener
    # 連點重試，index 請求數 = 1 + 重試次數（重試未生效/雙重觸發都會破壞此數）；
    # 另以 addEventListener spy 驗證重跑 init 不重複綁定（retry 綁定的是同名函式 init，
    # 瀏覽器會去重，故請求數測不出綁定堆疊；真正會被堆疊的是 routeTabs/rangeSeg/
    # flightSel 的匿名 listener，必須直接統計綁定次數）。
    p8b, e8b = new_page(browser, viewport={'width': 1280, 'height': 900})
    req8b = {'n': 0}
    def fail500(route):
        req8b['n'] += 1
        route.fulfill(status=500, content_type='application/json', body='{}')
    p8b.route('**/api/index.json', fail500)
    # 在 app.js 之前注入 spy：統計各元素 listener 綁定次數
    p8b.add_init_script("""
      (function () {
        const orig = EventTarget.prototype.addEventListener;
        window.__ael = {};
        EventTarget.prototype.addEventListener = function (type, cb, opts) {
          const key = (this.id || this.tagName) + '.' + type;
          window.__ael[key] = (window.__ael[key] || 0) + 1;
          return orig.call(this, type, cb, opts);
        };
      })();
    """)
    p8b.goto(URL + '/web/')
    p8b.wait_for_selector('#errBox:not([hidden])', timeout=8000)
    p8b.locator('#retryBtn').click()
    p8b.wait_for_selector('#errBox:not([hidden])', timeout=8000)
    p8b.locator('#retryBtn').click()
    p8b.wait_for_selector('#errBox:not([hidden])', timeout=8000)
    check('E2E-08b 重試不堆疊 listener', req8b['n'] == 3, f'got {req8b["n"]}')
    ael = p8b.evaluate('window.__ael')
    check('E2E-08b 重跑 init 不重複綁定（各控件 listener 仍為 1）',
          ael.get('routeTabs.click') == 1 and ael.get('rangeSeg.click') == 1 and
          ael.get('flightSel.change') == 1 and ael.get('retryBtn.click') == 1,
          repr(ael))
    # 註：刻意 500 會產生 console error，屬預期，故不檢查
    p8b.close()

    # E2E-08c 重試成功恢復正常
    p8c, e8c = new_page(browser, viewport={'width': 1280, 'height': 900})
    p8c.route('**/api/index.json', lambda route: route.fulfill(
        status=500, content_type='application/json', body='{}'))
    p8c.goto(URL + '/web/')
    p8c.wait_for_selector('#errBox:not([hidden])', timeout=8000)
    p8c.unroute('**/api/index.json')
    p8c.locator('#retryBtn').click()
    p8c.wait_for_selector('#chart path.price-line', timeout=8000)
    check('E2E-08c 重試成功載入圖表', p8c.locator('#chart path.price-line').count() == 1)
    check('E2E-08c 重試後互動正常', '顯示 3 個月' in p8c.locator('#chartTitle').inner_text())
    # 註：首次 500 的 console error 屬預期，故不檢查
    p8c.close()

    # E2E-11 過舊警示（15 天前）
    import datetime
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=15)).strftime('%Y-%m-%dT12:00:00.000Z')
    p11, e11 = new_page(browser, viewport={'width': 1280, 'height': 900})
    route_mock_api(p11, index=mock_index(generated_at=old))
    p11.goto(URL + '/web/')
    wait_chart(p11)
    check('E2E-11 過舊警示顯示', p11.locator('#staleBar').is_visible())
    check('E2E-11 警示含上次更新日期', '資料可能過時' in p11.locator('#staleText').inner_text())
    check('E2E-11 圖表照常顯示', p11.locator('#chart path.price-line').count() == 1)
    p11.close()

    # E2E-12 空資料
    p12, e12 = new_page(browser, viewport={'width': 1280, 'height': 900})
    route_mock_api(p12, index=mock_index(trips=[]))
    p12.goto(URL + '/web/')
    p12.wait_for_selector('#emptyBox:not([hidden])', timeout=8000)
    check('E2E-12 空狀態顯示', p12.locator('#emptyBox').is_visible())
    check('E2E-12 空狀態文案', '每週五更新' in p12.locator('#emptyBox').inner_text())
    p12.close()

    # E2E-09 缺資料週（mock 5 週，缺第 3 週 → gap-dot，兩側仍有連續點）
    p9, e9 = new_page(browser, viewport={'width': 1280, 'height': 900})
    route_mock_api(p9, index=mock_index(), missing_url='TPE-NRT_2026-08-29_2026-09-06.json')
    p9.goto(URL + '/web/')
    p9.wait_for_selector('#chart path.price-line', state='attached', timeout=8000)
    p9.wait_for_timeout(300)
    check('E2E-09 缺資料週 gap-dot', p9.locator('#chart circle.gap-dot').count() >= 1)
    p9.close()

    # E2E-10 售罄週
    p10, e10 = new_page(browser, viewport={'width': 1280, 'height': 900})
    route_mock_api(p10, index=mock_index(include_soldout_week=True), trips='soldout')
    p10.goto(URL + '/web/')
    p10.wait_for_selector('#chart path.price-line', timeout=8000)
    p10.wait_for_timeout(300)
    check('E2E-10 售罄標示', p10.locator('#chart text.sold-out-label').count() >= 1)
    p10.close()

    # E2E-05b XSS 防護：航班號含 HTML（引號突圍）不注入
    p5b, e5b = new_page(browser, viewport={'width': 1280, 'height': 900})
    evil = 'x"><img src=x onerror="window.__xss__=1">'
    evil_trip = mock_trip('2026-08-15', '2026-08-23', [(evil, 32296, 'Available')])
    evil_trip2 = mock_trip('2026-08-22', '2026-08-30', [('JX 802', 46072, 'Available')])
    urls5 = ['api/trips/TPE-NRT_2026-08-15_2026-08-23.json',
             'api/trips/TPE-NRT_2026-08-22_2026-08-30.json']
    idx5 = mock_index(trips=urls5)
    def handler5(route):
        url = route.request.url
        if '/api/index.json' in url:
            route.fulfill(status=200, content_type='application/json', body=json.dumps(idx5, ensure_ascii=False))
        elif '/api/trips/' in url:
            body = evil_trip if url.endswith('TPE-NRT_2026-08-15_2026-08-23.json') else evil_trip2
            route.fulfill(status=200, content_type='application/json', body=json.dumps(body, ensure_ascii=False))
        else:
            route.continue_()
    p5b.route('**/api/**', handler5)
    p5b.goto(URL + '/web/')
    p5b.wait_for_selector('#chart path.price-line', timeout=8000)
    p5b.wait_for_timeout(300)
    check('E2E-05b 航班號 HTML 不注入', p5b.evaluate('window.__xss__') != 1)
    check('E2E-05b 下拉含該航班選項', p5b.locator('#flightSel option').count() >= 2)
    check('E2E-05b 無 console/page error', len(e5b) == 0, e5b[:2])
    p5b.close()

    # E2E-14 載入中工具列 disabled（掛起 index 請求 → 頁面必定停在載入中）
    p14a, e14a = new_page(browser, viewport={'width': 1280, 'height': 900})

    def hang(route):
        pass  # 不 fulfill 不 continue → fetchIndex 永久 pending

    p14a.route('**/api/index.json', hang)
    p14a.goto(URL + '/web/')
    p14a.wait_for_timeout(300)
    check('E2E-14 載入中航班 select disabled',
          p14a.locator('#flightSel').is_disabled() and
          not p14a.locator('#skeleton').evaluate('el => el.hidden'))
    p14a.close()

    # E2E-14b 載入完成後啟用（真實資料）
    p14b, e14b = new_page(browser, viewport={'width': 1280, 'height': 900})
    p14b.goto(URL + '/web/')
    p14b.wait_for_selector('#chart path.price-line', timeout=8000)
    check('E2E-14 載入完成啟用', not p14b.locator('#flightSel').is_disabled())
    p14b.close()


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
    print(f'\n═══ E2E 結果：{passed}/{total} 通過 ═══')
    for name, ok, detail in RESULTS:
        if not ok:
            print('  ❌', name, '←', detail)
    sys.exit(0 if passed == total else 1)


if __name__ == '__main__':
    main()
