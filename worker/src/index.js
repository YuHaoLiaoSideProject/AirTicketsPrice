// worker/src/index.js — 星宇票價趨勢推播服務（Cloudflare Worker，Phase 2 / T8）
// ---------------------------------------------------------------------------------------------
// 對照：docs/tech-decisions/PWA-2026-08-15.md（D2/D3/D4/D6）；docs/development/PWA.md §1.4 / §3 / §9.1
// 加密層：worker/src/vapid.mjs（Spike S1 移植，純 Web Crypto，零依賴零 build）
// 測試：worker/src/index.test.js（HDL-01~11；KV / push service 以 mock 注入，執行 node --test）
// 部署：worker/wrangler.toml + worker/README.md（§9.1 步驟；本檔不部署、不碰 web/）
//
// 端點合約（§3.1，T9 任務版）：
//   GET  /vapid-public-key  → 200 { publicKey }（無需驗證）
//   POST /subscribe         → 免 Bearer token（T9：前端瀏覽器無法持有 secret token）；
//                             ① Origin 白名單（github.io 頁面 + 本機測試 origin；其他 → 403 origin not allowed）
//                             ② 格式嚴格驗證（isValidSubscription：endpoint https、keys 合法 base64url）
//                             ③ MAX_SUBS 上限（達上限 → 400）；{endpoint, keys, action?}；
//                             有效 add → 200 寫入 KV sub:{endpoint}；action=remove → 200 刪除；無效 → 400
//   POST /notify            → Bearer PUSH_API_TOKEN（爬蟲持有）；{drops:[...]}（最多 3 條，Worker 防禦性 slice）；
//                             成功 → 200 {ok,sent,failed:0}；drops 缺失/空且無自訂訊息 → 400 drops required；token 錯 → 401；
//                             404/410 訂閱自動清理；5xx/網路失敗 → 500 {ok:false,failed}（不誤報成功）
//   POST /notify（自訂訊息） → 同端點；body 另可帶 {title?, body?, url?}（任一非空字串 → 自訂模式）：
//                             推送標題=title（缺省「✈️ 票價下降了！」）、內容=body、點擊跳轉=url（相對 SW scope，預設 './'）
//                             ——手動測試/公告用；爬蟲仍送 drops 格式不受影響。
// ---------------------------------------------------------------------------------------------
import { buildRequest, signVapidJwt, base64urlToBytes } from './vapid.mjs';

/* ── 常數（§1.4 / D4 / D6）── */
export const ROUTE_NAMES = { 'TPE-NRT': '東京', 'TPE-KIX': '大阪', 'TPE-FUK': '福岡', 'TPE-CTS': '札幌' };
export const MAX_DROPS = 3;        // D4：摘要最多 3 條（爬蟲已選 top-3，Worker 防禦性再 slice）
export const SUB_PREFIX = 'sub:';  // KV key = sub:{endpoint}（HDL-10：以 endpoint 為裝置單位）
export const MAX_SUBS = 1000;      // D6：KV 防陌生人灌爆上限（超出 → 400 拒絕寫入）
const VAPID_TTL = 86400;           // push TTL 秒（Spike §3.4：FCM 上限 2419200；Mozilla 必帶）
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
};

export default {
  /** 三端點 router（依 pathname + method 分流；CORS preflight 204） */
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
    if (request.method === 'GET' && url.pathname === '/vapid-public-key') return vapidPublicKey(env);
    if (request.method === 'POST' && url.pathname === '/subscribe') return subscribe(request, env);
    if (request.method === 'POST' && url.pathname === '/notify') return notify(request, env);
    return new Response('Not Found', { status: 404, headers: CORS });
  },
};

/** GET /vapid-public-key：回傳 VAPID 公鑰（base64url raw point，前端 PushManager.subscribe 可解析）。
 *  無需任何驗證；回應僅含公鑰，絕不含私鑰（HDL-01 / BR 憑證分層）。 */
export async function vapidPublicKey(env) {
  return json({ publicKey: env.VAPID_PUBLIC_KEY });
}

