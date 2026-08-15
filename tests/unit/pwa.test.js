// PWA Phase 1 — manifest / 圖示靜態驗證單元測試（TDD 紅燈階段，Step 1/2）
// 對照：docs/test-plans/PWA測試計畫.md F-16 / F-18（manifest 欄位、圖示存在與尺寸）
//       docs/development/PWA.md §2.2（manifest 規格）、§2.3（圖示）
//       docs/bdds/PWA.feature（@business-rules：manifest 欄位齊全、maskable 圖示）
// 執行：node --test tests/unit/pwa.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const MANIFEST_PATH = path.join(ROOT, 'web', 'manifest.webmanifest');
const STYLES_PATH = path.join(ROOT, 'web', 'styles.css');
const ICONS_DIR = path.join(ROOT, 'web', 'icons');
const INDEX_HTML_PATH = path.join(ROOT, 'web', 'index.html');
const SW_PATH = path.join(ROOT, 'web', 'sw.js');

// web/pwa.js（UMD，對齊 cache.js）：瀏覽器掛全域 `Pwa`；Node 測試走 module.exports
const Pwa = require('../../web/pwa.js');

/**
 * 解析 PNG IHDR 實際尺寸（width / height，big-endian）。
 * PNG 8-byte signature + IHDR chunk：length(4) + "IHDR"(4) + width(4) + height(4)，
 * 故 width = bytes[16..20)、height = bytes[20..24)。
 */
function pngSize(buf) {
  assert.equal(buf.readUInt32BE(0), 0x89504e47, 'PNG signature 錯誤');
  assert.equal(buf.toString('latin1', 12, 16), 'IHDR', '缺少 IHDR chunk');
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

// ════════════════════════════════════════════════════════════
// Phase 1 — 安裝狀態機與 iOS 判定（F-01~F-04 / F-21）
// 對照：docs/development/PWA.md §2.4 / §5.1；docs/test-plans/PWA測試計畫.md §4.1
// ════════════════════════════════════════════════════════════

/** 假的 BeforeInstallPromptEvent（deferred prompt）：prompt() 回傳 userChoice promise */
function fakePrompt(outcome, { spyPrompt = true } = {}) {
  let calls = 0;
  const p = {
    prompt() {
      calls += 1;
      return Promise.resolve({ outcome, platform: 'web' });
    },
    userChoice: null,
  };
  if (spyPrompt) p.promptCalls = () => calls;
  return p;
}

// ── F-01 / F-02 安裝狀態機（§5.1：idle → available → installed｜cancelled→available）──
test('F-01 安裝狀態機初始為 idle：事件前不顯示安裝按鈕（P1-A / BR4）', () => {
  const sm = Pwa.installStateMachine();
  assert.equal(sm.state(), 'idle');
  assert.equal(sm.canInstall(), false);
  // shouldShowInstall('idle') → false（事件前按鈕不顯示，E2E-38 ①）
  assert.equal(Pwa.shouldShowInstall(sm.state(), false), false);
});

test('F-01b beforeinstallprompt 後 → available：可安裝（P1-A / E2E-38 ②）', () => {
  const sm = Pwa.installStateMachine();
  sm.setPrompt(fakePrompt('accepted'));
  assert.equal(sm.state(), 'available');
  assert.equal(sm.canInstall(), true);
  assert.equal(Pwa.shouldShowInstall(sm.state(), false), true);
});

test('F-02 deferred prompt 暫存：點擊才呼叫 prompt()；取消後可再觸發（P1-A）', async () => {
  const sm = Pwa.installStateMachine();
  const deferred = fakePrompt('dismissed');
  sm.setPrompt(deferred);
  // setPrompt 只暫存，不得呼叫原生 prompt()（F-02 / BR40）
  assert.equal(deferred.promptCalls(), 0, 'setPrompt 不應呼叫 prompt()');
  // 點擊安裝按鈕 → 才呼叫 prompt()
  const choice1 = await sm.prompt();
  assert.equal(deferred.promptCalls(), 1);
  assert.equal(choice1.outcome, 'dismissed');
  // 取消 → 回 available（deferred 保留，按鈕保留可再觸發）
  assert.equal(sm.state(), 'available');
  assert.equal(sm.canInstall(), true);
  const choice2 = await sm.prompt();
  assert.equal(deferred.promptCalls(), 2, '取消後按鈕可再次觸發 prompt');
  assert.equal(choice2.outcome, 'dismissed');
});

test('F-02b 接受安裝 → installed：按鈕隱藏且不可再觸發（P1-A）', async () => {
  const sm = Pwa.installStateMachine();
  sm.setPrompt(fakePrompt('accepted'));
  const choice = await sm.prompt();
  assert.equal(choice.outcome, 'accepted');
  assert.equal(sm.state(), 'installed');
  assert.equal(sm.canInstall(), false);
  assert.equal(Pwa.shouldShowInstall(sm.state(), false), false);
  // installed 後再呼叫 prompt → 無作用（防重入）
  const again = await sm.prompt();
  assert.equal(again, null);
});

test('F-02c 重入防護：重複 setPrompt 不覆寫；prompt 流程中忽略重複呼叫', async () => {
  const sm = Pwa.installStateMachine();
  const first = fakePrompt('dismissed');
  const second = fakePrompt('accepted');
  sm.setPrompt(first);
  sm.setPrompt(second); // 已 available → 忽略，保留 first
  assert.equal(sm.state(), 'available');
  let resolveDeferred;
  const inflight = {
    prompt() {
      return new Promise(r => { resolveDeferred = r; });
    },
  };
  const sm2 = Pwa.installStateMachine();
  sm2.setPrompt(inflight);
  const p1 = sm2.prompt();
  const p2 = sm2.prompt(); // 流程中 → 忽略（F-22 語意：防重入）
  const r2 = await p2;
  assert.equal(r2, null);
  resolveDeferred({ outcome: 'dismissed' });
  await p1;
  assert.equal(sm2.state(), 'available');
});

test('F-02d reset（appinstalled 後）→ 回 idle：standalone 下按鈕永不顯示', () => {
  const sm = Pwa.installStateMachine();
  sm.setPrompt(fakePrompt('accepted'));
  sm.reset(); // appinstalled → reset（§2.6）
  assert.equal(sm.state(), 'idle');
  assert.equal(sm.canInstall(), false);
});

// ── F-03 shouldShowInstall（P1-C：已安裝模式隱藏安裝入口）──
test('F-03 非 standalone 且 available → true；standalone／已安裝／idle → false（P1-C）', () => {
  assert.equal(Pwa.shouldShowInstall('available', false), true);
  assert.equal(Pwa.shouldShowInstall('available', true), false);   // standalone（P1-C）
  assert.equal(Pwa.shouldShowInstall('installed', false), false);  // 已安裝
  assert.equal(Pwa.shouldShowInstall('idle', false), false);       // 事件未觸發
  assert.equal(Pwa.shouldShowInstall('cancelled', false), false);  // 未知狀態保守隱藏
});

// ── F-04 isIOS（P1-B / BR5：iOS 依 UA 顯示「加到主畫面」提示）──
test('F-04 isIOS：iPhone/iPad/iPod UA → true；其他 → false', () => {
  assert.equal(Pwa.isIOS('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148'), true);
  assert.equal(Pwa.isIOS('Mozilla/5.0 (iPad; CPU OS 16_4 like Mac OS X) AppleWebKit/605.1.15'), true);
  assert.equal(Pwa.isIOS('Mozilla/5.0 (iPod touch; CPU iPhone OS 15_6 like Mac OS X)'), true);
  assert.equal(Pwa.isIOS('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'), false);
  assert.equal(Pwa.isIOS('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126'), false);
  assert.equal(Pwa.isIOS(''), false);
});

// ── F-21 iosVersionAtLeast（EC6：iOS 16.4+ 才收得到推播）──
test('F-21 iosVersionAtLeast：16.4/16.3/17.x 判定（EC6）', () => {
  const ua164 = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) Mobile/15E148';
  const ua163 = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) Mobile/15E148';
  const ua175 = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148';
  assert.equal(Pwa.iosVersionAtLeast(ua164, 16, 4), true);   // 16.4 恰好 → true
  assert.equal(Pwa.iosVersionAtLeast(ua163, 16, 4), false);  // 16.3 → false（<16.4 限制提示）
  assert.equal(Pwa.iosVersionAtLeast(ua175, 16, 4), true);   // 17.x → true
  assert.equal(Pwa.iosVersionAtLeast(ua175, 17, 0), true);
  assert.equal(Pwa.iosVersionAtLeast(ua175, 17, 5), true);   // 17.5 ≥ 17.5 → true
  assert.equal(Pwa.iosVersionAtLeast(ua175, 17, 6), false);  // 17.5 < 17.6 → false
  assert.equal(Pwa.iosVersionAtLeast('Mozilla/5.0 (Macintosh; Intel Mac OS X) Chrome/126', 16, 4), false); // 非 iOS → false
  assert.equal(Pwa.iosVersionAtLeast('', 16, 4), false);
});

