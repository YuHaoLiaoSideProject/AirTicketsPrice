/* 離線功能 — app shell Service Worker（方案 C：只兜頁面殼，不攔 api/）
   對照：docs/tech-decisions/離線功能-2026-08-15.md D1；docs/development/離線功能.md §2.4 / §9
   任務：T6（離線功能開發規格） */
'use strict';

const CACHE = 'airtickets-shell-v1';          // 版本化；部署 bump → activate 清舊 cache（§5.1）
const SHELL = [                               // 僅 app shell 檔（D1：不攔 api/）
  'index.html',
  'styles.css',
  'app.js',
  'aggregate.js',
  'cache.js',
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
