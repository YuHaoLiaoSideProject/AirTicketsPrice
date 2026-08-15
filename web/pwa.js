/**
 * PWA — 前端模組（安裝狀態機 + 訂閱狀態機 + iOS 判定 + 通知純函式）
 *
 * 職責：
 *  - 安裝按鈕狀態機（beforeinstallprompt deferred prompt 暫存、重入防護）
 *  - standalone 判定輔助（shouldShowInstall：非 standalone 且可安裝才顯示）
 *  - iOS UA／版本判定（isIOS / iosVersionAtLeast，供「加到主畫面」hint 與訂閱 iOS 限制提示）
 *  - 訂閱狀態機與流程（subscriptionUI / fetchVapidPublicKey / subscribeFlow / unsubscribeFlow /
 *    shouldRequestPermission / iosSubscribeGate；user gesture 防護、防重入 F-22）
 *  - 通知純函式（resolveNotificationUrl / findNotificationTarget / formatNotification）
 *
 * UMD 匯出：瀏覽器掛全域 `Pwa`；Node（node:test）走 module.exports（對齊 cache.js / aggregate.js）。
 * 對照：docs/development/PWA.md §2.4 / §5.1 / §5.2 / §3.2；docs/tech-decisions/PWA-2026-08-15.md（D1/D4/D5/D7）
 * 測試：tests/unit/pwa.test.js（F-01~F-26）
 */