/** POST /subscribe（訂閱／退訂雙用途，維持「三端點」路徑面；T9 免 token 調整）：
 *  ① **免 Bearer token**（前端瀏覽器無法持有 secret token）→ 改以 Origin 白名單防護：
 *     `https://yuhaoliaosideproject.github.io` 與本機測試 origin（http(s)://127.0.0.1|localhost[:port]）
 *     允許；其他 → 403（不寫入 KV）；無 Origin header（非瀏覽器 client）→ 允許（CSRF 語意只擋瀏覽器跨源）
 *  ② body 為 PushSubscription JSON → isValidSubscription 嚴格驗證（endpoint https、keys base64url）→
 *     KV put sub:{endpoint}（重複 endpoint 覆寫，HDL-10）→ 200
 *  ③ 退訂：body = {endpoint, action:'remove'} → KV delete → 200（冪等）
 *  無效資料 → 400 且不寫入 KV（HDL-03）；達 MAX_SUBS → 400（D6 防灌爆）。 */
export async function subscribe(request, env) {
  if (!isOriginAllowed(request, env)) return json({ error: 'origin not allowed' }, 403);
  const body = await request.json().catch(() => null);
  if (body && body.action === 'remove') {
    if (typeof body.endpoint === 'string') await env.SUBS.delete(SUB_PREFIX + body.endpoint);
    return json({ ok: true });
  }
  const subReason = isValidSubscription(body);
  if (subReason) return json({ error: `invalid subscription: ${subReason}` }, 400);
  try {
    if ((await countSubs(env.SUBS)) >= MAX_SUBS) return json({ error: 'subscription limit reached' }, 400);
    await env.SUBS.put(SUB_PREFIX + body.endpoint, JSON.stringify(body));
  } catch {
    return json({ ok: false, error: 'kv unavailable' }, 500);
  }
  return json({ ok: true });
}

/** POST /notify：驗證 Bearer token（D6）→ drops 驗證 → 讀全部訂閱 → VAPID Web Push 廣播。
 *  - token 無效 / 缺 → 401，不發送、不寫入（HDL-05 / E6）
 *  - drops 缺失/空 → 400（任務合約；爬蟲端正常流程不會送空）
 *  - 無訂閱者 → 200 空廣播（HDL-06 / E7）
 *  - 單筆 push 回 404/410 → 刪除該訂閱（E5 / HDL-07）；其他失敗（5xx/網路）→ 記 failed（HDL-11，不誤報成功） */
export async function notify(request, env) {
  if (!isAuthorized(request, env.PUSH_API_TOKEN)) return json({ error: 'unauthorized' }, 401);
  const body = await request.json().catch(() => null);
  const drops = body && body.drops;
  // 自訂訊息模式：title/body 任一為非空字串 → 不需 drops（手動測試/公告；爬蟲仍送 drops 格式）
  const hasCustom = !!(body && (typeof body.title === 'string' || typeof body.body === 'string'));
  if ((!Array.isArray(drops) || drops.length === 0) && !hasCustom) {
    return json({ error: 'drops required' }, 400);
  }
  const payload = formatNotification(drops, body); // §3.2 承載；自訂模式優先，否則最多 3 行（MAX_DROPS 防禦性 slice）

  let names;
  try { names = await listSubs(env.SUBS); } catch { return json({ ok: false, error: 'kv unavailable' }, 500); }
  if (names.length === 0) return json({ ok: true, sent: 0, failed: 0 }); // E7 空廣播

  const failed = [];
  for (const name of names) {
    let sub;
    try { sub = JSON.parse(await env.SUBS.get(name)); } catch { failed.push(name); continue; }
    let res;
    try { res = await sendPush(sub, payload, env); } catch { failed.push(name); continue; } // 網路失敗
    if (res.status === 404 || res.status === 410) {
      await env.SUBS.delete(name).catch(() => {}); // E5 失效訂閱清理
    } else if (!res.ok) {
      failed.push(name); // 401/403/429/5xx → 記失敗，保留訂閱（HDL-11）
    }
  }
  const sent = names.length - failed.length;
  // 部分成功也算成功（至少 1 人收到 → 200；全部失敗 → 500）
  return sent === 0 && failed.length > 0
    ? json({ ok: false, sent: 0, failed: failed.length }, 500)
    : json({ ok: true, sent, failed: failed.length });
}

