#!/usr/bin/env python3
"""PWA — E2E（Phase 1 安裝 T5 + Phase 2 訂閱/通知 T12 完整版）

對照：
  docs/development/PWA.md（§5.1 安裝狀態機、§8 步驟 5/12、§9.3 GitHub Pages 子路徑、§9.7 Spike S2/S4）
  docs/test-plans/PWA測試計畫.md（Phase 1：E2E-01~05 / E2E-36~39 / INT-06；Phase 2：INT-01~07 + E2E-06~49）
  docs/bdds/PWA.feature（P1-A~P2-F、E1~E14、@edge-case、@business-rules）

架構：本機 http.server（repo root，以 /web/ 子路徑 serve，模擬 GitHub Pages）＋ Python Playwright
      headless chromium；完全沿用 tests/e2e_smoke.py / tests/e2e_offline.py 的
      check() 累積 + 本機伺服器模式（回歸門檻檔案不動）。

模擬技術（D8「mocked 端到端」驗收；測試計畫 §6.0）：
  - beforeinstallprompt：add_init_script 攔截 window.addEventListener('beforeinstallprompt')
    → 暴露 window.__fireBIP(outcome)（stub prompt() → userChoice；outcome 控制 accept/dismiss）
  - display-mode standalone（P1-C）：add_init_script 覆寫 matchMedia，僅攔截 display-mode 查詢
  - iOS UA（P1-B / P2-D / EC6）：browser.new_context(user_agent=iPhone UA)；16.3 / 17.5 兩版
  - mock push service（Phase 2）：page.route 攔截 /vapid-public-key（測試公鑰，可設 500 測 E3）、
    /subscribe（記錄 body）、/notify（驗證 Bearer token）——pwa.js CONFIG.PUSH_WORKER_URL 以
    init script 於 Pwa 指派時覆寫為本機伺服器（fetchVapidPublicKey 讀取時生效）
  - PushManager / Notification stub（Phase 2）：add_init_script 注入假 pushManager.getSubscription /
    subscribe 與 Notification.permission / requestPermission（可控制狀態；訂閱以 localStorage 持久，
    跨 reload 還原三態——模擬真實 PushManager 訂閱在 reload 後保留，F-26 / INT-02）
  - push 事件（Phase 2）：本 Chromium（149）CDP 已移除 ServiceWorker.dispatchPushEvent / \
    dispatchNotificationClickEvent → 改以 SW 執行緒內建構 PushEvent（payload JSON）與 plain Event
    + 手動 notification/waitUntil 屬性觸發**真實** sw.js push / notificationclick / notificationclose
    handler；showNotification 以 SW 端 patch 記錄（通知內容斷言）；openWindow 分支以
    matchAll→[] 觸發（真實開新分頁）或記錄呼叫（deep-link URL 斷言）
  - 離線 reload（沿用 e2e_offline 技術）：cookie offline=1 + navigator.onLine 覆寫 + api route abort
  - 安裝性稽核（Spike S4 / MAN-11 替代）：CDP Page.getAppManifest + Page.getInstallabilityErrors
    （persistent context 避免 Playwright incognito 造成 in-incognito 假錯誤）

執行：python tests/e2e_pwa.py
"""
import functools
import http.server
import json
import os
import socketserver
import sys
import threading
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = None
chromium_path = '/usr/bin/chromium'

RESULTS = []

IOS_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) '
          'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 '
          'Mobile/15E148 Safari/604.1')


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


# ═══════════════ 注入腳本（add_init_script，頁面任何 script 前執行）═══════════════
# beforeinstallprompt stub：攔截 app.js 對 window 綁定的 beforeinstallprompt handler，
# 暴露 __fireBIP(outcome) 觸發之（stub prompt() 回傳 userChoice promise，計數供驗證）。
INIT_BIP = r"""
(() => {
  window.__install = { promptCalls: 0, bipFired: 0 };
  const orig = window.addEventListener.bind(window);
  let bipHandler = null;
  window.addEventListener = function (type, cb, opts) {
    if (type === 'beforeinstallprompt') { bipHandler = cb; return; }
    return orig(type, cb, opts);
  };
  window.__fireBIP = function (outcome) {
    window.__install.bipFired += 1;
    const promise = Promise.resolve({ outcome: outcome || 'accepted', platform: 'web' });
    const ev = {
      type: 'beforeinstallprompt',
      platforms: ['web'],
      preventDefault() {},          // app.js e.preventDefault()（暫存 deferred prompt，F-02）
      prompt() {
        window.__install.promptCalls += 1;   // 點擊按鈕才呼叫（BR40 / F-02 驗證）
        return promise;
      },
    };
    if (bipHandler) bipHandler(ev);
    return promise;
  };
})();
"""

# display-mode standalone stub（P1-C / E2E-38③）：僅攔截 display-mode 查詢 → matches=true；
# 其餘（如 max-width: 767px 的 mobile 排版判斷）走真實 matchMedia，避免破壞版面。
INIT_STANDALONE = r"""
(() => {
  const orig = window.matchMedia.bind(window);
  window.matchMedia = function (q) {
    if (q.indexOf('display-mode') !== -1) {
      return {
        matches: true, media: q, onchange: null,
        addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
        dispatchEvent() { return true; },
      };
    }
    return orig(q);
  };
})();
"""

# navigator.onLine 覆寫（沿用 e2e_offline INIT_ONLINE）：cookie offline=1 → 離線 reload 持久；
# __setOnline(on) 動態切換並觸發 offline/online 事件。
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


# ═══════════════ helpers ═══════════════
def new_ctx(browser, bip=False, standalone=False, ios=False, online_override=False, viewport=None,
            push=False, preset='', ios_version=None):
    """獨立 context。bip → beforeinstallprompt stub；standalone → display-mode stub；
    ios → iPhone UA（ios_version='16.3' → iOS 16.3 UA，EC6）；online_override → navigator.onLine 覆寫
    （cookie offline=1 於 reload 前再加）；push → Phase 2 push stub（Pwa.CONFIG.PUSH_WORKER_URL 指向本機
    mock；preset 為 stub 後附加 JS，如預置已訂閱/權限狀態）。"""
    kw = {'viewport': viewport or {'width': 1280, 'height': 900}}
    if ios:
        kw['user_agent'] = IOS_UA_163 if ios_version == '16.3' else IOS_UA
    ctx = browser.new_context(**kw)
    if bip:
        ctx.add_init_script(INIT_BIP)
    if standalone:
        ctx.add_init_script(INIT_STANDALONE)
    if online_override:
        ctx.add_init_script(INIT_ONLINE)
    if push:
        ctx.add_init_script(push_init_script(URL, preset))
    page = ctx.new_page()
    errs = []
    page.on('pageerror', lambda e: errs.append('PAGE: ' + str(e)))
    page.on('console', lambda m: errs.append('CONSOLE: ' + m.text) if m.type == 'error' else None)
    return ctx, page, errs


def wait_chart(page, timeout=10000):
    page.wait_for_selector('#chart path.price-line', timeout=timeout)
    page.wait_for_timeout(150)


def wait_sw_ready(page):
    """等待 SW active 且 shell cache v3（pwa.js 進 shell）precache 完成（離線 reload 前必備）。"""
    page.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.ready;
      const keys = await caches.keys();
      return reg.active && keys.includes('airtickets-shell-v3');
    }""", timeout=10000)


def is_hidden(page, sel):
    """元素具 hidden 屬性且被 [hidden]{display:none!important} 遮蔽（沿用 e2e_smoke 語意）。"""
    return page.locator(sel).evaluate(
        'el => el.hidden && getComputedStyle(el).display === "none"')


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


# ═══════════════ Phase 2：mock push service + PushManager/Notification stub（§6.0）═══════════════
IOS_UA_163 = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
              'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 '
              'Mobile/15E148 Safari/604.1')

# RFC 8291 測試公鑰（65B 未壓縮點，87 chars base64url）——mock /vapid-public-key 回傳值
TEST_VAPID_KEY = 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A'

PRESET_SUBSCRIBED = ("window.__pushSetSub({endpoint: 'https://mock-push.example.com/sub/preset', "
                     "expirationTime: null, keys: {p256dh: 'BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', "
                     "auth: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'}});")


def push_init_script(worker_url, preset=''):
    """Phase 2 push stub（add_init_script）：
    ① Notification.permission / requestPermission 可控制（requestResult：granted/denied/ignore（E4））
    ② PushManager.prototype.getSubscription / subscribe stub；訂閱以 localStorage 持久（跨 reload
       還原三態，模擬真實瀏覽器行為）；subscribeError 非 null → subscribe reject（E2）
    ③ Pwa.CONFIG.PUSH_WORKER_URL 於 Pwa 指派時覆寫為本機 worker URL（app.js initPwaPush 呼叫
       fetchVapidPublicKey 時讀取，故須在指派瞬間覆寫）
    preset：附加於 stub 後的 JS（預置已訂閱 / 權限狀態等）。"""
    return f"""