// ════════════════════════════════════════════════════════════
// Phase 1/2 — 通知純函式骨架（F-14 / F-19a/b，§2.4）
// ════════════════════════════════════════════════════════════

// ── F-14 resolveNotificationUrl（EC3：以 SW scope 為基準拼接子路徑 deep-link）──
test('F-14 resolveNotificationUrl：相對 ?route= 以 scope 拼接（EC3 / §3.2）', () => {
  const scope = 'https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/';
  assert.equal(
    Pwa.resolveNotificationUrl(scope, '?route=TPE-NRT'),
    'https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/?route=TPE-NRT');
  assert.equal(
    Pwa.resolveNotificationUrl(scope, '?route=TPE-KIX'),
    'https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/?route=TPE-KIX');
  // 空 dataUrl → 以 scope 為基準（fallback 首頁）
  assert.equal(Pwa.resolveNotificationUrl(scope, ''),
    'https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/');
  // scope 缺尾斜線時防呆（registration.scope 必以 / 結尾，但純函式防呆）
  assert.equal(
    Pwa.resolveNotificationUrl('https://x.test/AirTicketsPrice/web', '?route=TPE-NRT'),
    'https://x.test/AirTicketsPrice/web/?route=TPE-NRT');
});

// ── F-19a/b formatNotification（BR10：通知承載單則摘要格式，Outline ×2）──
const DROP_NRT = {
  route: 'TPE-NRT', outbound_date: '2026-08-22', return_date: '2026-08-30',
  flight_no: 'JX 804', old_price: 26008, new_price: 24120,
};
const DROP_KIX = {
  route: 'TPE-KIX', outbound_date: '2026-08-23', return_date: '2026-08-31',
  flight_no: 'JX 202', old_price: 12900, new_price: 11500,
};

