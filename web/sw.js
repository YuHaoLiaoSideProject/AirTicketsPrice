/* 離線功能 — app shell Service Worker（方案 C：只兜頁面殼，不攔 api/）
   對照：docs/tech-decisions/離線功能-2026-08-15.md D1；docs/development/離線功能.md §2.4 / §9
   任務：T6（離線功能開發規格）+ T9（PWA Phase 2：push / notificationclick / notificationclose） */
'use strict';

// T9：載入 pwa.js（deep-link 拼接 / 分頁決策純函式單一來源，§2.7）；pwa.js 在 SW scope 內且已 precache
importScripts('pwa.js');

const CACHE = 'airtickets-shell-v3';          // 版本化；部署 bump → activate 清舊 cache（§5.1）；Phase 1 v1→v2、Phase 2 v2→v3（push handler）
const SHELL = [                               // 僅 app shell 檔（D1：不攔 api/）
  'index.html',
  'styles.css',
  'app.js',
  'aggregate.js',
  'cache.js',
  'pwa.js',                                   // T3：pwa.js 進 shell（離線 reload 不可缺，§2.1）
  'sw.js',
];

/* install：precache shell；任一個失敗 → 整體失敗（install 不完成，下次再試） */
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

/* activate：刪除非目前版本 cache（避免舊 shell 殘留） */
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

/* fetch：只攔「同源 + 命中 shell 清單」的 GET →
 *   cache-first 回傳 + 背景 revalidate 更新快取（SWR，不回推頁面——資料更新由 app.js 的 IDB 層負責）
 *   navigation 請求（如 /web/ 或 /web/index.html）→ cache-first：先比對 request，miss 再比對
 *   'index.html'（目錄導覽共用同一份 shell）；仍 miss → fallback network。
 *   其餘（含 api/** 與跨源）→ 直接 return，絕不攔截。
 */
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  const base = new URL('./', self.registration.scope).pathname;   // …/web/
  const isShell = SHELL.some(p => u.pathname === base + p);
  const isNav = e.request.mode === 'navigate';
  if (!isShell && !isNav) return;               // 非 shell、非導覽 → 放行（含 api/**，結構上也在 scope 外）
  e.respondWith(
    caches.match(e.request).then(cached =>
      cached || (isNav ? caches.match(base + 'index.html') : undefined)
    ).then(fallback => {
      const net = fetch(e.request).then(res => {
        if (res && res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      }).catch(() => fallback);
      return fallback || net;                   // cache-first；快取 miss → 網路，網路失敗 → 快取
    })
  );
});

/* ═══ Phase 2（T9 / §2.7）：push / notificationclick / notificationclose ═══ */

/* push：Worker 已格式化 payload { title, body, data: { url } }（§3.2）；
 * 無 payload／解析失敗 → fallback 通知（§5.4）。信任 Worker 格式化結果，僅做欄位防禦。 */
self.addEventListener('push', e => {
  let payload = null;
  try { payload = e.data && e.data.json(); } catch (err) { payload = null; }
  const p = payload || { title: '✈️ 票價下降了！', body: '有票價更新', data: { url: './' } };
  e.waitUntil(self.registration.showNotification(p.title || '票價趨勢', {
    body: p.body || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/icon-192.png',
    data: p.data || {},
  }));
});

/* notificationclick：close → 以 registration.scope 為基準拼接相對 data.url（F-14 / EC3 子路徑部署）
 * → 既有同 origin 分頁 focus + navigate（E10，不重開分頁）；無分頁 → openWindow（P2-B / E2E-10）。 */
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const dataUrl = (e.notification.data && e.notification.data.url) || './';
  const url = new URL(dataUrl, self.registration.scope);
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const target = (typeof Pwa !== 'undefined' && Pwa.findNotificationTarget)
      ? Pwa.findNotificationTarget(list, url.href)
      : list.find(c => new URL(c.url).origin === url.origin) || null;
    if (target) {
      target.focus();
      if (typeof target.navigate === 'function') target.navigate(url.href).catch(() => {});
      return;
    }
    return clients.openWindow(url.href);
  }));
});

/* notificationclose：滑掉通知 → 無任何動作（E13 / F-15；不開頁、不 focus、不導覽） */
self.addEventListener('notificationclose', e => {
  // 有意為空：關閉通知不觸發任何後續行為（EC8 多則通知各自獨立）
});