(() => {{
  const KEY = '__push_sub';
  const PK = '__push_perm';
  let saved = null, savedPerm = null;
  try {{ const raw = localStorage.getItem(KEY); if (raw) saved = JSON.parse(raw); }} catch (e) {{}}
  try {{ savedPerm = localStorage.getItem(PK); }} catch (e) {{}}
  window.__push = {{
    permission: savedPerm || 'default',
    requestResult: 'granted',
    requestCalls: 0,
    subscribeCalls: 0,
    unsubscribeCalls: 0,
    subscribeError: null,
    lastOpts: null,
    _sub: saved,
  }};
  window.__pushSetSub = function (s) {{
    window.__push._sub = s;
    try {{ if (s) localStorage.setItem(KEY, JSON.stringify(s)); else localStorage.removeItem(KEY); }} catch (e) {{}}
  }};
  window.__pushSetPerm = function (p) {{
    window.__push.permission = p;
    try {{ localStorage.setItem(PK, p); }} catch (e) {{}}
  }};
  const mkSub = () => ({{
    endpoint: 'https://mock-push.example.com/sub/abc123',
    expirationTime: null,
    keys: {{ p256dh: 'BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', auth: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' }},
    unsubscribe: async function () {{ window.__push.unsubscribeCalls += 1; window.__pushSetSub(null); return true; }},
    toJSON: function () {{ return {{ endpoint: this.endpoint, expirationTime: this.expirationTime, keys: this.keys }}; }},
  }});
  try {{
    Object.defineProperty(Notification, 'permission', {{ get: () => window.__push.permission, configurable: true }});
  }} catch (e) {{}}
  try {{
    Notification.requestPermission = function () {{
      window.__push.requestCalls += 1;
      const r = window.__push.requestResult;
      if (r === 'ignore') return Promise.resolve('default');  // E4：詢問被忽略（關閉）→ default，無錯誤；再點可重新詢問
      window.__push.permission = r;
      try {{ localStorage.setItem(PK, r); }} catch (e) {{}}
      return Promise.resolve(r);
    }};
  }} catch (e) {{}}
  try {{
    PushManager.prototype.getSubscription = function () {{
      const s = window.__push._sub;
      if (!s) return Promise.resolve(null);
      const sub = mkSub();
      Object.assign(sub, s);
      return Promise.resolve(sub);
    }};
    PushManager.prototype.subscribe = function (opts) {{
      window.__push.subscribeCalls += 1;
      window.__push.lastOpts = opts;
      if (window.__push.subscribeError) return Promise.reject(new Error(window.__push.subscribeError));  // E2
      const sub = mkSub();
      window.__pushSetSub(sub);
      return Promise.resolve(sub);
    }};
  }} catch (e) {{}}
  // Pwa 指派瞬間覆寫 CONFIG.PUSH_WORKER_URL（§6.0「mock push service」）
  let target = window.Pwa;
  Object.defineProperty(window, 'Pwa', {{
    configurable: true,
    get: () => target,
    set: (v) => {{ target = v; if (v && v.CONFIG) v.CONFIG.PUSH_WORKER_URL = '{worker_url}'; }},
  }});
  {preset}
}})();
"""


class WorkerMock:
    """mock push service（§6.0 ①）：page.route 攔截三端點。
    /vapid-public-key → 測試公鑰（vapid_status=500 測 E3）；/subscribe → 記錄 body 回 200（可設 500 測 E2）；
    /notify → 驗證 Authorization: Bearer（缺/錯 → 401），記錄 drops 回 200。"""

    def __init__(self, page):
        self.page = page
        self.vapid_status = 200
        self.subscribe_status = 200
        self.vapid_requests = 0
        self.subscribe_bodies = []
        self.notify = []          # [{auth, drops}]
        page.route('**/vapid-public-key', self._on_vapid)
        page.route('**/subscribe', self._on_subscribe)
        page.route('**/notify', self._on_notify)

    def _on_vapid(self, route):
        self.vapid_requests += 1
        if self.vapid_status == 200:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'publicKey': self.vapid_key}))
        else:
            # E3：以網路層失敗模擬 500/不可達（app 行為相同；避免 500 響應觸發 console error）
            route.abort('failed')

    @property
    def vapid_key(self):
        return TEST_VAPID_KEY

    def _on_subscribe(self, route):
        body = route.request.post_data
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {}
        self.subscribe_bodies.append(parsed)
        if self.subscribe_status == 200:
            route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
        else:
            # E2：/subscribe 失敗（500/不可達）
            route.abort('failed')

    def _on_notify(self, route):
        auth = route.request.headers.get('authorization', '') or ''
        try:
            parsed = json.loads(route.request.post_data or '{}')
        except Exception:
            parsed = {}
        self.notify.append({'auth': auth, 'drops': parsed.get('drops')})
        if not auth.startswith('Bearer '):
            route.fulfill(status=401, content_type='application/json', body='{"error":"unauthorized"}')
        else:
            route.fulfill(status=200, content_type='application/json',
                          body='{"ok":true,"sent":1,"failed":0}')


# ═══════════════ Phase 2：SW 執行緒內事件驅動（push / notificationclick / notificationclose）═══════════════
# 本 Chromium（149）CDP 已移除 ServiceWorker.dispatchPushEvent / dispatchNotificationClickEvent，
# 改以在 SW 執行緒（ctx.service_workers[i].evaluate）內建構事件觸發**真實** sw.js handler（§6.0 技術對照註記）：
#  - push：PushEvent 可建構（new PushEvent('push', { data: JSON.stringify(payload) })）→ e.data.json() 走真實解析
#  - notificationclick / close：NotificationEvent 需真實 Notification（SW 端非法建構）→ 以 plain Event +
#    手動 notification / waitUntil 屬性觸發（handler 讀 e.notification.data.url / e.waitUntil 皆真實）

def get_sw(ctx):
    """目前 SW（service_workers 即時清單；無 → None）。"""
    sws = ctx.service_workers
    return sws[0] if sws else None


def sw_patch_recorder(sw):
    """SW 端 patch showNotification → 記錄於 self.__notifs（通知顯示斷言）。"""
    sw.evaluate("""() => {
      self.__notifs = [];
      ServiceWorkerRegistration.prototype.showNotification = function (title, opts) {
        self.__notifs.push({ title: title, opts: JSON.parse(JSON.stringify(opts || {})) });
        return Promise.resolve();
      };
    }""")


def sw_dispatch_push(sw, payload):
    """觸發真實 push handler（payload = 通知承載 dict；與 Worker formatNotification 產出同構）。"""
    sw.evaluate("""(s) => { self.dispatchEvent(new PushEvent('push', { data: s })); }""",
                json.dumps(payload, ensure_ascii=False))


def sw_dispatch_click(sw, data_url):
    """觸發真實 notificationclick handler（data.url 相對 SW scope；既有分頁 → focus+navigate）。"""
    sw.evaluate("""(u) => {
      const ev = new Event('notificationclick');
      ev.notification = { close() {}, data: { url: u } };
      ev.waitUntil = (pr) => { self.__waitUntil = pr; };
      self.dispatchEvent(ev);
    }""", data_url)


def sw_dispatch_click_open_record(sw, data_url):
    """openWindow 分支之 URL 斷言：patch matchAll→[] 且 openWindow 記錄呼叫（不真正開窗）。"""
    sw.evaluate("""(u) => {
      self.__openCalls = [];
      self.clients.matchAll = () => Promise.resolve([]);
      self.clients.openWindow = (url) => { self.__openCalls.push(String(url)); return Promise.resolve(null); };
      const ev = new Event('notificationclick');
      ev.notification = { close() {}, data: { url: u } };
      ev.waitUntil = (pr) => { self.__waitUntil = pr; };
      self.dispatchEvent(ev);
    }""", data_url)


def sw_dispatch_close(sw):
    """觸發真實 notificationclose handler（E13：無任何動作）。"""
    sw.evaluate("""() => {
      const ev = new Event('notificationclose');
      ev.notification = { close() {} };
      self.dispatchEvent(ev);
    }""")


def route_abort_all_api(page):
    """離線：api/** 一律 abort（沿用 e2e_offline 技術，資料層離線）。"""
    page.route('**/api/**', lambda route: route.abort('failed'))


def errs_without_net(errs):
    """過濾「mock 故意失敗」的瀏覽器自動網路 log（'Failed to load resource: …'）——
    E3 / E2 情境以 mock 500/abort 模擬失敗，該 console 訊息為瀏覽器內建記錄、非 JS 錯誤。"""
    return [e for e in errs if 'Failed to load resource' not in e]


def set_online(page, on):
    page.evaluate('window.__setOnline(%s)' % ('true' if on else 'false'))


def wait_sub_ready(page, timeout=10000):
    """訂閱區 render 完成（initPwaPush 抓公鑰 + 還原狀態後 subBtn 顯示）。"""
    page.wait_for_selector('#subBtn:not([hidden])', timeout=timeout)


def wait_sub_state(page, text, timeout=6000):
    page.wait_for_function("(t) => document.getElementById('subBtn').textContent === t",
                           arg=text, timeout=timeout)


def wait_sub_status(page, text, timeout=6000):
    page.wait_for_function("(t) => document.getElementById('subStatus').textContent.includes(t)",
                           arg=text, timeout=timeout)


# ═══════════════ Phase 2 測試（T12；INT-01~07 + E2E-06~49）═══════════════
def run_phase2_tests(browser):
    """Phase 2：訂閱/退訂/通知/離線並存/錯誤處理/商業規則 E2E（mock push service + SW 事件驅動）。"""
    drops_nrt = {'route': 'TPE-NRT', 'outbound_date': '2026-08-22', 'return_date': '2026-08-30',
                 'flight_no': 'JX 804', 'old_price': 26008, 'new_price': 24120}
    drops_kix = {'route': 'TPE-KIX', 'outbound_date': '2026-08-23', 'return_date': '2026-08-31',
                 'flight_no': 'JX 820', 'old_price': 12900, 'new_price': 11500}
    PAYLOAD_NRT = {'title': '✈️ 票價下降了！',
                   'body': 'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）',
                   'data': {'url': '?route=TPE-NRT'}}
    PAYLOAD_KIX = {'title': '✈️ 票價下降了！',
                   'body': 'TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）',
                   'data': {'url': '?route=TPE-KIX'}}

    # ═══ 訂閱入口四態（P2-A Outline ×4 / E2E-06a~d / F-05 對應）═══
    print('\n── P2-A 訂閱入口四態（E2E-06a~d）──')
    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-06a default＋無訂閱 →「開啟票價提醒」',
          page.locator('#subBtn').inner_text() == '開啟票價提醒')
    check('E2E-06a 頁面載入不自動彈權限詢問（requestCalls=0）',
          page.evaluate('window.__push.requestCalls') == 0)
    check('E2E-06a 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, preset="window.__pushSetPerm('granted');")
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-06b granted＋無訂閱 →「開啟票價提醒」',
          page.locator('#subBtn').inner_text() == '開啟票價提醒')
    check('E2E-06b 載入不自動詢問', page.evaluate('window.__push.requestCalls') == 0)
    check('E2E-06b 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True,
                              preset="window.__pushSetPerm('granted'); " + PRESET_SUBSCRIBED)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-06c granted＋已訂閱 →「關閉票價提醒」＋「已訂閱」',
          page.locator('#subBtn').inner_text() == '關閉票價提醒' and
          '已訂閱' in page.locator('#subStatus').inner_text())
    check('E2E-06c 載入不自動詢問', page.evaluate('window.__push.requestCalls') == 0)
    check('E2E-06c 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, preset="window.__pushSetPerm('denied');")
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-06d denied → 拒絕引導（E1）',
          '通知已封鎖' in page.locator('#subStatus').inner_text())
    check('E2E-06d 載入不重複詢問', page.evaluate('window.__push.requestCalls') == 0)
    check('E2E-06d 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 訂閱流程（E2E-07/08/41/42 / INT-05）═══
    print('\n── P2-A 訂閱流程（E2E-07/08/41/42 / INT-05）──')
    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-07 載入時 0 次 requestPermission（F-06 user gesture 守衛）',
          page.evaluate('window.__push.requestCalls') == 0)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    check('E2E-07 點擊（user gesture）才觸發權限詢問',
          page.evaluate('window.__push.requestCalls') == 1)
    opts = page.evaluate('window.__push.lastOpts') or {}
    # applicationServerKey 自 T9+ 為 Uint8Array（Safari 相容；b64urlToBytes 解碼），比對解碼後值
    app_key = opts.get('applicationServerKey')
    ak_ok = False
    if app_key == TEST_VAPID_KEY:
        ak_ok = True
    elif isinstance(app_key, list):
        try:
            b = bytes(app_key)
            import base64
            ak_ok = base64.urlsafe_b64encode(b).rstrip(b'=').decode() == TEST_VAPID_KEY
        except Exception:
            ak_ok = False
    check('INT-05/E2E-08 subscribe 以 userVisibleOnly + 測試公鑰（Uint8Array 解碼比對）',
          opts.get('userVisibleOnly') is True and ak_ok,
          repr(opts))
    check('E2E-08 訂閱成功狀態「已訂閱」',
          '已訂閱' in page.locator('#subStatus').inner_text())
    bodies = w.subscribe_bodies
    check('E2E-42/INT-05 POST /subscribe 收到有效訂閱 body（endpoint+keys+action=add）',
          len(bodies) == 1 and bodies[0].get('action') == 'add' and
          bool(bodies[0].get('endpoint')) and
          bool(bodies[0].get('keys', {}).get('p256dh')) and
          bool(bodies[0].get('keys', {}).get('auth')), repr(bodies))
    check('E2E-41 前端 GET /vapid-public-key 取得公鑰（mock 被請求）',
          w.vapid_requests >= 1, f'vapid_requests={w.vapid_requests}')
    check('E2E-08 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 退訂（P2-C / E2E-11）═══
    print('\n── P2-C 退訂（E2E-11）──')
    ctx, page, errs = new_ctx(browser, push=True,
                              preset="window.__pushSetPerm('granted'); " + PRESET_SUBSCRIBED)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '開啟票價提醒')
    check('E2E-11 本機 PushSubscription 移除（unsubscribe 被呼叫）',
          page.evaluate('window.__push.unsubscribeCalls') == 1)
    bodies = w.subscribe_bodies
    check('E2E-11 POST /subscribe {endpoint, action:remove}（Worker KV 刪除）',
          len(bodies) == 1 and bodies[0].get('action') == 'remove' and
          bodies[0].get('endpoint') == 'https://mock-push.example.com/sub/preset', repr(bodies))
    check('E2E-11 狀態回「未訂閱」（subStatus 隱藏）',
          page.locator('#subStatus').evaluate('el => el.hidden'))
    check('E2E-11 之後 getSubscription 為空（不再收到通知）',
          page.evaluate('window.__push._sub') is None)
    check('E2E-11 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ iOS 分支（P2-D / E8 / EC6）═══
    print('\n── iOS：E8 / P2-D / EC6（E2E-22/12/34）──')
    ctx, page, errs = new_ctx(browser, push=True, ios=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_status(page, '需加到主畫面')
    check('E2E-22 iOS 未安裝 → 提示「需加到主畫面後才收得到通知」（E8）',
          '需加到主畫面後才收得到通知' in page.locator('#subStatus').inner_text())
    check('E2E-22 不發權限請求（requestCalls=0）',
          page.evaluate('window.__push.requestCalls') == 0)
    check('E2E-22 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, ios=True, standalone=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    check('E2E-12 iOS 16.4+ 已加到主畫面 → 可正常訂閱（P2-D）',
          page.locator('#subBtn').inner_text() == '關閉票價提醒' and
          '已訂閱' in page.locator('#subStatus').inner_text())
    check('E2E-12 執行權限詢問（與一般流程相同）',
          page.evaluate('window.__push.requestCalls') == 1)
    check('E2E-12 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, ios=True, standalone=True, ios_version='16.3')
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_status(page, 'iOS 16.4+')
    check('E2E-34 iOS 16.3 standalone → 限制提示「需加到主畫面且 iOS 16.4+」（EC6）',
          '需加到主畫面且 iOS 16.4+' in page.locator('#subStatus').inner_text())
    check('E2E-34 不發權限請求', page.evaluate('window.__push.requestCalls') == 0)
    check('E2E-34 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 錯誤處理（E1~E4 / E2E-15~18）═══
    print('\n── 錯誤處理：E1~E4（E2E-15/16/17/18）──')
    ctx, page, errs = new_ctx(browser, push=True, preset="window.__pushSetPerm('denied');")
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-15 權限封鎖顯示拒絕引導（E1）',
          '通知已封鎖' in page.locator('#subStatus').inner_text())
    page.locator('#subBtn').click()
    page.wait_for_timeout(400)
    check('E2E-15 不重複彈權限詢問（requestCalls=0）',
          page.evaluate('window.__push.requestCalls') == 0)
    check('E2E-15 仍顯示拒絕引導',
          '通知已封鎖' in page.locator('#subStatus').inner_text())
    # 使用者到瀏覽器設定允許 → 回頁面重按 → 重跑訂閱流程（F-11b）
    page.evaluate("window.__pushSetPerm('granted')")
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    check('E2E-15 允許後重新點 → 訂閱成功（可恢復）',
          '已訂閱' in page.locator('#subStatus').inner_text())
    check('E2E-15 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    page.evaluate("window.__push.subscribeError = 'boom'")
    page.locator('#subBtn').click()
    wait_sub_status(page, '訂閱失敗')
    check('E2E-16 subscribe 拋錯 → 「訂閱失敗，請稍後重試」（E2）',
          '訂閱失敗，請稍後重試' in page.locator('#subStatus').inner_text())
    check('E2E-16 按鈕可再點重試（未鎖定）', not page.locator('#subBtn').is_disabled())
    check('E2E-16 圖表瀏覽不受影響', page.locator('#chart path.price-line').count() == 1)
    page.evaluate("window.__push.subscribeError = null")
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    check('E2E-16 重試後訂閱成功',
          '已訂閱' in page.locator('#subStatus').inner_text())
    check('E2E-16 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    w.subscribe_status = 500
    page.locator('#subBtn').click()
    wait_sub_status(page, '訂閱失敗')
    check('E2E-16b POST /subscribe 500 → 「訂閱失敗」+ 具體原因（E2）',
          '訂閱失敗' in page.locator('#subStatus').inner_text())
    # 恢復後：本機訂閱已存在 → 再點走「關閉」（app 語意）→ 三點重建訂閱成功
    w.subscribe_status = 200
    page.locator('#subBtn').click()
    wait_sub_state(page, '開啟票價提醒')
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    check('E2E-16b 恢復後可重新建立訂閱',
          '已訂閱' in page.locator('#subStatus').inner_text())
    check('E2E-16b 無 console/page error（mock 500 網路 log 除外）',
          len(errs_without_net(errs)) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    w.vapid_status = 500
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-17 VAPID 公鑰抓取失敗 → 按鈕停用（E3）',
          page.locator('#subBtn').is_disabled())
    check('E2E-17 提示「提醒功能暫時不可用」',
          '提醒功能暫時不可用' in page.locator('#subStatus').inner_text())
    check('E2E-17 圖表/航線正常（不受影響）',
          page.locator('#chart path.price-line').count() == 1)
    w.vapid_status = 200
    page.reload()
    wait_chart(page)
    wait_sub_ready(page)
    check('E2E-17 下次載入按鈕自動恢復可用',
          not page.locator('#subBtn').is_disabled() and
          page.locator('#subBtn').inner_text() == '開啟票價提醒')
    check('E2E-17 無 console/page error（mock 500 網路 log 除外）',
          len(errs_without_net(errs)) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    page.evaluate("window.__push.requestResult = 'ignore'")
    page.locator('#subBtn').click()
    page.wait_for_timeout(500)
    check('E2E-18 權限詢問被忽略 → 維持未訂閱、無錯誤提示（E4）',
          page.locator('#subBtn').inner_text() == '開啟票價提醒' and
          page.locator('#subStatus').evaluate('el => el.hidden'))
    check('E2E-18 requestPermission 被呼叫（詢問框出現）',
          page.evaluate('window.__push.requestCalls') == 1)
    page.evaluate("window.__push.requestResult = 'granted'")
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    check('E2E-18 再點重新彈詢問 → 訂閱成功',
          page.evaluate('window.__push.requestCalls') == 2)
    check('E2E-18 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 通知顯示（P2-B / E2E-09 / E2E-44a/b / E2E-25）═══
    print('\n── P2-B 通知顯示（E2E-09/44a/44b/25）──')
    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    sw = get_sw(ctx)
    sw_patch_recorder(sw)
    merged = page.evaluate("(args) => window.Pwa.formatNotification(args)", [drops_nrt, drops_kix])
    sw_dispatch_push(sw, merged)
    page.wait_for_timeout(400)
    notifs = sw.evaluate('self.__notifs')
    check('E2E-09 多航班下降合併為單則通知（1 則非 2 則）', len(notifs) == 1, f'n={len(notifs)}')
    check('E2E-09 title「✈️ 票價下降了！」', notifs[0]['title'] == '✈️ 票價下降了！')
    body = notifs[0]['opts']['body']
    check('E2E-09 body 列出兩筆下降航班（\n 分隔）',
          body.count('\n') == 1 and '東京' in body and '大阪' in body, repr(body))
    check('E2E-09 data.url = ?route=<第一筆>',
          notifs[0]['opts']['data']['url'] == '?route=TPE-NRT')
    sw_patch_recorder(sw)
    sw_dispatch_push(sw, PAYLOAD_NRT)
    page.wait_for_timeout(300)
    n1 = sw.evaluate('self.__notifs')[-1]
    check('E2E-44a body 精確符合承載格式（Outline row 1）',
          n1['opts']['body'] == 'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）', repr(n1['opts']['body']))
    check('E2E-44a data.url = ?route=TPE-NRT',
          n1['opts']['data']['url'] == '?route=TPE-NRT')
    sw_dispatch_push(sw, PAYLOAD_KIX)
    page.wait_for_timeout(300)
    n2 = sw.evaluate('self.__notifs')[-1]
    check('E2E-44b body 精確符合承載格式（Outline row 2）',
          n2['opts']['body'] == 'TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）', repr(n2['opts']['body']))
    check('E2E-44b data.url = ?route=TPE-KIX',
          n2['opts']['data']['url'] == '?route=TPE-KIX')
    check('E2E-44 通知 icon/badge 為 icons/icon-192.png',
          n1['opts'].get('icon') == 'icons/icon-192.png' and
          n1['opts'].get('badge') == 'icons/icon-192.png')
    drops5 = [
        {'route': 'TPE-NRT', 'outbound_date': '2026-08-22', 'return_date': '2026-08-30', 'flight_no': 'JX 804', 'old_price': 26008, 'new_price': 24120},
        {'route': 'TPE-KIX', 'outbound_date': '2026-08-23', 'return_date': '2026-08-31', 'flight_no': 'JX 820', 'old_price': 12900, 'new_price': 11500},
        {'route': 'TPE-FUK', 'outbound_date': '2026-08-24', 'return_date': '2026-09-01', 'flight_no': 'JX 840', 'old_price': 9800, 'new_price': 9200},
        {'route': 'TPE-CTS', 'outbound_date': '2026-08-25', 'return_date': '2026-09-02', 'flight_no': 'JX 850', 'old_price': 18000, 'new_price': 17500},
        {'route': 'TPE-NRT', 'outbound_date': '2026-09-05', 'return_date': '2026-09-13', 'flight_no': 'JX 802', 'old_price': 31000, 'new_price': 30800},
    ]
    payload5 = page.evaluate("(ds) => window.Pwa.formatNotification(ds)", drops5)
    check('E2E-25 5 筆只保留下降幅度最大 3 筆（body 3 行，E11）',
          payload5['body'].count('\n') == 2 and payload5['body'].split('\n')[0].startswith('TPE-NRT'),
          repr(payload5['body']))
    sw_patch_recorder(sw)
    sw_dispatch_push(sw, payload5)
    page.wait_for_timeout(300)
    n3 = sw.evaluate('self.__notifs')[-1]
    check('E2E-25 通知 body 3 行（其餘 2 條不發送）', n3['opts']['body'].count('\n') == 2)
    check('通知顯示 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ notificationclick：openWindow / focus+navigate / close（E2E-10/24/31/27/35）═══
    print('\n── notificationclick：deep-link（E2E-10/31/24/35/27 / INT-03/04/07）──')
    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    sw = get_sw(ctx)
    sw_dispatch_click_open_record(sw, '?route=TPE-NRT')
    page.wait_for_timeout(500)
    opened = sw.evaluate('self.__openCalls')
    check('E2E-10 點通知 → openWindow 以 scope 拼接載入 /web/?route=TPE-NRT（P2-B）',
          len(opened) == 1 and opened[0] == URL + '/web/?route=TPE-NRT', repr(opened))
    check('E2E-31/INT-04 deep-link 以 SW scope 為基準拼接（子路徑 EC3）',
          len(opened) == 1 and opened[0] == URL + '/web/?route=TPE-NRT')
    # 實際「開新分頁顯示航線」由 E2E-24（既有分頁 navigate 真實切換）＋ E2E-48 全流程覆蓋；
    # headless 下 clients.openWindow 需真實 user gesture（瀏覽器限制），故以 handler 呼叫記錄斷言（§6.0）
    check('E2E-10 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    page.locator('#routeTabs button[data-route="TPE-KIX"]').click()
    page.wait_for_selector('#chartTitle:has-text("大阪")', timeout=10000)
    npages = len(ctx.pages)
    sw = get_sw(ctx)
    sw_dispatch_click(sw, '?route=TPE-NRT')
    page.wait_for_selector('#chartTitle:has-text("東京")', timeout=10000)
    check('E2E-24/INT-03 既有分頁聚焦並切換到東京（E10，不重開分頁）',
          '東京' in page.locator('#chartTitle').inner_text())
    check('E2E-24 URL 帶 ?route=TPE-NRT', 'route=TPE-NRT' in page.url, page.url)
    check('E2E-24 分頁總數不增加', len(ctx.pages) == npages, f'{len(ctx.pages)} vs {npages}')
    check('E2E-24 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    sw = get_sw(ctx)
    sw_patch_recorder(sw)
    sw_dispatch_push(sw, PAYLOAD_NRT)
    page.wait_for_timeout(300)
    sw_dispatch_push(sw, PAYLOAD_KIX)
    page.wait_for_timeout(300)
    check('E2E-35/INT-07 通知中心同時 2 則（各自獨立）',
          len(sw.evaluate('self.__notifs')) == 2)
    npages = len(ctx.pages)
    sw_dispatch_click(sw, '?route=TPE-NRT')
    page.wait_for_selector('#chartTitle:has-text("東京")', timeout=10000)
    check('E2E-35/INT-07 點擊一則只開啟該則航線（東京）',
          'route=TPE-NRT' in page.url and 'route=TPE-KIX' not in page.url, page.url)
    check('E2E-35 其他通知無連動（分頁數不變）', len(ctx.pages) == npages)
    check('E2E-35 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    sw = get_sw(ctx)
    url_before = page.url
    npages = len(ctx.pages)
    sw_dispatch_close(sw)
    page.wait_for_timeout(600)
    check('E2E-27 滑掉通知無任何後續動作（E13）',
          page.url == url_before and len(ctx.pages) == npages)
    check('E2E-27 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 離線並存（P2-E / INT-01/02 / E2E-13/23/33）═══
    print('\n── 離線並存（INT-01/02 / E2E-13/23/33）──')
    ctx, page, errs = new_ctx(browser, push=True, online_override=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    set_online(page, False)
    ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    route_abort_all_api(page)
    resp = page.reload()
    wait_chart(page)
    wait_sub_ready(page)
    check('INT-02/E2E-33 離線 reload 快取繪圖 + 離線橫幅',
          resp is not None and resp.from_service_worker and
          page.locator('#chart path.price-line').count() == 1 and
          page.locator('#offBar').is_visible())
    check('INT-02/E2E-33 訂閱狀態離線不失效（仍「已訂閱」）',
          page.locator('#subBtn').inner_text() == '關閉票價提醒' and
          '已訂閱' in page.locator('#subStatus').inner_text())
    # INT-01：離線 push → 點通知 → 快取繪圖 + 橫幅 + 訂閱維持
    sw = get_sw(ctx)
    sw_patch_recorder(sw)
    sw_dispatch_push(sw, PAYLOAD_NRT)
    page.wait_for_timeout(300)
    check('INT-01 離線收到 push 通知', len(sw.evaluate('self.__notifs')) == 1)
    sw_dispatch_click(sw, '?route=TPE-NRT')
    page.wait_for_selector('#chartTitle:has-text("東京")', timeout=10000)
    check('INT-01/E2E-13 離線點通知 → 以快取資料顯示東京趨勢圖',
          '東京' in page.locator('#chartTitle').inner_text() and
          page.locator('#chart path.price-line').count() == 1)
    check('INT-01 頁首離線橫幅 + 訂閱狀態維持「已訂閱」',
          page.locator('#offBar').is_visible() and
          page.locator('#subBtn').inner_text() == '關閉票價提醒')
    check('INT-01 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, online_override=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    set_online(page, False)
    ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    route_abort_all_api(page)
    page.reload()
    wait_chart(page)
    wait_sub_ready(page)
    sw = get_sw(ctx)
    sw_dispatch_click(sw, '?route=TPE-KIX')
    page.wait_for_selector('#routeTabs button[data-route="TPE-KIX"] .hint', timeout=8000)
    check('E2E-23 離線點通知目標未快取 →「此航線尚未下載，需連網」（E9）',
          '此航線尚未下載，需連網' in
          page.locator('#routeTabs button[data-route="TPE-KIX"] .hint').inner_text())
    check('E2E-23 停留原航線（東京），不白屏',
          '東京' in page.locator('#chartTitle').inner_text() and
          page.locator('#chart path.price-line').count() == 1)
    check('E2E-23 不跳出錯誤卡/空狀態',
          page.locator('#errBox').evaluate('el => el.hidden') and
          page.locator('#emptyBox').evaluate('el => el.hidden'))
    check('E2E-23 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # 離線 reload getSubscription 還原三態（F-26 / INT-02 語意）
    ctx, page, errs = new_ctx(browser, push=True, online_override=True,
                              preset="window.__pushSetPerm('granted'); " + PRESET_SUBSCRIBED)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    set_online(page, False)
    ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    route_abort_all_api(page)
    page.reload()
    wait_chart(page)
    wait_sub_ready(page)
    check('離線還原三態① 已訂閱 →「關閉票價提醒」＋「已訂閱」',
          page.locator('#subBtn').inner_text() == '關閉票價提醒' and
          '已訂閱' in page.locator('#subStatus').inner_text())
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, online_override=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    set_online(page, False)
    ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    route_abort_all_api(page)
    page.reload()
    wait_chart(page)
    wait_sub_ready(page)
    check('離線還原三態② 未訂閱 →「開啟票價提醒」',
          page.locator('#subBtn').inner_text() == '開啟票價提醒')
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, online_override=True,
                              preset="window.__pushSetPerm('denied');")
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    set_online(page, False)
    ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    route_abort_all_api(page)
    page.reload()
    wait_chart(page)
    wait_sub_ready(page)
    check('離線還原三態③ denied → 拒絕引導（E1 離線仍顯示）',
          '通知已封鎖' in page.locator('#subStatus').inner_text())
    ctx.close()

    # ═══ 裝置單位 / 憑證分層 / 公開免登入（E2E-32/45/49）═══
    print('\n── 裝置單位 / 憑證分層 / 公開免登入（E2E-32/45/49）──')
    ctx_a, page_a, errs_a = new_ctx(browser, push=True)
    w_a = WorkerMock(page_a)
    page_a.goto(URL + '/web/')
    wait_chart(page_a)
    wait_sub_ready(page_a)
    page_a.locator('#subBtn').click()
    wait_sub_state(page_a, '關閉票價提醒')
    ctx_b, page_b, errs_b = new_ctx(browser, push=True)
    w_b = WorkerMock(page_b)
    page_b.goto(URL + '/web/')
    wait_chart(page_b)
    wait_sub_ready(page_b)
    check('E2E-32 瀏覽器 B（獨立 storage）顯示「未訂閱」（EC4 無跨裝置同步）',
          page_b.locator('#subBtn').inner_text() == '開啟票價提醒')
    check('E2E-32 瀏覽器 A 訂閱不受影響',
          page_a.locator('#subBtn').inner_text() == '關閉票價提醒')
    check('E2E-32 B 未發出任何 add 請求', w_b.subscribe_bodies == [], repr(w_b.subscribe_bodies))
    ctx_a.close()
    ctx_b.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    body = page.evaluate("""async () => {
      const res = await fetch(window.Pwa.CONFIG.PUSH_WORKER_URL + '/vapid-public-key');
      return await res.json();
    }""")
    check('E2E-45 /vapid-public-key 僅回傳公鑰（不含私鑰，BR11）',
          list(body.keys()) == ['publicKey'] and body['publicKey'] == TEST_VAPID_KEY, repr(body))
    secret_ok = True
    for f in ['web/pwa.js', 'web/app.js', 'web/sw.js']:
        src = (Path(ROOT) / f).read_text(encoding='utf-8')
        if any(k in src for k in ['VAPID_PRIVATE_KEY', 'privateKeyJwk', 'PUSH_API_TOKEN']):
            secret_ok = False
    check('E2E-45 前端檔案不含私鑰/secret 字串（憑證分層）', secret_ok is True)
    check('E2E-45 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True, bip=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    page.evaluate('window.__fireBIP("accepted")')
    page.wait_for_selector('#installBtn:not([hidden])', timeout=5000)
    check('E2E-49 公開免登入：訂閱流程可用（無登入牆）',
          '已訂閱' in page.locator('#subStatus').inner_text())
    check('E2E-49 公開免登入：安裝按鈕可用（無登入牆）',
          not page.locator('#installBtn').evaluate('el => el.hidden'))
    check('E2E-49 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ /notify 契約（P2-F / E2E-14 / E2E-20 / E2E-21）+ E2E-48 全流程 ═══
    print('\n── /notify 契約（E2E-14/20/21）+ E2E-48 全流程 ──')
    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    res = page.evaluate("""async () => {
      const url = window.Pwa.CONFIG.PUSH_WORKER_URL;
      const drops = [{route:'TPE-NRT', outbound_date:'2026-08-22', return_date:'2026-08-30', flight_no:'JX 804', old_price:26008, new_price:24120}];
      const r1 = await fetch(url + '/notify', { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ drops: drops }) });
      const r2 = await fetch(url + '/notify', { method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer test-token'},
        body: JSON.stringify({ drops: drops }) });
      return { noAuth: r1.status, withAuth: r2.status };
    }""")
    check('E2E-20 /notify 缺 token → 401（E6）', res['noAuth'] == 401, repr(res))
    check('E2E-14 /notify 正確 Bearer → 200（P2-F）', res['withAuth'] == 200, repr(res))
    check('E2E-14 mock 端收到 Bearer token + drops 承載',
          w.notify[-1]['auth'] == 'Bearer test-token' and len(w.notify[-1]['drops']) == 1,
          repr(w.notify[-1]))
    check('E2E-21 /notify 契約 200（空訂閱者空廣播為 HDL-06 單元驗證）',
          w.notify[-1]['drops'] is not None)
    # 使用者端收到：Worker 同簽名 formatNotification → SW push → 通知顯示（零操作）
    payload = page.evaluate("(d) => window.Pwa.formatNotification(d)",
                            [{'route': 'TPE-NRT', 'outbound_date': '2026-08-22',
                              'return_date': '2026-08-30', 'flight_no': 'JX 804',
                              'old_price': 26008, 'new_price': 24120}])
    sw = get_sw(ctx)
    sw_patch_recorder(sw)
    sw_dispatch_push(sw, payload)
    page.wait_for_timeout(400)
    n = sw.evaluate('self.__notifs')[-1]
    check('E2E-14 使用者零操作即收到通知（SW 顯示摘要）',
          n['title'] == '✈️ 票價下降了！' and 'TPE-NRT' in n['opts']['body'])
    check('E2E-14 無 console/page error（mock 401 網路 log 除外）',
          len(errs_without_net(errs)) == 0, errs[:2])
    ctx.close()

    ctx, page, errs = new_ctx(browser, push=True)
    w = WorkerMock(page)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    wait_sub_ready(page)
    page.locator('#subBtn').click()
    wait_sub_state(page, '關閉票價提醒')
    sw = get_sw(ctx)
    sw_patch_recorder(sw)
    sw_dispatch_push(sw, PAYLOAD_NRT)
    page.wait_for_timeout(300)
    sw_dispatch_click_open_record(sw, '?route=TPE-NRT')
    page.wait_for_timeout(400)
    opened = sw.evaluate('self.__openCalls')
    check('E2E-48 訂閱 → push → 點擊 deep-link 全流程（D8 mocked 端到端驗收）',
          len(opened) == 1 and opened[0] == URL + '/web/?route=TPE-NRT', repr(opened))
    check('E2E-48 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ file:// Phase 2：訂閱區降級（E14）═══
    ctx, page, errs = new_ctx(browser)
    page.goto('file://' + os.path.join(ROOT, 'web', 'index.html'))
    page.wait_for_timeout(600)
    check('E2E-28 file:// 訂閱區維持隱藏（無推播能力，E14）',
          page.locator('#subBtn').evaluate('el => el.hidden'))
    check('E2E-28 file:// 無 pageerror（降級不炸 JS）',
          len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══ 爬蟲端契約（E2E-26/29/30/40）＋ workflow（E2E-47）＋ 回歸防護（E2E-46）═══
    run_python_notify_checks()

    wf = (Path(ROOT) / '.github' / 'workflows' / 'weekly-crawl.yml').read_text(encoding='utf-8')
    check('E2E-47 workflow 於 Commit 後追加 notify step（BR13 既有步驟不動）',
          wf.find('Commit data & api') < wf.find('Detect drops & notify') and
          'fetch_prices.py --notify' in wf)
    check('E2E-47 notify step 有 secret 守衛＋Bearer env（E6）',
          "secrets.PUSH_API_TOKEN != ''" in wf and 'PUSH_API_TOKEN' in wf)
    sw_src = (Path(ROOT) / 'web' / 'sw.js').read_text(encoding='utf-8')
    check('E2E-46 sw.js 回歸防護：shell 7 檔＋push/click/close handler（BR48）',
          all(f in sw_src for f in ['index.html', 'styles.css', 'app.js', 'aggregate.js', 'cache.js', 'pwa.js', 'sw.js']) and
          "addEventListener('push'" in sw_src and
          "addEventListener('notificationclick'" in sw_src and
          "addEventListener('notificationclose'" in sw_src)


def run_python_notify_checks():
    """爬蟲端下降偵測 / 節流 / 基準 / 週頻率 E2E（E2E-26/29/30/40）——以 fetch_prices 純函式驗證
    （§2 單元 SYS-* 已細測，此處跑 BDD 關鍵契約作為端到端確認）。"""
    print('\n── 爬蟲端契約（E2E-26/29/30/40）──')
    sys.path.insert(0, ROOT)  # python tests/e2e_pwa.py 時 script 目錄為 tests/，需補 repo root
    try:
        import fetch_prices as fp
    except Exception as e:  # noqa: BLE001
        check('Python notify 模組可匯入（fetch_prices）', False, str(e))
        return
    prev = [{'route_id': 'TPE-NRT', 'outbound_date': '2026-08-22', 'return_date': '2026-08-30',
             'outbound_flight_no': 'JX 804', 'price_total': 26008}]
    check('E2E-29 持平 → 不觸發（EC1）',
          fp.detect_drops(prev, [dict(prev[0], price_total=26008)]) == [])
    check('E2E-29 上漲 → 不觸發',
          fp.detect_drops(prev, [dict(prev[0], price_total=27000)]) == [])
    check('E2E-29 僅低於平均但較上次持平 → 不觸發（drop_last 唯一條件）',
          fp.detect_drops(prev, [dict(prev[0], price_total=prev[0]['price_total'])]) == [])
    drops = fp.detect_drops(prev, [dict(prev[0], price_total=24120)])
    check('E2E-29 較上次下降 → 觸發（drop_amount=1888）',
          len(drops) == 1 and drops[0]['drop_amount'] == 1888, repr(drops))
    ok, d = fp.should_notify(None, prev)
    check('E2E-26 首次無基準 → 跳過通知僅建立基準（E12）', ok is False and d == [])
    from datetime import datetime, timezone
    now = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    check('E2E-30 同週已發送 → 不重複（EC2）',
          fp.within_weekly_window(fp._iso_week(now), now) is True)
    nxt = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
    check('E2E-30 跨週 → 恢復可發送',
          fp.within_weekly_window(fp._iso_week(now), nxt) is False)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        prev_f = d / '20260807.json'
        curr_f = d / '20260814.json'
        prev_data = [{'route_id': 'TPE-NRT', 'outbound_date': '2026-08-22', 'return_date': '2026-08-30',
                      'outbound_flight_no': 'JX 804', 'price_total': 26008,
                      'scraped_at': '2026-08-07T01:00:00.000Z'}]
        curr_data = [{'route_id': 'TPE-NRT', 'outbound_date': '2026-08-22', 'return_date': '2026-08-30',
                      'outbound_flight_no': 'JX 804', 'price_total': 24120,
                      'scraped_at': '2026-08-14T01:00:00.000Z'}]
        prev_f.write_text(json.dumps(prev_data), encoding='utf-8')
        curr_f.write_text(json.dumps(curr_data), encoding='utf-8')
        base = fp.load_baseline(d, curr_f)
        check('E2E-40 基準 = 上一週 data 原始檔（非本次 / latest.json，BR6）',
              base == prev_data, repr(base))
        dd = fp.detect_drops(base, curr_data)
        check('E2E-40 較上次下降 → 觸發（old 26008→new 24120）',
              len(dd) == 1 and dd[0]['old_price'] == 26008 and dd[0]['new_price'] == 24120, repr(dd))


# ═══════════════ 測試 ═══════════════
def run_tests(browser, pw):
    # ═══ Phase 1 靜態：manifest 可達 + head 六項（E2E-36 / E2E-37 / BR1 / BR3）═══
    print('\n── Phase 1 靜態：manifest / head（E2E-36 / E2E-37）──')
    ctx, page, errs = new_ctx(browser)
    page.goto(URL + '/web/')
    wait_chart(page)
    man = page.evaluate("""async () => {
      const res = await fetch('manifest.webmanifest');
      const j = await res.json();
      return { status: res.status,
               name: j.name, short_name: j.short_name, start_url: j.start_url,
               scope: j.scope, display: j.display, theme_color: j.theme_color,
               lang: j.lang,
               icons: (j.icons || []).map(i => i.src + '|' + i.sizes + '|' + (i.purpose || '')) };
    }""")
    check('E2E-36 manifest fetch 200', man['status'] == 200, man['status'])
    check('E2E-36 manifest 必要欄位齊全（name/short_name/start_url ./ /scope ./ /standalone/theme/lang）',
          bool(man['name'] and man['short_name']) and man['start_url'] == './' and
          man['scope'] == './' and man['display'] == 'standalone' and
          man['theme_color'] == '#1a73e8' and man['lang'] == 'zh-Hant', repr(man))
    check('E2E-36 manifest icons 含 192 / 512 / 512-maskable',
          'icons/icon-192.png|192x192|' in man['icons'] and
          'icons/icon-512.png|512x512|' in man['icons'] and
          'icons/icon-512-maskable.png|512x512|maskable' in man['icons'], repr(man['icons']))
    link = page.evaluate("""() => {
      const l = document.querySelector('link[rel="manifest"]');
      return l ? new URL(l.getAttribute('href'), location.href).href : null;
    }""")
    check('E2E-36 manifest link 解析於 /web/ 子路徑（S2）',
          link == URL + '/web/manifest.webmanifest', link)
    start_url = page.evaluate("""async () => {
      const m = await (await fetch('manifest.webmanifest')).json();
      return new URL(m.start_url, location.href).href;
    }""")
    check('E2E-36 start_url ./ 解析為 /web/（S2：安裝後開啟子路徑）',
          start_url == URL + '/web/', start_url)
    head = page.evaluate("""() => {
      const q = s => document.querySelector(s);
      return {
        manifest: !!q('link[rel="manifest"]'),
        apple: q('link[rel="apple-touch-icon"]') ?
          q('link[rel="apple-touch-icon"]').getAttribute('href') : null,
        theme: q('meta[name="theme-color"]') ?
          q('meta[name="theme-color"]').getAttribute('content') : null,
        mwac: !!q('meta[name="mobile-web-app-capable"]'),
        amwac: !!q('meta[name="apple-mobile-web-app-capable"]'),
        sb: !!q('meta[name="apple-mobile-web-app-status-bar-style"]'),
      };
    }""")
    check('E2E-37 head 六項 PWA 連結/meta 齊全（BR3 / E2E-39 apple-touch-icon）',
          head['manifest'] and head['apple'] == 'icons/apple-touch-icon.png' and
          head['theme'] == '#1a73e8' and head['mwac'] and head['amwac'] and head['sb'], repr(head))
    check('E2E-36/37 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ SW 註冊（E2E-36 / S2：scope 為 /web/ 子路徑）═══
    print('\n── SW 註冊：scope / controller / shell（E2E-36 / S2）──')
    ctx, page, errs = new_ctx(browser)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    reg = page.evaluate("""async () => {
      const r = await navigator.serviceWorker.ready;
      return { scope: r.scope, active: !!r.active, controlled: !!navigator.serviceWorker.controller };
    }""")
    check('E2E-36 SW 註冊成功且 active', reg['active'] is True)
    check('E2E-36 SW scope = /web/ 子路徑（S2）', reg['scope'] == URL + '/web/', reg['scope'])
    check('E2E-36 SW 控制頁面（navigator.serviceWorker.controller 存在）', reg['controlled'] is True)
    shell = page.evaluate("""async () => {
      const keys = await caches.keys();
      const c = await caches.open(keys.find(k => k.includes('airtickets-shell')));
      const urls = (await c.keys()).map(r => r.url);
      return { cacheName: keys.join(','), files: urls.map(u => u.split('/').pop()) };
    }""")
    check('E2E-36 shell cache v3 存在（Phase 2 bump v2→v3，§5.3）',
          'airtickets-shell-v3' in shell['cacheName'], shell['cacheName'])
    check('E2E-36 shell 含 pwa.js（離線 reload 前提，§2.1）', 'pwa.js' in shell['files'], shell['files'])
    check('SW 註冊 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 安裝按鈕狀態機（P1-A：E2E-01 / E2E-02 / E2E-03 / E2E-38 ① ②）═══
    print('\n── 安裝按鈕：beforeinstallprompt 狀態機（P1-A / E2E-38）──')
    ctx, page, errs = new_ctx(browser, bip=True)
    page.goto(URL + '/web/')
    wait_chart(page)
    check('E2E-38 ① 事件前安裝按鈕隱藏', is_hidden(page, '#installBtn'))
    check('E2E-38 ① 非 iOS 下 iOS hint 亦隱藏', is_hidden(page, '#iosHint'))
    page.evaluate('window.__fireBIP("dismissed")')
    page.wait_for_selector('#installBtn:not([hidden])', timeout=5000)
    check('E2E-01 事件後顯示「安裝 App」按鈕',
          page.locator('#installBtn').inner_text().strip().startswith('安裝 App'))
    check('E2E-01 按鈕含下載圖示 svg', page.locator('#installBtn svg').count() == 1)
    page.locator('#installBtn').click()
    page.wait_for_timeout(200)
    check('E2E-02 點擊按鈕才呼叫 prompt()（deferred 暫存，BR40）',
          page.evaluate('window.__install.promptCalls') == 1)
    check('E2E-03 取消（dismissed）後按鈕保留', not is_hidden(page, '#installBtn'))
    page.locator('#installBtn').click()
    page.wait_for_timeout(200)
    check('E2E-03 取消後可再次觸發 prompt（2 次）',
          page.evaluate('window.__install.promptCalls') == 2)
    page.evaluate('window.dispatchEvent(new Event("appinstalled"))')
    page.wait_for_timeout(150)
    check('E2E-02 appinstalled 後安裝按鈕隱藏', is_hidden(page, '#installBtn'))
    check('E2E-01~03 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # accepted 流程：點擊 → prompt accepted → installed（appinstalled 後隱藏）
    ctx, page, errs = new_ctx(browser, bip=True)
    page.goto(URL + '/web/')
    wait_chart(page)
    page.evaluate('window.__fireBIP("accepted")')
    page.wait_for_selector('#installBtn:not([hidden])', timeout=5000)
    page.locator('#installBtn').click()
    page.wait_for_timeout(200)
    check('E2E-02 接受安裝：prompt 回 accepted',
          page.evaluate('window.__install.promptCalls') == 1)
    page.evaluate('window.dispatchEvent(new Event("appinstalled"))')
    page.wait_for_timeout(150)
    check('E2E-02 接受後 appinstalled → 按鈕隱藏', is_hidden(page, '#installBtn'))
    check('接受流程 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ standalone 模式（P1-C / E2E-38 ③）：安裝入口永不顯示 ═══
    print('\n── standalone：安裝入口永不顯示（P1-C / E2E-38③）──')
    ctx, page, errs = new_ctx(browser, bip=True, standalone=True)
    page.goto(URL + '/web/')
    wait_chart(page)
    check('E2E-38 ③ standalone 下安裝按鈕隱藏', is_hidden(page, '#installBtn'))
    check('E2E-38 ③ standalone 下 iOS hint 隱藏', is_hidden(page, '#iosHint'))
    page.evaluate('window.__fireBIP("accepted")')
    page.wait_for_timeout(300)
    check('E2E-05 standalone 下 BIP 後按鈕仍隱藏（已安裝模式）', is_hidden(page, '#installBtn'))
    check('P1-C standalone 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ iOS UA（P1-B / E2E-04 / E2E-39）═══
    print('\n── iOS：加到主畫面 hint（P1-B / E2E-04）──')
    ctx, page, errs = new_ctx(browser, bip=True, ios=True)
    page.goto(URL + '/web/')
    wait_chart(page)
    check('E2E-04 iOS 顯示「加到主畫面」提示', not is_hidden(page, '#iosHint'))
    check('E2E-04 提示含「加到主畫面」文案',
          '加到主畫面' in page.locator('#iosHint').inner_text())
    check('E2E-04 iOS 不顯示「安裝 App」按鈕', is_hidden(page, '#installBtn'))
    page.evaluate('window.__fireBIP("accepted")')
    page.wait_for_timeout(300)
    check('E2E-04 BIP 後 iOS 安裝按鈕仍隱藏（iOS 走 hint）', is_hidden(page, '#installBtn'))
    check('P1-B iOS 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # iOS + standalone（已加到主畫面開啟）：hint 與按鈕皆隱藏（P1-C 語意）
    ctx, page, errs = new_ctx(browser, bip=True, ios=True, standalone=True)
    page.goto(URL + '/web/')
    wait_chart(page)
    check('E2E-04 iOS standalone：hint 與安裝按鈕皆隱藏',
          is_hidden(page, '#iosHint') and is_hidden(page, '#installBtn'))
    check('iOS standalone 無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ 離線 reload：SW 兜 shell + IDB 繪圖（P1-C @regression / E2E-05 離線部分）═══
    print('\n── 離線 reload：SW 兜 shell + IDB 繪圖（P1-C @regression）──')
    ctx, page, errs = new_ctx(browser, standalone=True, online_override=True)
    page.goto(URL + '/web/')
    wait_chart(page)
    wait_sw_ready(page)
    counter = ApiCounter(page)
    ctx.set_offline(True)
    ctx.add_cookies([{'name': 'offline', 'value': '1', 'url': URL + '/'}])
    resp = page.reload()
    wait_chart(page)
    check('E2E-05 離線 reload shell 由 SW 提供', resp is not None and resp.from_service_worker,
          'from_service_worker')
    check('E2E-05 離線以 IDB 快取繪出趨勢圖', page.locator('#chart path.price-line').count() == 1)
    off_text = page.locator('#offBar').inner_text()
    check('E2E-05 離線橫幅顯示上次更新時間',
          not page.locator('#offBar').evaluate('el => el.hidden') and
          '離線模式 · 顯示上次資料（' in off_text and
          len(off_text) > len('離線模式 · 顯示上次資料（') + 2, repr(off_text))
    check('E2E-05 離線 0 個 api/ 請求', counter.index == 0 and counter.trips == 0,
          f'index={counter.index} trips={counter.trips}')
    check('E2E-05 離線 standalone 下安裝入口仍隱藏',
          is_hidden(page, '#installBtn') and is_hidden(page, '#iosHint'))
    check('P1-C 離線無 console/page error', len(errs) == 0, errs[:2])
    ctx.close()

    # ═══ file:// 降級（E14 / INT-06 Phase 1：無 SW → 無安裝資格）═══
    print('\n── file:// 降級（E14 / INT-06）──')
    ctx, page, errs = new_ctx(browser)
    page.goto('file://' + os.path.join(ROOT, 'web', 'index.html'))
    page.wait_for_timeout(600)
    has_sw = page.evaluate("""async () => {
      const out = { hasSW: 'serviceWorker' in navigator };
      try {
        const reg = await navigator.serviceWorker.getRegistration();
        out.reg = reg ? reg.scope : 'none';
      } catch (e) { out.regErr = e.name; }
      try {
        await navigator.serviceWorker.register('sw.js');
        out.register = 'ok';
      } catch (e) { out.registerErr = e.name; }
      return out;
    }""")
    check('INT-06 file:// 下 SW 註冊失敗（SecurityError，無安裝資格，E14）',
          has_sw['hasSW'] and has_sw.get('registerErr') is not None and
          has_sw.get('regErr') is not None, repr(has_sw))
    check('INT-06 file:// 下安裝按鈕隱藏', is_hidden(page, '#installBtn'))
    check('INT-06 file:// 下無 pageerror（降級不炸 JS）',
          len([e for e in errs if e.startswith('PAGE:')]) == 0)
    ctx.close()

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2（T12）：訂閱 / 通知 / 離線並存 / 錯誤處理 / 商業規則 E2E
    # ═══════════════════════════════════════════════════════════════════════
    run_phase2_tests(browser)

    # ═══ 安裝性稽核（Spike S4 / MAN-11 替代：Lighthouse 13 已移除 PWA 稽核）═══
    print('\n── 安裝性稽核：CDP installability（Spike S4 / MAN-11 替代）──')
    user_dir = tempfile.mkdtemp(prefix='pwa-profile-')
    pctx = pw.chromium.launch_persistent_context(
        user_dir, executable_path=chromium_path, args=['--no-sandbox'], headless=True)
    try:
        page = pctx.pages[0] if pctx.pages else pctx.new_page()
        perrs = []
        page.on('pageerror', lambda e: perrs.append('PAGE: ' + str(e)))
        page.goto(URL + '/web/')
        wait_chart(page)
        page.wait_for_timeout(500)
        cdp = pctx.new_cdp_session(page)
        cdp.send('Page.enable')
        man = cdp.send('Page.getAppManifest')
        check('S4 Page.getAppManifest：manifest 解析無錯誤', man.get('errors') == [],
              man.get('errors'))
        check('S4 manifest URL 正確（/web/manifest.webmanifest）',
              (man.get('url') or '').endswith('/web/manifest.webmanifest'), man.get('url'))
        parsed = json.loads(man['data'])
        check('S4 manifest start_url/scope 為 ./（子路徑解析）',
              parsed.get('start_url') == './' and parsed.get('scope') == './' and
              parsed.get('display') == 'standalone')
        inst = cdp.send('Page.getInstallabilityErrors')
        err_list = inst.get('installabilityErrors', [])
        check('S4 Page.getInstallabilityErrors：errors 為空 = 可安裝',
              err_list == [], repr(err_list))
        check('S4 CDP 稽核 無 pageerror', len(perrs) == 0, perrs[:2])
    finally:
        pctx.close()


def main():
    global URL
    httpd, port = start_server()
    URL = f'http://127.0.0.1:{port}'
    print(f'server: {URL}/web/')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chromium_path, args=['--no-sandbox'])
            run_tests(browser, p)
            browser.close()
    finally:
        httpd.shutdown()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f'\n═══ PWA E2E 結果：{passed}/{total} 通過 ═══')
    for name, ok, detail in RESULTS:
        if not ok:
            print('  ❌', name, '←', detail)
    sys.exit(0 if passed == total else 1)


if __name__ == '__main__':
    main()