test('F-19a 通知承載（Outline row 1：TPE-NRT 東京）', () => {
  const n = Pwa.formatNotification([DROP_NRT]);
  assert.equal(n.title, '✈️ 票價下降了！');
  assert.equal(n.body, 'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）');
  assert.deepEqual(n.data, { url: '?route=TPE-NRT' });   // 相對 SW scope 路徑（§3.2 canonical）
});

test('F-19b 通知承載（Outline row 2：TPE-KIX 大阪）', () => {
  const n = Pwa.formatNotification([DROP_KIX]);
  assert.equal(n.title, '✈️ 票價下降了！');
  assert.equal(n.body, 'TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）');
  assert.deepEqual(n.data, { url: '?route=TPE-KIX' });
});

test('F-19c 通知承載：多筆以 \n 連接、data.url 取第一筆（P2-B 單則摘要）', () => {
  const n = Pwa.formatNotification([DROP_NRT, DROP_KIX]);
  assert.equal(n.body,
    'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）\n' +
    'TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）');
  assert.deepEqual(n.data, { url: '?route=TPE-NRT' });
});

test('F-19d 通知承載：空 drops → body「有票價更新」、data.url「?route=」（§5.4 fallback）', () => {
  const n = Pwa.formatNotification([]);
  assert.equal(n.title, '✈️ 票價下降了！');
  assert.equal(n.body, '有票價更新');
  assert.deepEqual(n.data, { url: '?route=' });
  const n2 = Pwa.formatNotification(null);   // 防呆：非陣列視同空
  assert.equal(n2.body, '有票價更新');
});

test('F-19e 通知承載：月/日去前導零、價格千分位（§3.2 規則）', () => {
  const n = Pwa.formatNotification([{
    route: 'TPE-FUK', outbound_date: '2026-10-05', return_date: '2026-10-13',
    flight_no: 'JX 306', old_price: 9999, new_price: 8888,
  }]);
  assert.equal(n.body, 'TPE-FUK 福岡 10/5–10/13 降至 NT$8,888（原 NT$9,999）');
});

// ════════════════════════════════════════════════════════════
// Phase 1 — 整合靜態驗證（F-17 / F-16e，§2.5 / §2.7）
// ════════════════════════════════════════════════════════════

// ── F-17 index.html 六項 PWA 連結/meta + pwa.js script（BR3 / E2E-37）──
test('F-17 index.html 具備 PWA 連結與 iOS meta（BR3）', () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  assert.match(html, /<link rel="manifest" href="manifest\.webmanifest">/);
  assert.match(html, /<link rel="apple-touch-icon" href="icons\/apple-touch-icon\.png">/);
  assert.match(html, /<meta name="theme-color" content="#1a73e8">/);
  assert.match(html, /<meta name="mobile-web-app-capable" content="yes">/);
  assert.match(html, /<meta name="apple-mobile-web-app-capable" content="yes">/);
  assert.match(html, /<meta name="apple-mobile-web-app-status-bar-style" content="default">/);
  // pwa.js 必須在 app.js 前載入（app.js 依賴 window.Pwa）
  const pwaIdx = html.indexOf('src="pwa.js"');
  const appIdx = html.indexOf('src="app.js"');
  assert.ok(pwaIdx >= 0 && appIdx >= 0 && pwaIdx < appIdx,
    'pwa.js script 應在 app.js 前');
});

test('F-17b index.html 含安裝按鈕與 iOS 提示 DOM，預設 hidden（§2.5）', () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  assert.match(html, /id="installBtn"[^>]*hidden/);
  assert.match(html, /id="iosHint"[^>]*hidden/);
  assert.match(html, /id="pwaActions"/);
});

// ── F-16e sw.js：SHELL 7 檔（+= pwa.js）、cache bump v3（§2.7 / §5.3）──
test('F-16e sw.js SHELL 含 pwa.js 且 cache 版本為 v3（§2.7 / §5.3）', () => {
  const sw = fs.readFileSync(SW_PATH, 'utf8');
  assert.match(sw, /airtickets-shell-v3/);
  assert.ok(!/airtickets-shell-v2/.test(sw), 'Phase 2 bump v2→v3（§5.3 版本管理）');
  const m = sw.match(/const SHELL\s*=\s*\[([\s\S]*?)\];/);
  assert.ok(m, 'sw.js 應定義 SHELL 清單');
  const body = m[1].replace(/\/\/[^\n]*/g, '');   // 剝除陣列內行註解
  const items = body.split(',').map(s => s.trim().replace(/^'|'$/g, '')).filter(Boolean);
  assert.ok(items.includes('pwa.js'), 'SHELL 應含 pwa.js（離線 reload 不可缺）');
  assert.equal(items.length, 7, 'SHELL 應為 7 檔（6 → +pwa.js）');
  for (const f of ['index.html', 'styles.css', 'app.js', 'aggregate.js', 'cache.js', 'sw.js']) {
    assert.ok(items.includes(f), `SHELL 缺 ${f}`);
  }
});

// ── F-16 manifest 欄位齊全（BDD BR：manifest 欄位齊全）──
test('F-16 manifest 存在且是合法 JSON', () => {
  const raw = fs.readFileSync(MANIFEST_PATH, 'utf8');
  const manifest = JSON.parse(raw);   // 拋出 = 非合法 JSON（紅燈時檔案不存在 → 拋 ENOENT）
  assert.ok(manifest && typeof manifest === 'object');
});