(function (global, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    global.Pwa = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ════════════════════════════════════════════════════════════
  // 設定常數（§9.4：Worker 部署後更新 PUSH_WORKER_URL；ROUTE_NAMES 與 config.py / aggregate.js 對齊）
  // ════════════════════════════════════════════════════════════
  const CONFIG = {
    PUSH_WORKER_URL: 'https://airtickets-price-push.<account>.workers.dev',  // §9.1 部署後改為實際 workers.dev 網域
    MAX_NOTIFY_DROPS: 3,                       // D4：摘要最多 3 條（與爬蟲 / Worker 一致）
    ROUTE_NAMES: { 'TPE-NRT': '東京', 'TPE-KIX': '大阪', 'TPE-FUK': '福岡', 'TPE-CTS': '札幌' },
    IOS_SUPPORT_VERSION: [16, 4],              // iOS 16.4+ installed PWA 才收得到推播（D5/D7/S3）
    THEME_COLOR: '#1a73e8',                    // 與 manifest / styles.css --accent token 一致（F-16f）
    MANIFEST_PATH: 'manifest.webmanifest',     // 相對 index.html 路徑（GitHub Pages 子路徑自動解析，S2）
  };

  // ════════════════════════════════════════════════════════════
  // 安裝狀態機（Phase 1，T4；F-01~F-03）
  // ════════════════════════════════════════════════════════════

  /**
   * 安裝狀態機：'idle'（事件前）→ 'available'（beforeinstallprompt 後，deferredPrompt 暫存）
   *             → 'installed'（prompt 接受／appinstalled 後）｜dismissed → 回 'available'（可再觸發）。
   * 回傳 { state(), setPrompt(prompt), prompt(), reset(), canInstall() }：
   *   - setPrompt：暫存 deferred prompt（重入防護：已有暫存 → 忽略重複事件；非 idle/available → 忽略）
   *   - prompt()：僅在點擊按鈕（user gesture）時呼叫（F-02）；流程中重複呼叫 → resolve null（防重入）
   *   - reset()：appinstalled 後清除暫存回 idle（§2.6）
   * @returns {{
   *   state: () => 'idle'|'available'|'installed',
   *   setPrompt: (prompt: object) => void,
   *   prompt: () => Promise<{outcome: string}|null>,
   *   reset: () => void,
   *   canInstall: () => boolean,
   * }}
   */
  function installStateMachine() {
    let _state = 'idle';
    let deferredPrompt = null;
    let prompting = false;

    /** beforeinstallprompt 事件 handler 呼叫：只暫存，不呼叫原生 prompt()（BR40） */
    function setPrompt(prompt) {
      if (_state !== 'idle' && _state !== 'available') return;  // installed 後忽略
      if (deferredPrompt) return;                               // 重入防護：已有暫存 → 保留第一份
      deferredPrompt = prompt;
      _state = 'available';
    }

    /**
     * 點擊安裝按鈕時呼叫（user gesture 內）：叫出原生安裝確認框。
     *  - 接受 → 'installed'（appinstalled 事件前即鎖定，避免連點）
     *  - 取消（dismissed）→ 回 'available'（deferred 保留，按鈕可再觸發，P1-A）
     *  - 非 available／無暫存／流程中 → resolve null（防重入，F-22 語意）
     * @returns {Promise<{outcome: 'accepted'|'dismissed'}|null>}
     */
    function prompt() {
      if (_state !== 'available' || !deferredPrompt || prompting) {
        return Promise.resolve(null);
      }
      prompting = true;
      let p;
      try {
        p = deferredPrompt.prompt();              // BeforeInstallPromptEvent.prompt()
      } catch (e) {
        prompting = false;
        return Promise.resolve(null);
      }
      if (!p || typeof p.then !== 'function') p = Promise.resolve(null);
      return p.then(userChoice => {
        prompting = false;
        const outcome = userChoice && userChoice.outcome;
        if (outcome === 'accepted') {
          _state = 'installed';                   // 接受安裝 → 鎖定（appinstalled 前即停用按鈕）
          deferredPrompt = null;
        } else if (outcome === 'dismissed') {
          _state = 'available';                   // 取消 → 回 available（deferred 保留）
        }
        return userChoice || null;
      });
    }

    /** appinstalled 事件 handler 呼叫：清暫存回 idle（§5.1；standalone 下按鈕本就不顯示） */
    function reset() {
      _state = 'idle';
      deferredPrompt = null;
      prompting = false;
    }

    function canInstall() {
      return _state === 'available' && !!deferredPrompt && !prompting;
    }

    return {
      state: () => _state,
      setPrompt,
      prompt,
      reset,
      canInstall,
    };
  }

  /**
   * 安裝按鈕是否顯示（P1-C / BR4）：僅「非 standalone 且狀態 available」時顯示。
   * standalone（matchMedia('(display-mode: standalone)') 或 navigator.standalone）→ false；
   * 已安裝（installed）→ false；事件未觸發（idle）→ false。
   * @param {string} state - installStateMachine().state() 的回傳值
   * @param {boolean} isStandalone - 是否以 standalone 模式執行
   * @returns {boolean}
   */
  function shouldShowInstall(state, isStandalone) {
    if (isStandalone) return false;             // P1-C：已安裝模式永不顯示
    if (state === 'installed') return false;    // 已安裝
    return state === 'available';               // 僅 beforeinstallprompt 後顯示
  }

  // ════════════════════════════════════════════════════════════
  // iOS 判定（Phase 1/2；F-04 / F-21）
  // ════════════════════════════════════════════════════════════

  /**
   * iOS UA 判定：/iPhone|iPad|iPod/（F-04）。
   * ⚠️ 限制（EC9）：iPadOS 13+ 桌面模式偽裝 Mac UA 會漏判；保守策略（navigator.maxTouchPoints > 1
   * 且 /Macintosh/）須由呼叫端以 navigator 資訊補強（純函式只吃 UA 字串，保持可測）。
   * @param {string} ua - navigator.userAgent
   * @returns {boolean}
   */
  function isIOS(ua) {
    return /iPhone|iPad|iPod/.test(ua || '');
  }

  /**
   * iOS 版本 ≥ (major, minor)：解析 UA 'CPU iPhone OS 17_5 like Mac OS X' 或 iPad 'CPU OS 16_4 ...'（F-21）。
   * 解析失敗（非 iOS／缺版本）→ false（保守）。<16.4 → 訂閱時顯示 iOS 推播限制提示（EC6）。
   * @param {string} ua - navigator.userAgent
   * @param {number} major - 需求主版號（如 16）
   * @param {number} [minor=0] - 需求次版號（如 4）
   * @returns {boolean}
   */
  function iosVersionAtLeast(ua, major, minor = 0) {
    const m = /CPU (?:iPhone )?OS (\d+)(?:[_.](\d+))?/.exec(ua || '');
    if (!m) return false;
    const vMajor = parseInt(m[1], 10);
    const vMinor = m[2] !== undefined ? parseInt(m[2], 10) : 0;
    return vMajor > major || (vMajor === major && vMinor >= minor);
  }

  // ════════════════════════════════════════════════════════════
  // 訂閱狀態機與流程（Phase 2，T9；F-05~F-13, F-20, F-22, F-23, F-26, EC6）
  // 對照：docs/development/PWA.md §2.4 / §5.2；docs/test-plans/PWA測試計畫.md §4.2
  // ════════════════════════════════════════════════════════════

  /** 訂閱流程防重入旗標（F-22：快速連點只觸發一次；F-23：失敗/中止後 finally 清除） */
  let _subscribing = false;

  /**
   * 訂閱 UI 三態 + 暫時性狀態（F-05a~d / F-10 / F-20 / F-26，§5.2）：
   *   permission + subscription + opts.vapidReady →
   *   'unsubscribed'（「開啟票價提醒」）｜'subscribed'（「關閉票價提醒」＋「已訂閱」）｜
   *   'denied'（拒絕引導，E1）｜暫時性：'loading'（流程中）｜'error'（E2）｜'unavailable'（E3）
   * @param {string} permission - Notification.permission（'default'|'granted'|'denied'）
   * @param {object|null} subscription - pushManager.getSubscription() 結果（F-20/F-26 唯一真相）
   * @param {{vapidReady?: boolean, state?: 'loading'|'error'}} [opts]
   * @returns {{state: string, buttonLabel: string, hint: string, retryable: boolean}}
   */
  function subscriptionUI(permission, subscription, opts) {
    const o = opts || {};
    if (o.vapidReady === false) {
      return { state: 'unavailable', buttonLabel: '開啟票價提醒', hint: '提醒功能暫時不可用', retryable: true };  // E3
    }
    if (o.state === 'loading') {
      return { state: 'loading', buttonLabel: '處理中…', hint: '', retryable: false };
    }
    if (o.state === 'error') {
      return { state: 'error', buttonLabel: '開啟票價提醒', hint: '訂閱失敗，請稍後重試', retryable: true };  // E2
    }
    if (permission === 'denied') {
      return { state: 'denied', buttonLabel: '開啟票價提醒', hint: '通知已封鎖，請到瀏覽器網站設定中允許通知', retryable: true };  // E1
    }
    if (permission === 'granted' && !!subscription) {
      return { state: 'subscribed', buttonLabel: '關閉票價提醒', hint: '已訂閱', retryable: false };
    }
    return { state: 'unsubscribed', buttonLabel: '開啟票價提醒', hint: '', retryable: true };
  }

  /** 是否可請求權限（E1：denied 不重複 requestPermission） */
  function shouldRequestPermission(permission) {
    return permission !== 'denied';
  }

  /**
   * iOS 訂閱限制判定（E8 / EC6）：
   *  - iOS 且非 standalone → blocked（需加到主畫面後才收得到通知，E8；不發權限請求）
   *  - iOS standalone 但 <16.4 → blocked（需加到主畫面且 16.4+，EC6）
   *  - 其餘 → 不阻擋
   * @param {string} ua - navigator.userAgent
   * @param {boolean} standalone - 是否以 standalone（加到主畫面）模式執行
   * @returns {{blocked: boolean, state?: string, hint: string}}
   */
  function iosSubscribeGate(ua, standalone) {
    if (!isIOS(ua)) return { blocked: false, hint: '' };
    if (!standalone) {
      return { blocked: true, state: 'ios-required', hint: '需加到主畫面後才收得到通知' };  // E8
    }
    if (!iosVersionAtLeast(ua, CONFIG.IOS_SUPPORT_VERSION[0], CONFIG.IOS_SUPPORT_VERSION[1])) {
      return { blocked: true, state: 'ios-unsupported', hint: '需加到主畫面且 iOS 16.4+ 才收得到通知' };  // EC6
    }
    return { blocked: false, hint: '' };
  }

  /**
   * VAPID 公鑰抓取（E3 / F-10b）：GET {PUSH_WORKER_URL}/vapid-public-key → base64url 字串。
   * 失敗（HTTP 非 2xx / 壞 shape / 網路錯）→ null（app.js 停用按鈕＋「提醒功能暫時不可用」）。
   * ⚠️ 未部署（CONFIG.PUSH_WORKER_URL 仍為占位網域）→ 直接回 null，不發無意義請求（§9.4）。
   * @param {string} [url] - 端點 URL（預設 CONFIG.PUSH_WORKER_URL + '/vapid-public-key'）
   * @param {(url: string) => Promise<Response>} [fetchImpl] - 可注入 fetch（Node 測試 mock）
   * @returns {Promise<string|null>}
   */
  async function fetchVapidPublicKey(url, fetchImpl) {
    const u = url || CONFIG.PUSH_WORKER_URL + '/vapid-public-key';
    if (!u || u.includes('<account>')) return null;   // 未部署：占位網域不可解析（E3 降級）
    let res;
    try {
      res = await (fetchImpl || fetch)(u);
    } catch (e) {
      return null;
    }
    if (!res || !res.ok) return null;
    let data;
    try {
      data = await res.json();
    } catch (e) {
      return null;
    }
    return (data && typeof data.publicKey === 'string' && data.publicKey) || null;
  }

  /**
   * 瀏覽器環境依賴（subscribeFlow / unsubscribeFlow 缺省值；Node 測試以 deps 注入覆寫）。
   * 所有存取皆以 typeof 守衛，pwa.js 在 SW（importScripts）與 Node 環境皆安全載入。
   */
  function browserDeps() {
    const nav = typeof navigator !== 'undefined' ? navigator : null;
    const win = typeof window !== 'undefined' ? window : null;
    const notification = typeof Notification !== 'undefined' ? Notification : null;
    return {
      ua: nav ? nav.userAgent : '',
      standalone: !!(nav && (nav.standalone === true ||
        (win && win.matchMedia && win.matchMedia('(display-mode: standalone)').matches))),
      userGesture: true,
      permission: notification ? notification.permission : 'default',
      getRegistration: () => nav.serviceWorker.ready,
      requestPermission: () => (notification && notification.requestPermission)
        ? notification.requestPermission()
        : Promise.resolve('denied'),
      subscribe: (reg, key) => reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }),
      postSubscribe: async (sub) => {
        const res = await fetch(CONFIG.PUSH_WORKER_URL + '/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(sub),
        });
        return { ok: res.ok, status: res.status };
      },
    };
  }

  /**
   * 訂閱流程（F-06/F-07/F-09/F-11/F-12/F-13/EC6/F-22/F-23）：**僅在按鈕 click（user gesture）handler 內呼叫**。
   * ① user gesture 守衛（F-06）② iOS 限制判定（E8/EC6，不發權限請求）③ 權限（E1 不重複；E4 忽略無錯誤）
   * ④ VAPID 公鑰（E3）⑤ PushManager.subscribe({userVisibleOnly, applicationServerKey})（E2 失敗分支）
   * ⑥ POST /subscribe {endpoint, keys, action:'add'}（免 token；Worker 以 Origin 白名單 + 格式驗證保護，T9）
   * 防重入：模組級 `_subscribing` 旗標，流程中忽略重複呼叫（F-22）；finally 清除（F-23 無殘留狀態）。
   * @param {object} [deps] 可注入依賴（缺省取瀏覽器全域，見 browserDeps）
   *   - ua / standalone / userGesture / permission / vapidKey / getRegistration /
   *     requestPermission / subscribe / postSubscribe
   * @returns {Promise<{state: string, hint: string}>}
   */
  async function subscribeFlow(deps) {
    if (_subscribing) return { state: 'busy', hint: '' };                                  // F-22 防重入
    if (deps && deps.userGesture === false) return { state: 'unsubscribed', hint: '' };    // F-06 守衛
    const d = Object.assign(browserDeps(), deps || {});
    const gate = iosSubscribeGate(d.ua, d.standalone);
    if (gate.blocked) return { state: gate.state, hint: gate.hint };                       // E8 / EC6
    if (!shouldRequestPermission(d.permission)) {                                          // E1：denied 不重複詢問
      return { state: 'denied', hint: '通知已封鎖，請到瀏覽器網站設定中允許通知' };
    }
    _subscribing = true;
    try {
      // ③ 權限（D5：唯一 requestPermission 入口；由 click handler 的 user gesture 驅動）
      let permission;
      try {
        permission = await d.requestPermission();
      } catch (e) {
        return { state: 'error', hint: '訂閱失敗，請稍後重試' };
      }
      if (permission === 'denied') return { state: 'denied', hint: '通知已封鎖，請到瀏覽器網站設定中允許通知' };  // E1
      if (permission === 'default') return { state: 'unsubscribed', hint: '' };  // E4：詢問被忽略 → 無錯誤
      // ④ VAPID 公鑰（E3：抓取失敗 → 停用＋暫時不可用）
      let key = d.vapidKey;
      if (key === undefined || key === null) key = await fetchVapidPublicKey();
      if (!key) return { state: 'unavailable', hint: '提醒功能暫時不可用' };
      // ⑤ 建立訂閱（E2：subscribe 拋錯 → 可重試）
      const reg = await d.getRegistration();
      let sub;
      try {
        sub = await d.subscribe(reg, key);
      } catch (e) {
        return { state: 'error', hint: '訂閱失敗，請稍後重試' };
      }
      if (!sub || !sub.endpoint) return { state: 'error', hint: '訂閱失敗，請稍後重試' };
      // ⑥ 寫入 Worker（免 token；body = {endpoint, keys, action:'add'}，§3.1 T9 合約）
      const body = {
        endpoint: sub.endpoint,
        keys: sub.keys,
        ...(sub.expirationTime !== undefined && sub.expirationTime !== null ? { expirationTime: sub.expirationTime } : {}),
        action: 'add',
      };
      let res;
      try {
        res = await d.postSubscribe(body);
      } catch (e) {
        return { state: 'error', hint: '訂閱失敗，請稍後重試' };
      }
      if (!res || !res.ok) return { state: 'error', hint: '訂閱失敗，請稍後重試' };  // E2（含 Worker 403/400）
      return { state: 'subscribed', hint: '已訂閱' };
    } finally {
      _subscribing = false;   // F-23：流程失敗/中止 → 旗標清除，無殘留狀態
    }
  }

  /**
   * 退訂流程（F-08 / P2-C）：getSubscription().unsubscribe()（本機移除）→
   * POST /subscribe {endpoint, action:'remove'}（Worker 刪除 KV 記錄）→ 回 'unsubscribed'。
   * 冪等：無本機訂閱 → 不發 remove、直接回未訂閱。
   * @param {object} [deps] 可注入依賴（getRegistration / postSubscribe）
   * @returns {Promise<{state: string, hint: string}>}
   */
  async function unsubscribeFlow(deps) {
    const d = Object.assign(browserDeps(), deps || {});
    const reg = await d.getRegistration();
    let sub = null;
    try {
      sub = await reg.pushManager.getSubscription();
    } catch (e) {
      sub = null;
    }
    const endpoint = sub && sub.endpoint ? sub.endpoint : null;
    if (sub && typeof sub.unsubscribe === 'function') {
      try { await sub.unsubscribe(); } catch (e) { /* 本機移除失敗不阻斷 Worker 刪除 */ }
    }
    if (endpoint) {
      try {
        const res = await d.postSubscribe({ endpoint, action: 'remove' });
        if (!res || !res.ok) return { state: 'error', hint: '退訂失敗，請稍後重試' };
      } catch (e) {
        return { state: 'error', hint: '退訂失敗，請稍後重試' };
      }
    }
    return { state: 'unsubscribed', hint: '' };
  }

  // ════════════════════════════════════════════════════════════
  // 通知純函式（Phase 1/2；F-14 / F-14b / F-19a/b）
  // ════════════════════════════════════════════════════════════

  /**
   * notificationclick deep-link 拼接（F-14 / EC3）：data.url 為**相對 SW scope** 路徑
   * （'?route=TPE-NRT'），以 new URL(dataUrl, scope) 解析 → 任何子路徑深度皆正確
   * （/AirTicketsPrice/web/?route=TPE-NRT）。scope 缺尾斜線時補上（registration.scope 必以 / 結尾）。
   * @param {string} scope - self.registration.scope（以 / 結尾）
   * @param {string} dataUrl - 通知 data.url（相對路徑；空 → 以 scope 為基準回首頁）
   * @returns {string} 解析後的絕對 URL
   */
  function resolveNotificationUrl(scope, dataUrl) {
    const base = /\/$/.test(scope || '') ? scope : (scope || '') + '/';
    return new URL(dataUrl || './', base).href;
  }

  /**
   * notificationclick 分頁決策（F-14b / E10 / EC8）：既有分頁（同 origin）→ 回傳該 client（focus/navigate）；
   * 否則 → null（openWindow 開新窗）。多則通知點擊一則只作用於該則（EC8，SW 以 event 自帶 data 分流）。
   * @param {Array<{url: string}>|null} clients - clients.matchAll({type:'window'}) 結果
   * @param {string} url - 已解析的絕對 deep-link URL
   * @returns {{url: string}|null}
   */
  function findNotificationTarget(clients, url) {
    const list = Array.isArray(clients) ? clients : [];
    const targetOrigin = new URL(url).origin;
    return list.find(c => new URL(c.url).origin === targetOrigin) || null;
  }

  /** 月/日去前導零：'2026-08-22' → '8/22'（§3.2；無效日期 → '—'） */
  function formatMonthDay(iso) {
    if (!iso) return '—';
    const p = String(iso).split('-');
    if (p.length < 3) return '—';
    return parseInt(p[1], 10) + '/' + parseInt(p[2], 10);
  }

  /** 價格千分位：24120 → '24,120'（§3.2；非數值 → 原值字串） */
  function formatPrice(n) {
    if (typeof n !== 'number' || !Number.isFinite(n)) return String(n == null ? '' : n);
    return n.toLocaleString('en-US');
  }

  /**
   * 通知承載格式化（F-19a/b；與 worker/src/index.js formatNotification **同簽名合約**）：
   *   title = '✈️ 票價下降了！'
   *   body  = drops 逐筆「{route} {名稱} {M}/{D}–{M}/{D} 降至 NT${new}（原 NT${old}）」以 '\n' 連接
   *           （範例：'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）'；價格千分位、月日去前導零）
   *   data.url = '?route=' + drops[0].route（**相對 SW scope** 路徑，sw.js 以 registration.scope 拼接）
   *   drops 為空／非陣列 → title 不變、body '有票價更新'、data.url '?route='（§5.4 fallback）
   * @param {Array<{route: string, outbound_date: string, return_date: string,
   *                old_price: number, new_price: number}>} drops
   * @returns {{title: string, body: string, data: {url: string}}}
   */
  function formatNotification(drops) {
    const list = Array.isArray(drops) ? drops.slice(0, CONFIG.MAX_NOTIFY_DROPS) : [];
    const title = '✈️ 票價下降了！';
    if (list.length === 0) {
      return { title, body: '有票價更新', data: { url: '?route=' } };
    }
    const body = list.map(d => {
      const name = CONFIG.ROUTE_NAMES[d.route] || d.route || '';
      return d.route + ' ' + name + ' ' + formatMonthDay(d.outbound_date) + '–' +
        formatMonthDay(d.return_date) + ' 降至 NT$' + formatPrice(d.new_price) +
        '（原 NT$' + formatPrice(d.old_price) + '）';
    }).join('\n');
    return { title, body, data: { url: '?route=' + (list[0].route || '') } };
  }

  // UMD 匯出（tests/unit/pwa.test.js require 的公開面）
  return {
    installStateMachine,
    shouldShowInstall,
    isIOS,
    iosVersionAtLeast,
    subscriptionUI,
    shouldRequestPermission,
    iosSubscribeGate,
    fetchVapidPublicKey,
    subscribeFlow,
    unsubscribeFlow,
    resolveNotificationUrl,
    findNotificationTarget,
    formatNotification,
    CONFIG,
  };
});