/* ── 純函式（worker/src/index.test.js 直接測試；與 pwa.js formatNotification 同簽名合約）── */

/** Bearer token 驗證（D6；常數時間比較，Spike §4 建議）——僅 /notify 使用（爬蟲持有） */
export function isAuthorized(request, expected) {
  const m = /^Bearer\s+(.+)$/i.exec(request.headers.get('Authorization') || '');
  if (!m) return false;
  return timingSafeEqual(m[1], expected || '');
}

/**
 * /subscribe 的 Origin 白名單（T9：免 token 的替代防護，D6）：
 *  - 允許：頁面 origin `https://yuhaoliaosideproject.github.io`、本機測試 origin
 *    （`http(s)://127.0.0.1[:port]` / `http(s)://localhost[:port]`）、env.ALLOWED_ORIGINS（逗號分隔，可擴充）
 *  - 其他 → false（403）；無 Origin header（非瀏覽器 client）→ true（CSRF 語意：只擋瀏覽器跨源）
 */
export function isOriginAllowed(request, env) {
  const origin = request.headers.get('Origin');
  if (!origin) return true;
  const extra = (env && env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
  if (extra.includes(origin)) return true;
  if (origin === 'https://yuhaoliaosideproject.github.io') return true;
  if (/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(origin)) return true;
  return false;
}

/** 常數時間字串比較（長度洩漏可接受；內容比較恆定時間） */
export function timingSafeEqual(a, b) {
  const ea = new TextEncoder().encode(a || '');
  const eb = new TextEncoder().encode(b || '');
  if (ea.length !== eb.length) return false;
  let diff = 0;
  for (let i = 0; i < ea.length; i++) diff |= ea[i] ^ eb[i];
  return diff === 0;
}

/** 訂閱資料驗證（HDL-02/03）：endpoint 為 https URL；keys.p256dh 為 65B 未壓縮點（0x04 開頭）；
 *  keys.auth ≥16B（Spike 陷阱：不符則 notify 時 encryptPayload 必失敗，故訂閱時即拒絕）。
 *  回傳 null = 合法；字串 = 具體失敗原因（前端顯示，診斷 iOS/各瀏覽器格式差異）。 */
export function isValidSubscription(sub) {
  if (!sub || typeof sub !== 'object' || typeof sub.endpoint !== 'string') return 'endpoint missing';
  try { if (new URL(sub.endpoint).protocol !== 'https:') return 'endpoint not https'; } catch { return 'endpoint invalid url'; }
  const keys = sub.keys;
  if (!keys || typeof keys !== 'object') return 'keys missing';
  if (typeof keys.p256dh !== 'string' || keys.p256dh === '') return 'p256dh missing';
  if (typeof keys.auth !== 'string' || keys.auth === '') return 'auth missing';
  let p256dh;
  try { p256dh = base64urlToBytes(keys.p256dh); } catch { return 'p256dh bad base64'; }
  if (p256dh.length !== 65 || p256dh[0] !== 4) return `p256dh format(len=${p256dh.length},head=0x${p256dh[0]?.toString(16)})`;
  let auth;
  try { auth = base64urlToBytes(keys.auth); } catch { return 'auth bad base64'; }
  if (auth.length < 16) return `auth too short(${auth.length})`;
  return null;
}

/** 通知承載格式化（HDL-08 / F-19a/b 同簽名合約）：
 *  title = '✈️ 票價下降了！'；body 為 drops 明細（見下）；data.url = '?route=' + 首筆 route（SW 以 scope 拼接）。
 *  第二參數 custom（選用）＝自訂訊息模式：
 *    - custom.title 非空字串 → 取代 title；custom.body 非空字串 → 取代 body 並以 custom.url（相對 SW scope，預設 './'）為 data.url
 *    - 至少 title/body 任一非空才視為自訂（notify handler 已擋掉兩者皆無且 drops 空的請求）
 *  drops 為空／非陣列且無自訂 → title 不變、body = '有票價更新'、data.url = './'（§5.4 fallback）
 *  實作與 web/pwa.js formatNotification 逐字對齊（同一份合約兩份實作，§3.2 語意對照）。 */
export function formatNotification(drops, custom) {
  const list = Array.isArray(drops) ? drops.slice(0, MAX_DROPS) : [];
  const customTitle = custom && typeof custom.title === 'string' && custom.title;
  const customBody = custom && typeof custom.body === 'string' && custom.body;
  const title = customTitle || '✈️ 票價下降了！';
  if (customTitle || customBody) {
    return { title, body: customBody || title, data: { url: (custom && custom.url) || './' } };
  }
  if (list.length === 0) return { title, body: '有票價更新', data: { url: '?route=' } };
  const body = list.map((d) => {
    const name = ROUTE_NAMES[d.route] || d.route || '';
    return d.route + ' ' + name + ' ' + formatMonthDay(d.outbound_date) + '–' +
      formatMonthDay(d.return_date) + ' 降至 NT$' + formatPrice(d.new_price) +
      '（原 NT$' + formatPrice(d.old_price) + '）';
  }).join('\n');
  return { title, body, data: { url: '?route=' + (list[0].route || '') } };
}

/** sendPush：組 VAPID 頭（Authorization: vapid t=..,k=..）+ RFC 8291 加密 payload → fetch(endpoint)。
 *  回傳 fetch 的 Response；呼叫端依 status 分流 200/404/410/其他（§1.4 / Spike §3.4）。 */
export async function sendPush(subscription, payload, env) {
  const privateKeyJwk = JSON.parse(env.VAPID_PRIVATE_KEY || 'null');
  if (!privateKeyJwk) throw new Error('VAPID_PRIVATE_KEY 未設定（wrangler secret put）');
  // payload 為物件（formatNotification 產出）→ 序列化為 JSON 字串再加密（§3.2：SW 以 e.data.json() 讀取）
  const message = typeof payload === 'string' ? payload : JSON.stringify(payload);
  const req = await buildRequest(subscription, message, {
    privateKeyJwk,
    publicKeyBase64url: env.VAPID_PUBLIC_KEY,
    subject: env.VAPID_SUBJECT || 'mailto:admin@example.com',
    ttl: VAPID_TTL,
  });
  // 注意（Spike §3.4）：不手動設 Content-Length——CF Runtime 依 body 自動計算
  return fetch(req.url, { method: req.method, headers: req.headers, body: req.body });
}

/* ── 內部工具 ── */

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  });
}

/** KV list（分頁巡覽，cursor 迴圈；key = SUB_PREFIX 前綴） */
async function listSubs(kv) {
  const names = [];
  let cursor;
  do {
    const page = await kv.list({ prefix: SUB_PREFIX, ...(cursor ? { cursor } : {}) });
    for (const k of page.keys) names.push(k.name);
    cursor = page.cursor;
  } while (cursor);
  return names;
}

async function countSubs(kv) {
  return (await listSubs(kv)).length;
}

/** 月/日去前導零：'2026-08-22' → '8/22'（§3.2；無效日期 → '—'，與 pwa.js 一致） */
function formatMonthDay(iso) {
  if (!iso) return '—';
  const p = String(iso).split('-');
  if (p.length < 3) return '—';
  return parseInt(p[1], 10) + '/' + parseInt(p[2], 10);
}

/** 價格千分位：24120 → '24,120'（§3.2；非數值 → 原值字串，與 pwa.js 一致） */
function formatPrice(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return String(n == null ? '' : n);
  return n.toLocaleString('en-US');
}