test('F-16b manifest 必要欄位齊全且值正確（§2.2 D4 規格）', () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  assert.equal(manifest.name, '星宇機票價格趨勢');
  assert.equal(manifest.short_name, '票價趨勢');
  assert.ok(typeof manifest.description === 'string' && manifest.description.length > 0);
  assert.equal(manifest.lang, 'zh-Hant');
  assert.equal(manifest.start_url, './');     // GitHub Pages 子路徑部署（S2）
  assert.equal(manifest.scope, './');
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.theme_color, '#1a73e8');
  assert.equal(manifest.background_color, '#ffffff');
});

test('F-16c theme_color 與 web/styles.css 的 --accent token 一致', () => {
  const css = fs.readFileSync(STYLES_PATH, 'utf8');
  const m = css.match(/--accent:\s*(#[0-9a-fA-F]{3,8})/);
  assert.ok(m, 'styles.css 應定義 --accent token');
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  assert.equal(manifest.theme_color.toLowerCase(), m[1].toLowerCase());
});

test('F-16d icons 含 192x192、512x512、512x512 maskable，type 皆為 image/png', () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  assert.ok(Array.isArray(manifest.icons) && manifest.icons.length >= 3);
  const bySrc = new Map(manifest.icons.map((ic) => [ic.src, ic]));
  for (const ic of manifest.icons) assert.equal(ic.type, 'image/png');

  const i192 = bySrc.get('icons/icon-192.png');
  assert.ok(i192, '缺 icons/icon-192.png 項目');
  assert.equal(i192.sizes, '192x192');

  const i512 = bySrc.get('icons/icon-512.png');
  assert.ok(i512, '缺 icons/icon-512.png 項目');
  assert.equal(i512.sizes, '512x512');

  const mask = bySrc.get('icons/icon-512-maskable.png');
  assert.ok(mask, '缺 icons/icon-512-maskable.png 項目');
  assert.equal(mask.sizes, '512x512');
  assert.equal(mask.purpose, 'maskable');     // BDD BR：maskable 用途
});

// ── CONFIG 骨架常數（§2.4：theme_color / iOS 最低版本）──
test('F-16f pwa.js CONFIG：theme_color 與 iOS 最低版本 16.4（§2.4 / §2.2）', () => {
  assert.equal(Pwa.CONFIG.THEME_COLOR, '#1a73e8');
  assert.deepEqual(Pwa.CONFIG.IOS_SUPPORT_VERSION, [16, 4]);
  assert.equal(Pwa.CONFIG.MAX_NOTIFY_DROPS, 3);
  // theme_color 與 manifest / styles.css token 一致（F-16c 同源）
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  assert.equal(Pwa.CONFIG.THEME_COLOR.toLowerCase(), manifest.theme_color.toLowerCase());
  assert.equal(Pwa.CONFIG.ROUTE_NAMES['TPE-NRT'], '東京');
});

// ── F-18 圖示檔案存在且實際尺寸正確（PNG IHDR 解析，對應 Pillow --check）──
test('F-18 圖示檔案存在且尺寸正確（icon-192=192 / icon-512=512 / maskable=512 / apple-touch=180）', () => {
  const expect = [
    ['icon-192.png', 192],
    ['icon-512.png', 512],
    ['icon-512-maskable.png', 512],
    ['apple-touch-icon.png', 180],
  ];
  for (const [file, size] of expect) {
    const p = path.join(ICONS_DIR, file);
    assert.ok(fs.existsSync(p), `缺圖示檔 ${file}`);
    const { width, height } = pngSize(fs.readFileSync(p));
    assert.equal(width, size, `${file} 寬度應為 ${size}`);
    assert.equal(height, size, `${file} 高度應為 ${size}`);
  }
});

// ════════════════════════════════════════════════════════════
// Phase 2 — 訂閱 UI 三態（F-05a~d / F-10 / F-20 / F-26，§2.4 / §5.2）
// 對照：docs/test-plans/PWA測試計畫.md §4.2；docs/development/PWA.md §5.2
// ════════════════════════════════════════════════════════════

// ── F-05a~d subscriptionUI（BDD P2-A Outline ×4；三參數簽名 {vapidReady}）──
test('F-05a 權限 default＋無訂閱 → 「開啟票價提醒」（P2-A Outline row 1）', () => {
  const ui = Pwa.subscriptionUI('default', null, { vapidReady: true });
  assert.equal(ui.state, 'unsubscribed');
  assert.equal(ui.buttonLabel, '開啟票價提醒');
  assert.equal(ui.hint, '');
  assert.equal(ui.retryable, true);
});

test('F-05b 權限 granted＋無訂閱 → 「開啟票價提醒」（P2-A Outline row 2）', () => {
  const ui = Pwa.subscriptionUI('granted', null, { vapidReady: true });
  assert.equal(ui.state, 'unsubscribed');
  assert.equal(ui.buttonLabel, '開啟票價提醒');
});

test('F-05c 權限 granted＋已訂閱 → 「關閉票價提醒」＋狀態「已訂閱」（P2-A Outline row 3）', () => {
  const ui = Pwa.subscriptionUI('granted', { endpoint: 'https://p.example/s/1' }, { vapidReady: true });
  assert.equal(ui.state, 'subscribed');
  assert.equal(ui.buttonLabel, '關閉票價提醒');
  assert.equal(ui.hint, '已訂閱');
  assert.equal(ui.retryable, false);
});

test('F-05d 權限 denied → 拒絕引導，不彈權限詢問（P2-A Outline row 4 / E1）', () => {
  const ui = Pwa.subscriptionUI('denied', null, { vapidReady: true });
  assert.equal(ui.state, 'denied');
  assert.equal(ui.buttonLabel, '開啟票價提醒');
  assert.ok(ui.hint.includes('通知已封鎖'), ui.hint);
  assert.ok(ui.hint.includes('網站設定'), ui.hint);
});

test('F-20 訂閱狀態以 getSubscription 為準（E5 過期→未訂閱 / F-26 reload 還原）', () => {
  // E5：訂閱過期（getSubscription 空）→ 顯示未訂閱、可重新訂閱
  assert.equal(Pwa.subscriptionUI('granted', null, { vapidReady: true }).state, 'unsubscribed');
  assert.equal(Pwa.subscriptionUI('granted', null, { vapidReady: true }).buttonLabel, '開啟票價提醒');
  // F-26：reload 後 getSubscription 有效 → 還原「已訂閱」（不重複訂閱）
  assert.equal(Pwa.subscriptionUI('granted', { endpoint: 'https://p.example/s/1' }, { vapidReady: true }).state, 'subscribed');
  assert.equal(Pwa.subscriptionUI('default', null, { vapidReady: true }).state, 'unsubscribed');
});

test('F-10 E3：vapidReady=false → unavailable（僅未訂閱）；denied/已訂閱仍顯示本機真相（E1 優先）', () => {
  // 未訂閱 + 服務不可得 → unavailable（E2E-17 情境）
  const u = Pwa.subscriptionUI('default', null, { vapidReady: false });
  assert.equal(u.state, 'unavailable');
  assert.equal(u.hint, '提醒功能暫時不可用');
  // 本機真相優先：denied / 已訂閱不因 vapid 不可得而改變（離線三態 ①③）
  const d = Pwa.subscriptionUI('denied', null, { vapidReady: false });
  assert.equal(d.state, 'denied');
  assert.ok(d.hint.includes('封鎖'), d.hint);
  const s = Pwa.subscriptionUI('granted', { endpoint: 'https://p/x' }, { vapidReady: false });
  assert.equal(s.state, 'subscribed');
});

// ── F-10b fetchVapidPublicKey（E3：fetch → b64url 解析；失敗 → null）──
test('F-10b fetchVapidPublicKey：200+b64url → 字串；500/壞 shape/網路錯 → null（E3）', async () => {
  const okUrl = 'https://w.test/vapid-public-key';
  const key = 'BLf_d6fOp43_sPST91-wSmBFUKVKlXROfvikjwem7XMU37ngqedGaHHcAnignJ4MwmEhidqrxGL1DuWKbmgW7c8';
  assert.equal(
    await Pwa.fetchVapidPublicKey(okUrl, async () => new Response(JSON.stringify({ publicKey: key }), { status: 200, headers: { 'Content-Type': 'application/json' } })),
    key);
  assert.equal(await Pwa.fetchVapidPublicKey(okUrl, async () => new Response(null, { status: 500 })), null, '5xx → null');
  assert.equal(await Pwa.fetchVapidPublicKey(okUrl, async () => new Response(JSON.stringify({ foo: 1 }), { status: 200 })), null, '缺 publicKey → null');
  assert.equal(await Pwa.fetchVapidPublicKey(okUrl, async () => new Response(JSON.stringify({ publicKey: '' }), { status: 200 })), null, '空 publicKey → null');
  assert.equal(await Pwa.fetchVapidPublicKey(okUrl, async () => { throw new TypeError('network'); }), null, '網路錯 → null');
});

// ════════════════════════════════════════════════════════════
// Phase 2 — 訂閱／退訂流程（F-06~F-09 / F-11~F-13 / F-22 / F-23 / EC6，§2.4）
// deps 注入瀏覽器環境（Node 測試 mock；瀏覽器以全域預設，§2.4）
// ════════════════════════════════════════════════════════════

const IOS_UA_175 = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148';
const IOS_UA_163 = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) Mobile/15E148';

/** subscribeFlow / unsubscribeFlow deps 建構 helper（測試預設；over 覆寫） */
function mkFlowDeps(over = {}) {
  return {
    ua: '', standalone: false, userGesture: true, vapidKey: 'TEST-VAPID-KEY',
    getRegistration: async () => ({}),
    requestPermission: async () => 'granted',
    subscribe: async () => ({ endpoint: 'https://fcm.googleapis.com/fcm/send/u1', keys: { p256dh: 'x'.repeat(86), auth: 'y'.repeat(22) } }),
    postSubscribe: async () => ({ ok: true }),
    ...over,
  };
}

// ── F-06 僅 user gesture 觸發權限詢問（BDD P2-A / D5）──
test('F-06 無 user gesture → 拒絕執行、requestPermission 呼叫數 = 0', async () => {
  let req = 0;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    userGesture: false,
    requestPermission: async () => { req += 1; return 'granted'; },
  }));
  assert.equal(req, 0, '非 click handler 呼叫不得觸發 requestPermission');
  assert.equal(res.state, 'unsubscribed');
  assert.equal(res.hint, '');
});

// ── F-07 同意權限 → subscribe({userVisibleOnly, applicationServerKey}) → POST /subscribe → 已訂閱 ──
test('F-07 同意權限後訂閱成功：subscribe 帶公鑰、POST /subscribe 收到訂閱 body、狀態已訂閱（P2-A）', async () => {
  const calls = { req: 0, subscribeArgs: null, posted: null };
  const fakeSub = {
    endpoint: 'https://fcm.googleapis.com/fcm/send/u1',
    keys: { p256dh: 'x'.repeat(86), auth: 'y'.repeat(22) },
    expirationTime: null,
  };
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    vapidKey: 'VAPID-KEY-ABC',
    requestPermission: async () => { calls.req += 1; return 'granted'; },
    subscribe: async (reg, key) => { calls.subscribeArgs = { reg, key }; return fakeSub; },
    postSubscribe: async (body) => { calls.posted = body; return { ok: true }; },
  }));
  assert.equal(res.state, 'subscribed');
  assert.equal(res.hint, '已訂閱');
  assert.equal(calls.req, 1, 'requestPermission 恰一次');
  assert.equal(calls.subscribeArgs.key, 'VAPID-KEY-ABC', 'applicationServerKey = VAPID 公鑰');
  assert.deepEqual(calls.posted, {
    endpoint: fakeSub.endpoint, keys: fakeSub.keys, action: 'add',
  }, 'POST /subscribe body：{endpoint, keys, action:add}（免 token，T9 合約）');
});

test('F-07b POST /subscribe 含 expirationTime（非 null 時帶上）', async () => {
  let posted = null;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    subscribe: async () => ({
      endpoint: 'https://fcm.googleapis.com/fcm/send/u2',
      keys: { p256dh: 'x'.repeat(86), auth: 'y'.repeat(22) },
      expirationTime: 1234567890,
    }),
    postSubscribe: async (body) => { posted = body; return { ok: true }; },
  }));
  assert.equal(res.state, 'subscribed');
  assert.equal(posted.expirationTime, 1234567890);
});

// ── F-09 / F-23 訂閱失敗分支（E2：可重試；F-23：旗標無殘留）──
test('F-09 訂閱失敗 → 「訂閱失敗，請稍後重試」可重試（E2：PushManager.subscribe 拋錯 / POST 非 2xx）', async () => {
  // ① PushManager.subscribe 拋錯
  const r1 = await Pwa.subscribeFlow(mkFlowDeps({ subscribe: async () => { throw new Error('subscribe boom'); } }));
  assert.equal(r1.state, 'error');
  assert.equal(r1.hint, '訂閱失敗，請稍後重試');
  // ② POST /subscribe 非 2xx（mock 回 403/500）
  const r2 = await Pwa.subscribeFlow(mkFlowDeps({ postSubscribe: async () => ({ ok: false, status: 403 }) }));
  assert.equal(r2.state, 'error');
  assert.equal(r2.hint, '訂閱失敗，請稍後重試');
  // ③ POST /subscribe 網路拋錯
  const r3 = await Pwa.subscribeFlow(mkFlowDeps({ postSubscribe: async () => { throw new TypeError('net'); } }));
  assert.equal(r3.state, 'error');
});

test('F-23 流程失敗後防重入旗標清除：可再次執行訂閱（無殘留狀態）', async () => {
  const bad = await Pwa.subscribeFlow(mkFlowDeps({ subscribe: async () => { throw new Error('x'); } }));
  assert.equal(bad.state, 'error');
  const good = await Pwa.subscribeFlow(mkFlowDeps());
  assert.equal(good.state, 'subscribed', '失敗後重試應成功（F-22/F-23 語意：無殘留旗標）');
});

// ── F-11 / F-11b 權限 denied（E1：不重複 requestPermission、顯示拒絕引導）──
test('F-11 requestPermission 回 denied → 拒絕引導、不訂閱（E1）', async () => {
  let sub = 0;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    requestPermission: async () => 'denied',
    subscribe: async () => { sub += 1; throw new Error('不應訂閱'); },
  }));
  assert.equal(res.state, 'denied');
  assert.ok(res.hint.includes('通知已封鎖'), res.hint);
  assert.equal(sub, 0);
});

test('F-11b 已 denied 不重複 requestPermission（E1：shouldRequestPermission 守衛）', async () => {
  assert.equal(Pwa.shouldRequestPermission('denied'), false);
  assert.equal(Pwa.shouldRequestPermission('default'), true);
  assert.equal(Pwa.shouldRequestPermission('granted'), true);
  // 流程中 permission 已 denied → 直接拒絕引導，requestPermission 呼叫數 = 0
  let req = 0;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    permission: 'denied',
    requestPermission: async () => { req += 1; return 'denied'; },
  }));
  assert.equal(res.state, 'denied');
  assert.equal(req, 0, 'denied 時不得重複 requestPermission（E1）');
});

// ── F-12 權限詢問被忽略（E4：維持未訂閱、無錯誤）──
test('F-12 requestPermission 被忽略（default）→ 維持未訂閱、無錯誤提示（E4）', async () => {
  let sub = 0;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    requestPermission: async () => 'default',
    subscribe: async () => { sub += 1; throw new Error('不應訂閱'); },
  }));
  assert.equal(res.state, 'unsubscribed');
  assert.equal(res.hint, '');
  assert.equal(sub, 0);
});

// ── F-13 / EC6 iOS 分支（E8：未加到主畫面 → 提示且不發權限請求；EC6：<16.4 限制）──
test('F-13 iOS 非 standalone → 「需加到主畫面後才收得到通知」，requestPermission 呼叫數 = 0（E8）', async () => {
  let req = 0;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    ua: IOS_UA_175, standalone: false,
    requestPermission: async () => { req += 1; return 'granted'; },
  }));
  assert.equal(res.state, 'ios-required');
  assert.ok(res.hint.includes('加到主畫面'), res.hint);
  assert.equal(req, 0, 'E8：不發權限請求');
});

test('F-13b iOS standalone 且 ≥16.4 → 正常訂閱流程（P2-D）', async () => {
  const res = await Pwa.subscribeFlow(mkFlowDeps({ ua: IOS_UA_175, standalone: true }));
  assert.equal(res.state, 'subscribed');
});

test('F-21b iOS standalone 但 <16.4 → 限制提示、不發權限請求（EC6）', async () => {
  let req = 0;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    ua: IOS_UA_163, standalone: true,
    requestPermission: async () => { req += 1; return 'granted'; },
  }));
  assert.equal(res.state, 'ios-unsupported');
  assert.ok(res.hint.includes('16.4'), res.hint);
  assert.equal(req, 0);
});

// ── F-10c E3 於流程中：公鑰抓取失敗 → unavailable ──
test('F-10c 訂閱流程公鑰不可得 → unavailable「提醒功能暫時不可用」（E3）', async () => {
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    vapidKey: null,
    fetchImpl: async () => ({ ok: false }),   // mock 公鑰抓取失敗（不連外網；部署後 CONFIG 為正式網域）
    subscribe: async () => { throw new Error('不應訂閱'); },
  }));
  assert.equal(res.state, 'unavailable');
  assert.equal(res.hint, '提醒功能暫時不可用');
});

// ── F-08 退訂（P2-C：本機 unsubscribe + POST /subscribe remove）──
test('F-08 退訂：本機 unsubscribe() 移除 + POST /subscribe {endpoint, action:remove} → 未訂閱（P2-C）', async () => {
  let unsubbed = 0, posted = null;
  const fakeSub = {
    endpoint: 'https://fcm.googleapis.com/fcm/send/u1',
    unsubscribe: async () => { unsubbed += 1; return true; },
  };
  const res = await Pwa.unsubscribeFlow({
    getRegistration: async () => ({ pushManager: { getSubscription: async () => fakeSub } }),
    postSubscribe: async (body) => { posted = body; return { ok: true }; },
  });
  assert.equal(res.state, 'unsubscribed');
  assert.equal(unsubbed, 1, '本機 PushSubscription 已移除');
  assert.deepEqual(posted, { endpoint: fakeSub.endpoint, action: 'remove' }, 'Worker KV 刪除請求（免 token）');
});

test('F-08b 退訂：無本機訂閱 → 不發 remove、回未訂閱（冪等）', async () => {
  let posted = 0;
  const res = await Pwa.unsubscribeFlow({
    getRegistration: async () => ({ pushManager: { getSubscription: async () => null } }),
    postSubscribe: async () => { posted += 1; return { ok: true }; },
  });
  assert.equal(res.state, 'unsubscribed');
  assert.equal(posted, 0);
});

// ── F-22 快速連點：流程中重複呼叫被忽略（防重入旗標）──
test('F-22 快速連點「開啟票價提醒」→ 單次訂閱流程（防重入，F-22）', async () => {
  let resolvePerm;
  const gate = new Promise(r => { resolvePerm = r; });
  let req = 0;
  const p1 = Pwa.subscribeFlow(mkFlowDeps({
    requestPermission: () => { req += 1; return gate; },   // 掛起直到測試放行
  }));
  // 流程進行中 → 第二次呼叫立即回 busy，不觸發 requestPermission
  const r2 = await Pwa.subscribeFlow(mkFlowDeps({
    requestPermission: async () => { req += 1; return 'granted'; },
  }));
  assert.equal(r2.state, 'busy');
  assert.equal(req, 1, '只有第一次點擊觸發 requestPermission');
  resolvePerm('granted');
  const r1 = await p1;
  assert.equal(r1.state, 'subscribed');
  assert.equal(req, 1);
  // 完成後可再次執行
  const r3 = await Pwa.subscribeFlow(mkFlowDeps());
  assert.equal(r3.state, 'subscribed');
});

// ════════════════════════════════════════════════════════════
// Phase 2 — notificationclick 分頁決策純函式（F-14b / E10 / EC8）
// ════════════════════════════════════════════════════════════

test('F-14b findNotificationTarget：同 origin 既有分頁 → 回傳該 client；否則 null（E10/EC8）', () => {
  const clients = [
    { url: 'https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/' },
    { url: 'https://other.example/page' },
  ];
  // 同 origin（deep-link 目標）→ 聚焦既有分頁，不開新窗（E10）
  assert.equal(
    Pwa.findNotificationTarget(clients, 'https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/?route=TPE-NRT'),
    clients[0]);
  // 無分頁 → null → openWindow（P2-B）
  assert.equal(Pwa.findNotificationTarget([], 'https://x.test/a'), null);
  // 全部分頁皆不同 origin → null → 開新窗
  assert.equal(Pwa.findNotificationTarget(clients, 'https://x.test/a'), null);
  // 防呆：null/undefined clients
  assert.equal(Pwa.findNotificationTarget(null, 'https://x.test/a'), null);
});

// ════════════════════════════════════════════════════════════
// Phase 2 — sw.js push / notificationclick / notificationclose（F-15 / §2.7）
// ════════════════════════════════════════════════════════════

test('F-15 sw.js 含 push/notificationclick/notificationclose 三 handler（§2.7）', () => {
  const sw = fs.readFileSync(SW_PATH, 'utf8');
  assert.match(sw, /addEventListener\('push'/);
  assert.match(sw, /addEventListener\('notificationclick'/);
  assert.match(sw, /addEventListener\('notificationclose'/);
  // push handler：e.data.json() 讀承載 → showNotification（title/body/data.url/badge，§5.4）
  assert.match(sw, /e\.data\.json/);
  assert.match(sw, /showNotification/);
  assert.match(sw, /notification\.close\(\)/, 'notificationclick 先關閉通知');
  // notificationclick 以 registration.scope 拼接 data.url（F-14 / EC3）
  assert.match(sw, /registration\.scope/);
});

test('F-15b sw.js notificationclose 無副作用：不開頁、不 focus、不導覽（E13）', () => {
  const sw = fs.readFileSync(SW_PATH, 'utf8');
  const m = sw.match(/addEventListener\(\s*'notificationclose'[\s\S]*?=>\s*\{([\s\S]*?)\}\s*\);/);
  assert.ok(m, 'notificationclose handler 存在');
  assert.ok(!/openWindow|\.focus\(|\.navigate\(/.test(m[1]), '滑掉通知不得開啟/聚焦/導覽（E13）');
});

// ════════════════════════════════════════════════════════════
// Phase 2 — 降級與憑證分層（F-24 / F-25，E14 / BR11）
// ════════════════════════════════════════════════════════════

test('F-24 訂閱區 DOM 預設 hidden；initPwaPush 具備 secure context 守衛（E14）', () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  assert.match(html, /id="subBtn"[^>]*hidden/);
  assert.match(html, /id="subStatus"[^>]*hidden/);
  assert.match(html, /id="pwaActions"/);
  const app = fs.readFileSync(path.join(ROOT, 'web', 'app.js'), 'utf8');
  assert.match(app, /'serviceWorker' in navigator/, '非 secure context → 訂閱區不初始化');
  assert.match(app, /'PushManager' in window/);
  assert.match(app, /location\.protocol/);
  // initPwaPush 不得在載入時彈權限詢問（D5 / F-06）
  assert.ok(!/requestPermission\(\)/.test(app.replace(/initPwaPush[\s\S]*/, '')), 'init 載入流程不得呼叫 requestPermission');
});

test('F-25 前端不含 VAPID 私鑰／secret（BR11 憑證分層）', () => {
  for (const f of ['web/app.js', 'web/pwa.js', 'web/sw.js']) {
    const src = fs.readFileSync(path.join(ROOT, f), 'utf8');
    assert.ok(!src.includes('VAPID_PRIVATE_KEY'), f + ' 不得含 VAPID_PRIVATE_KEY（secret 名）');
    assert.ok(!src.includes('privateKeyJwk'), f + ' 不得含私鑰 JWK');
  }
});


// ── E2 錯誤分類（subscribe 拋錯依 name 給可操作提示）──
test('E2 分類：subscribe NotAllowedError → iOS 未安裝提示；AbortError → 服務連線提示；其他 → 通用（F-09 相容）', async () => {
  const n = await Pwa.subscribeFlow(mkFlowDeps({ subscribe: async () => { throw new DOMException('x', 'NotAllowedError'); } }));
  assert.equal(n.state, 'error');
  assert.ok(n.hint.includes('加到主畫面'), n.hint);
  const a = await Pwa.subscribeFlow(mkFlowDeps({ subscribe: async () => { throw new DOMException('x', 'AbortError'); } }));
  assert.equal(a.state, 'error');
  assert.ok(a.hint.includes('通知服務連線失敗'), a.hint);
  const g = await Pwa.subscribeFlow(mkFlowDeps({ subscribe: async () => { throw new Error('boom'); } }));
  assert.equal(g.state, 'error');
  assert.equal(g.hint, '訂閱失敗，請稍後重試');
});

// ── b64urlToBytes（applicationServerKey 轉 Uint8Array，Safari 相容）──
test('b64urlToBytes：87-char raw point → 65 bytes（0x04 開頭）', () => {
  const key = 'BLR37tYlifEmx-pybUsAHHnAbg4vtkuZGK-951g-vvGQdVnaYQEFSPRYnvBpQsBO7KqlEqt_-DxKkrihXxKCifE';
  const bytes = Pwa.b64urlToBytes(key);
  assert.equal(bytes.length, 65);
  assert.equal(bytes[0], 4);
});

// ── iOS 26 特例：subscription.keys getter 空 → toJSON() fallback（F-07 相容）──
test('iOS 26 keys fallback：sub.keys 空但 toJSON() 有 keys → 訂閱成功且 body 帶 toJSON keys', async () => {
  const fakeSub = {
    endpoint: 'https://web.push.apple.com/ios-26-token',
    keys: undefined,                       // iOS 26：keys getter 為空
    toJSON: () => ({ endpoint: 'https://web.push.apple.com/ios-26-token', keys: { p256dh: 'B' + 'A'.repeat(86), auth: 'B'.repeat(22) } }),
  };
  let posted = null;
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    subscribe: async () => fakeSub,
    postSubscribe: async (body) => { posted = body; return { ok: true }; },
  }));
  assert.equal(res.state, 'subscribed');
  assert.ok(posted.keys && posted.keys.p256dh, 'body 應帶 toJSON 的 keys');
});

test('keys 完全缺失（toJSON 也無）→ 金鑰不完整提示', async () => {
  const res = await Pwa.subscribeFlow(mkFlowDeps({
    subscribe: async () => ({ endpoint: 'https://p.example/s/1', keys: undefined, toJSON: () => ({ endpoint: 'https://p.example/s/1' }) }),
  }));
  assert.equal(res.state, 'error');
  assert.ok(res.hint.includes('訂閱金鑰不完整'), res.hint);
});
