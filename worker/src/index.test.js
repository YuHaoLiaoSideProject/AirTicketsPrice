// worker/src/index.test.js — 推播服務單元測試（HDL-01~11，對照 docs/test-plans/PWA測試計畫.md §3）
// ---------------------------------------------------------------------------------------------
// 執行：node --test worker/src/index.test.js
// TDD：本檔先行（紅：worker/src/index.js 尚未存在）→ 實作 index.js → 綠。
// 對照：docs/development/PWA.md §1.4 / §3.1 / §3.2；docs/bdds/PWA.feature @business-rules Worker 契約
// 測試策略（Spike S1 §3.6）：KV 與 push service 以 mock 注入（globalThis.fetch stub）；
//   VAPID 用固定測試金鑰；payload 用 worker/src/vapid.mjs 的 decryptPayload 模擬接收端解回原文
//   （加密層合規性已有 spike V-4/V-5 背書，單元測試只驗「傳了正確 endpoint/headers/body」）。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* ── 動態載入 ESM Worker 模組（index.js 為 ESM；測試走 dynamic import，對照 PWA.md §1.4 骨架）── */
let workerMod = null;
async function loadWorker() {
  if (!workerMod) workerMod = await import('../src/index.js');
  return workerMod;
}

/* ── 固定測試 VAPID 金鑰（worker/spike/gen-vapid-keys.mjs 一次性產出，測試專用）── */
const VAPID_PUBLIC_KEY = 'BLf_d6fOp43_sPST91-wSmBFUKVKlXROfvikjwem7XMU37ngqedGaHHcAnignJ4MwmEhidqrxGL1DuWKbmgW7c8';
const VAPID_PRIVATE_JWK = {
  key_ops: ['sign'], ext: true, kty: 'EC', crv: 'P-256',
  x: 't_93p86njf-w9JP3X7BKYEVQpUqVdE5--KSPB6btcxQ',
  y: '37ngqedGaHHcAnignJ4MwmEhidqrxGL1DuWKbmgW7c8',
  d: 'ONukVfKGkZMj37c1fEnSFHpCcM9Dt15tKmbbQv-EH-A',
};
const VAPID_SUBJECT = 'mailto:t8-test@example.com';
const TOKEN = 't8-test-push-token';
const ORIGIN = 'https://airtickets-price-push.example.workers.dev';

/* ── 通知承載 fixture（PWA.md §3.2 / BDD 情境 46 row 1）── */
const DROPS = [
  { route: 'TPE-NRT', outbound_date: '2026-08-22', return_date: '2026-08-30', flight_no: 'JX 804', old_price: 26008, new_price: 24120 },
];
const EXPECTED_PAYLOAD = {
  title: '✈️ 票價下降了！',
  body: 'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）',
  data: { url: '?route=TPE-NRT' },
};

/* ── KV mock：in-memory Map 包裝 get/put/delete/list（對齊 cache.test.js mkAdapter 注入風格）── */
function mkKV(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    _map: map,
    async get(key) { return map.has(key) ? map.get(key) : null; },
    async put(key, val) { map.set(key, String(val)); },
    async delete(key) { map.delete(key); },
    async list({ prefix = '' } = {}) {
      return { keys: [...map.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })) };
    },
  };
}

/** KV mock：全部方法拋錯（HDL-11「KV 不可用 → 500 不誤報成功」） */
function mkBrokenKV() {
  return {
    async get() { throw new Error('kv down'); },
    async put() { throw new Error('kv down'); },
    async delete() { throw new Error('kv down'); },
    async list() { throw new Error('kv down'); },
  };
}

/** globalThis.fetch stub：記錄 (url, method, headers, body) 並依 handler 回 Response（預設 200） */
function mkFetch(handler) {
  const calls = [];
  const orig = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const call = {
      url: String(url),
      method: init.method || 'POST',
      headers: init.headers || {},
      body: init.body instanceof Uint8Array ? new Uint8Array(init.body) : init.body,
    };
    calls.push(call);
    return handler ? await handler(call) : new Response(null, { status: 200 });
  };
  return { calls, restore() { globalThis.fetch = orig; } };
}

async function withFetch(handler, fn) {
  const stub = mkFetch(handler);
  try { return await fn(stub); } finally { stub.restore(); }
}

/* ── env / request 建構 ── */
function mkEnv(kv, over = {}) {
  return {
    SUBS: kv,
    PUSH_API_TOKEN: TOKEN,
    VAPID_PUBLIC_KEY,
    VAPID_PRIVATE_KEY: JSON.stringify(VAPID_PRIVATE_JWK),
    VAPID_SUBJECT,
    ...over,
  };
}

function mkReq(method, reqPath, { token, body, rawBody, origin } = {}) {
  const headers = {};
  if (token !== undefined) headers.Authorization = 'Bearer ' + token;
  if (origin !== undefined) headers.Origin = origin;
  let payload;
  if (rawBody !== undefined) payload = rawBody;
  else if (body !== undefined) payload = JSON.stringify(body);
  if (payload !== undefined) headers['Content-Type'] = 'application/json';
  return new Request(ORIGIN + reqPath, { method, headers, body: payload });
}

/** 模擬「使用者代理端」訂閱：p256dh ECDH 金鑰對 + auth secret + push service endpoint（同 verify.mjs makeFakeSubscription） */
async function mkFakeSub(endpoint = 'https://fcm.googleapis.com/fcm/send/t8-token-0001') {
  const kp = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
  const pub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  const privJwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
  const auth = crypto.getRandomValues(new Uint8Array(16));
  return {
    endpoint,
    expirationTime: null,
    keys: { p256dh: await b64(pub), auth: await b64(auth) },
    privJwk, // 測試端解密的收端私鑰
  };
}

/* ── 加密層工具（測試端模擬接收端解密用；與 index.js 共用 worker/src/vapid.mjs）── */
let vapidApi = null;
async function loadVapid() {
  if (!vapidApi) vapidApi = await import('../src/vapid.mjs');
  return vapidApi;
}
async function b64(bytes) { return (await loadVapid()).bytesToBase64url(bytes); }
async function b64d(str) { return (await loadVapid()).base64urlToBytes(str); }

/* ════════════════════════════════════════════════════════════════════════
 * HDL-01 / HDL-09  GET /vapid-public-key（公鑰公開；憑證分層）
 * ════════════════════════════════════════════════════════════════════════ */
test('HDL-01 GET /vapid-public-key 回傳公鑰（200、無需驗證、前端 PushManager 可解析）', async () => {
  const mod = await loadWorker();
  const res = await mod.default.fetch(mkReq('GET', '/vapid-public-key'), mkEnv(mkKV()));
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.publicKey, VAPID_PUBLIC_KEY);
  const raw = await b64d(body.publicKey);
  assert.equal(raw.length, 65, '公鑰為 65B raw point（applicationServerKey 可解析）');
  assert.equal(raw[0], 4, '0x04 開頭（未壓縮點）');
});

test('HDL-09 憑證分層：回應僅公鑰（無私鑰）；私鑰自 env secret 讀取；原始碼無硬編碼私鑰', async () => {
  const mod = await loadWorker();
  const res = await mod.default.fetch(mkReq('GET', '/vapid-public-key'), mkEnv(mkKV()));
  const text = await res.text();
  assert.ok(!text.includes(VAPID_PRIVATE_JWK.d), '回應不得含私鑰');
  assert.ok(!text.includes('VAPID_PRIVATE_KEY'), '回應不得含私鑰變數名');
  const src = fs.readFileSync(path.join(__dirname, 'index.js'), 'utf8');
  assert.ok(src.includes('env.VAPID_PRIVATE_KEY'), '私鑰須自 env.VAPID_PRIVATE_KEY 讀取（secret，不入 repo）');
  assert.ok(!src.includes(VAPID_PRIVATE_JWK.d), '原始碼不得硬編碼私鑰');
});

test('HDL-01b CORS：OPTIONS preflight 204 + 回應帶 Access-Control-Allow-Origin:*；未知路徑 404', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const opt = await mod.default.fetch(new Request(ORIGIN + '/notify', { method: 'OPTIONS' }), mkEnv(kv));
  assert.equal(opt.status, 204);
  assert.equal(opt.headers.get('Access-Control-Allow-Origin'), '*');
  const res = await mod.default.fetch(mkReq('GET', '/vapid-public-key'), mkEnv(kv));
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), '*');
  const nf = await mod.default.fetch(mkReq('GET', '/nope'), mkEnv(kv));
  assert.equal(nf.status, 404);
});

/* ════════════════════════════════════════════════════════════════════════
 * HDL-02 / HDL-03 / HDL-10  POST /subscribe（T9 免 token：Origin 白名單 → 驗證 → KV 寫入；退訂）
 * ════════════════════════════════════════════════════════════════════════ */
test('HDL-02 POST /subscribe 有效訂閱（免 token）→ 200 且 KV 寫入（sub:{endpoint}，僅此一筆）', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const sub = await mkFakeSub();
  await withFetch(async () => new Response(null, { status: 200 }), async () => {
    const res = await mod.default.fetch(mkReq('POST', '/subscribe', { body: sub }), mkEnv(kv));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true });
  });
  assert.deepEqual([...kv._map.keys()], ['sub:' + sub.endpoint], '僅寫入一筆，key = sub:{endpoint}');
  const stored = JSON.parse(kv._map.get('sub:' + sub.endpoint));
  assert.equal(stored.endpoint, sub.endpoint);
  assert.equal(stored.keys.p256dh, sub.keys.p256dh);
  assert.equal(stored.keys.auth, sub.keys.auth);
});

test('HDL-03 POST /subscribe 無效資料 → 400 invalid subscription，KV 零寫入', async () => {
  const mod = await loadWorker();
  const cases = [
    ['空物件', {}],
    ['缺 endpoint', { keys: { p256dh: 'x'.repeat(86), auth: 'y'.repeat(22) } }],
    ['endpoint 非 https', { endpoint: 'http://p.example/s/1', keys: { p256dh: 'x'.repeat(86), auth: 'y'.repeat(22) } }],
    ['endpoint 非 URL', { endpoint: 'not-a-url', keys: { p256dh: 'x'.repeat(86), auth: 'y'.repeat(22) } }],
    ['缺 keys', { endpoint: 'https://p.example/s/1' }],
    ['p256dh 非 65B 點', { endpoint: 'https://p.example/s/1', keys: { p256dh: 'aGVsbG8', auth: 'c2VjcmV0c2VjcmV0c2VjcmV0' } }],
    ['auth 太短', { endpoint: 'https://p.example/s/1', keys: { p256dh: 'x'.repeat(86), auth: 'c2VjcmV0' } }],
    ['keys 非合法 base64url', { endpoint: 'https://p.example/s/1', keys: { p256dh: '!!!not-b64!!!', auth: 'y'.repeat(22) } }],
    ['null', null],
  ];
  for (const [label, bad] of cases) {
    const kv = mkKV();
    const res = await mod.default.fetch(mkReq('POST', '/subscribe', { body: bad }), mkEnv(kv));
    assert.equal(res.status, 400, label);
    assert.ok((await res.json()).error.startsWith('invalid subscription: '), label);
    assert.equal(kv._map.size, 0, label + ' → 不得寫入 KV');
  }
  // 非 JSON body → 400
  const kv = mkKV();
  const res = await mod.default.fetch(mkReq('POST', '/subscribe', { rawBody: '{not json' }), mkEnv(kv));
  assert.equal(res.status, 400);
  assert.ok((await res.json()).error.startsWith('invalid subscription: '));
  assert.equal(kv._map.size, 0);
});

test('HDL-03b POST /subscribe 達 MAX_SUBS 上限 → 400 subscription limit reached（防灌爆 D6）', async () => {
  const mod = await loadWorker();
  const seed = {};
  for (let i = 0; i < mod.MAX_SUBS; i++) seed['sub:https://p.example/s/' + i] = '{}';
  const kv = mkKV(seed);
  const sub = await mkFakeSub();
  const res = await mod.default.fetch(mkReq('POST', '/subscribe', { body: sub }), mkEnv(kv));
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, 'subscription limit reached');
  assert.equal(kv._map.size, mod.MAX_SUBS, '達上限不寫入');
});

test('HDL-10 訂閱以裝置/瀏覽器為單位：同 endpoint 覆寫（單筆）、不同 endpoint 各自獨立', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const a = await mkFakeSub('https://fcm.googleapis.com/fcm/send/device-a');
  const a2 = { ...a }; // 同一裝置重新訂閱（同 endpoint）
  const b = await mkFakeSub('https://fcm.googleapis.com/fcm/send/device-b');
  await mod.default.fetch(mkReq('POST', '/subscribe', { body: a }), mkEnv(kv));
  await mod.default.fetch(mkReq('POST', '/subscribe', { body: a2 }), mkEnv(kv));
  await mod.default.fetch(mkReq('POST', '/subscribe', { body: b }), mkEnv(kv));
  assert.deepEqual(
    [...kv._map.keys()].sort(),
    ['sub:https://fcm.googleapis.com/fcm/send/device-a', 'sub:https://fcm.googleapis.com/fcm/send/device-b'].sort(),
    '同 endpoint 覆寫、不同 endpoint 各自獨立（無跨裝置同步）'
  );
});

test('HDL-10b POST /subscribe action=remove → 200 且 KV 刪除（冪等）', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const sub = await mkFakeSub();
  await mod.default.fetch(mkReq('POST', '/subscribe', { body: sub }), mkEnv(kv));
  assert.equal(kv._map.size, 1);
  const res = await mod.default.fetch(
    mkReq('POST', '/subscribe', { body: { endpoint: sub.endpoint, action: 'remove' } }), mkEnv(kv));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true });
  assert.equal(kv._map.size, 0, 'KV 記錄已刪除');
  // remove 不存在的 endpoint → 200（冪等，不報錯）
  const res2 = await mod.default.fetch(
    mkReq('POST', '/subscribe', { body: { endpoint: 'https://p.example/none', action: 'remove' } }), mkEnv(kv));
  assert.equal(res2.status, 200);
});

/* ════════════════════════════════════════════════════════════════════════
 * HDL-05  token 驗證：/notify 維持 Bearer（401）；/subscribe 免 token（T9，改以 Origin 白名單）
 * ════════════════════════════════════════════════════════════════════════ */
test('HDL-05 /notify 缺／錯 token → 401：不發送任何 Web Push（E6；/notify 維持 Bearer）', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  await withFetch(async () => new Response(null, { status: 200 }), async (f) => {
    let res = await mod.default.fetch(mkReq('POST', '/notify', { body: { drops: DROPS } }), mkEnv(kv));
    assert.equal(res.status, 401);
    assert.equal((await res.json()).error, 'unauthorized');
    res = await mod.default.fetch(mkReq('POST', '/notify', { token: 'wrong-token', body: { drops: DROPS } }), mkEnv(kv));
    assert.equal(res.status, 401);
    res = await mod.default.fetch(mkReq('POST', '/notify', { token: 'wrong-token', rawBody: '{bad' }), mkEnv(kv));
    assert.equal(res.status, 401);
    assert.equal(f.calls.length, 0, '401 不得觸發任何 Web Push');
    assert.equal(kv._map.size, 0, '401 不得寫入 KV');
  });
});

test('HDL-05b /subscribe 免 token：無 Origin／白名單 origin 皆 200（T9 合約）', async () => {
  const mod = await loadWorker();
  const sub = await mkFakeSub();
  const cases = [
    ['無 Origin header（非瀏覽器 client）', undefined],
    ['頁面 origin（github.io）', 'https://yuhaoliaosideproject.github.io'],
    ['本機測試 origin（127.0.0.1）', 'http://127.0.0.1:8000'],
    ['本機測試 origin（localhost）', 'http://localhost:8080'],
  ];
  for (const [label, origin] of cases) {
    const kv = mkKV();
    const res = await mod.default.fetch(mkReq('POST', '/subscribe', { body: sub, origin }), mkEnv(kv));
    assert.equal(res.status, 200, label);
    assert.equal(kv._map.size, 1, label + ' → 寫入 KV');
  }
  // remove 同受白名單保護（白名單 origin 正常刪除）
  const kv = mkKV({ ['sub:https://fcm.googleapis.com/fcm/send/u1']: '{}' });
  const res = await mod.default.fetch(
    mkReq('POST', '/subscribe', { body: { endpoint: 'https://fcm.googleapis.com/fcm/send/u1', action: 'remove' }, origin: 'https://yuhaoliaosideproject.github.io' }), mkEnv(kv));
  assert.equal(res.status, 200);
  assert.equal(kv._map.size, 0);
});

test('HDL-05c /subscribe 跨源（非白名單）→ 403 origin not allowed，KV 零寫入', async () => {
  const mod = await loadWorker();
  const sub = await mkFakeSub();
  const badOrigins = [
    'https://evil.example',
    'https://attacker.github.io',
    'https://yuhaoliaosideproject.github.io.evil.com',   // 前綴仿冒不得通過
    'http://192.168.1.10:8000',
    'file://',
  ];
  for (const origin of badOrigins) {
    const kv = mkKV();
    const res = await mod.default.fetch(mkReq('POST', '/subscribe', { body: sub, origin }), mkEnv(kv));
    assert.equal(res.status, 403, origin);
    assert.equal((await res.json()).error, 'origin not allowed', origin);
    assert.equal(kv._map.size, 0, origin + ' → 不得寫入 KV');
  }
  // remove 亦受 Origin 白名單保護（跨源退訂 → 403，不得刪除）
  const kv = mkKV({ ['sub:https://p.example/x']: '{}' });
  const res = await mod.default.fetch(
    mkReq('POST', '/subscribe', { body: { endpoint: 'https://p.example/x', action: 'remove' }, origin: 'https://evil.example' }), mkEnv(kv));
  assert.equal(res.status, 403);
  assert.equal(kv._map.size, 1, '跨源不得刪除 KV');
});

/* ════════════════════════════════════════════════════════════════════════
 * HDL-04 / HDL-06 / HDL-07 / HDL-11  POST /notify（廣播、空廣播、404/410 清理、5xx/網路失敗）
 * ════════════════════════════════════════════════════════════════════════ */
test('HDL-04 POST /notify 有效 token → 對全部訂閱者 Web Push（Authorization/Content-Encoding/payload 可解密）', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const subs = [];
  for (let i = 0; i < 3; i++) {
    const s = await mkFakeSub('https://fcm.googleapis.com/fcm/send/t8-broadcast-' + i);
    subs.push(s);
    await mod.default.fetch(mkReq('POST', '/subscribe', { body: s }), mkEnv(kv));
  }
  const payloadJson = JSON.stringify(EXPECTED_PAYLOAD);
  let pushCalls;
  await withFetch(async () => new Response(null, { status: 201 }), async (f) => {
    const res = await mod.default.fetch(mkReq('POST', '/notify', { token: TOKEN, body: { drops: DROPS } }), mkEnv(kv));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true, sent: 3, failed: 0 });
    assert.equal(f.calls.length, 3, '對 3 個訂閱者各發一次 Web Push');
    pushCalls = f.calls;
  });

  for (let i = 0; i < 3; i++) {
    const call = pushCalls[i];
    const sub = subs.find((s) => s.endpoint === call.url);
    assert.ok(sub, `push #${i} 送到對應 endpoint（${call.url}）`);
    // Authorization: vapid t=<JWT>,k=<65B raw point>（RFC 8292 / Spike V-1d）
    const m = /^vapid t=([^,]+),k=([A-Za-z0-9_-]+)$/.exec(call.headers.Authorization || '');
    assert.ok(m, 'Authorization 格式：vapid t=..,k=..');
    const kRaw = await b64d(m[2]);
    assert.equal(kRaw.length, 65);
    assert.equal(kRaw[0], 4);
    // JWT 可用固定公鑰驗證（證明以 env secret 私鑰簽署；aud = endpoint origin）
    const vapid = await loadVapid();
    const v = await vapid.verifyVapidJwt({
      jwt: m[1],
      publicKeyJwk: { kty: 'EC', crv: 'P-256', x: VAPID_PRIVATE_JWK.x, y: VAPID_PRIVATE_JWK.y },
      expectedAud: new URL(sub.endpoint).origin,
    });
    assert.equal(v.ok, true, `JWT 簽章可驗證（aud=${new URL(sub.endpoint).origin}）：${v.reason}`);
    assert.equal(v.claims.sub, VAPID_SUBJECT);
    // Content-Encoding: aes128gcm（RFC 8291 唯一允許）
    assert.equal(call.headers['Content-Encoding'], 'aes128gcm');
    // Crypto-Key: dh= 雙保險（Spike §5.4）
    assert.ok(/^dh=[A-Za-z0-9_-]+$/.test(call.headers['Crypto-Key'] || ''), 'Crypto-Key: dh=');
    // payload 可被模擬接收端（subscription p256dh 私鑰 + auth）decrypt 回原 body
    const plain = await vapid.decryptPayload({ body: call.body, subscription: { keys: sub.keys }, uaPrivateKeyJwk: sub.privJwk });
    assert.equal(new TextDecoder().decode(plain), payloadJson, 'push payload 解回 formatNotification 原文');
  }
  // 訂閱維持不變（全部成功，無清理）
  assert.equal(kv._map.size, 3);
});

test('HDL-04b POST /notify drops 缺失／空／非陣列 → 400 drops required（不發送）', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const badBodies = [undefined, {}, { drops: [] }, { drops: null }, { drops: 'x' }, { drops: 42 }];
  await withFetch(async () => new Response(null, { status: 200 }), async (f) => {
    for (const body of badBodies) {
      const res = await mod.default.fetch(mkReq('POST', '/notify', { token: TOKEN, body }), mkEnv(kv));
      assert.equal(res.status, 400, JSON.stringify(body));
      assert.equal((await res.json()).error, 'drops required');
    }
    // 非 JSON body → 400
    const res = await mod.default.fetch(mkReq('POST', '/notify', { token: TOKEN, rawBody: '{oops' }), mkEnv(kv));
    assert.equal(res.status, 400);
    assert.equal((await res.json()).error, 'drops required');
    assert.equal(f.calls.length, 0, 'drops 缺失/空不得發送任何 Web Push');
  });
});

test('HDL-04c POST /notify 自訂訊息模式（title/body/url，不需 drops）→ 推播自訂 payload', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const sub = await mkFakeSub('https://fcm.googleapis.com/fcm/send/hdl04c-custom');
  await mod.default.fetch(mkReq('POST', '/subscribe', { body: sub }), mkEnv(kv));
  let pushCall = null;
  await withFetch(async (call) => { pushCall = call; return new Response(null, { status: 201 }); }, async (f) => {
    const res = await mod.default.fetch(mkReq('POST', '/notify', {
      token: TOKEN,
      body: { title: '重要通知', body: '測試', url: './' },
    }), mkEnv(kv));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true, sent: 1, failed: 0 });
    assert.equal(f.calls.length, 1);
  });
  const vapid = await loadVapid();
  const plain = await vapid.decryptPayload({
    body: pushCall.body, subscription: { keys: sub.keys }, uaPrivateKeyJwk: sub.privJwk,
  });
  assert.deepEqual(JSON.parse(new TextDecoder().decode(plain)), {
    title: '重要通知', body: '測試', data: { url: './' },
  }, 'push payload 為自訂 title/body/url');
});

test('HDL-06 POST /notify 無訂閱者 → 200 空廣播（0 次 Web Push，無錯誤）', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  await withFetch(async () => new Response(null, { status: 200 }), async (f) => {
    const res = await mod.default.fetch(mkReq('POST', '/notify', { token: TOKEN, body: { drops: DROPS } }), mkEnv(kv));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true, sent: 0, failed: 0 });
    assert.equal(f.calls.length, 0);
  });
});

test('HDL-07 push service 回 404/410 → 自動刪除失效訂閱（E5），其餘正常發送、回 200', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const good = await mkFakeSub('https://fcm.googleapis.com/fcm/send/hdl07-good');
  const gone404 = await mkFakeSub('https://fcm.googleapis.com/fcm/send/hdl07-404');
  const gone410 = await mkFakeSub('https://fcm.googleapis.com/fcm/send/hdl07-410');
  for (const s of [good, gone404, gone410]) {
    await mod.default.fetch(mkReq('POST', '/subscribe', { body: s }), mkEnv(kv));
  }
  await withFetch(async (call) => {
    if (call.url.includes('hdl07-404')) return new Response(null, { status: 404 });
    if (call.url.includes('hdl07-410')) return new Response(null, { status: 410 });
    return new Response(null, { status: 201 });
  }, async (f) => {
    const res = await mod.default.fetch(mkReq('POST', '/notify', { token: TOKEN, body: { drops: DROPS } }), mkEnv(kv));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true, sent: 3, failed: 0 });
    assert.equal(f.calls.length, 3);
  });
  assert.deepEqual([...kv._map.keys()], ['sub:' + good.endpoint], '404/410 訂閱已清理，有效者保留');
});

test('HDL-11 push service 5xx／網路失敗：部分成功 → 200 ok:true；全部失敗 → 500 ok:false', async () => {
  const mod = await loadWorker();
  const kv = mkKV();
  const okSub = await mkFakeSub('https://fcm.googleapis.com/fcm/send/hdl11-ok');
  const bad5xx = await mkFakeSub('https://fcm.googleapis.com/fcm/send/hdl11-5xx');
  const netFail = await mkFakeSub('https://fcm.googleapis.com/fcm/send/hdl11-net');
  for (const s of [okSub, bad5xx, netFail]) {
    await mod.default.fetch(mkReq('POST', '/subscribe', { body: s }), mkEnv(kv));
  }
  await withFetch(async (call) => {
    if (call.url.includes('hdl11-5xx')) return new Response(null, { status: 500 });
    if (call.url.includes('hdl11-net')) throw new TypeError('network error');
    return new Response(null, { status: 201 });
  }, async (f) => {
    const res = await mod.default.fetch(mkReq('POST', '/notify', { token: TOKEN, body: { drops: DROPS } }), mkEnv(kv));
    // 部分成功（1/3）→ 200 ok:true；CI 不再因個別失敗而紅燈
    assert.equal(res.status, 200, '部分成功 → 200 不誤報失敗');
    const body = await res.json();
    assert.equal(body.ok, true);
    assert.equal(body.failed, 2);
    assert.equal(body.sent, 1, '成功送達 1 筆');
  });
  assert.equal(kv._map.size, 3, '失敗訂閱保留（非 404/410 不誤刪）');
  assert.ok(kv._map.has('sub:' + bad5xx.endpoint));
  assert.ok(kv._map.has('sub:' + netFail.endpoint));
});

test('HDL-11b KV 不可用 → 500 不誤報成功（/notify 與 /subscribe）', async () => {
  const mod = await loadWorker();
  await withFetch(async () => new Response(null, { status: 200 }), async () => {
    const res = await mod.default.fetch(mkReq('POST', '/notify', { token: TOKEN, body: { drops: DROPS } }), mkEnv(mkBrokenKV()));
    assert.equal(res.status, 500);
    const res2 = await mod.default.fetch(
      mkReq('POST', '/subscribe', { body: await mkFakeSub() }), mkEnv(mkBrokenKV()));
    assert.equal(res2.status, 500);
  });
});

/* ════════════════════════════════════════════════════════════════════════
 * HDL-08  formatNotification（與前端 pwa.js 同簽名合約；PWA.md §3.2 fixture）
 * ════════════════════════════════════════════════════════════════════════ */
test('HDL-08 formatNotification 與前端合約一致（§3.2 fixture TPE-NRT / TPE-KIX）', async () => {
  const mod = await loadWorker();
  const { formatNotification } = mod;
  // BDD 情境 46 row 1（TPE-NRT）
  assert.deepEqual(
    formatNotification([{ route: 'TPE-NRT', outbound_date: '2026-08-22', return_date: '2026-08-30', old_price: 26008, new_price: 24120 }]),
    EXPECTED_PAYLOAD,
  );
  // BDD 情境 46 row 2（TPE-KIX）
  assert.deepEqual(
    formatNotification([{ route: 'TPE-KIX', outbound_date: '2026-08-23', return_date: '2026-08-31', old_price: 12900, new_price: 11500 }]),
    { title: '✈️ 票價下降了！', body: 'TPE-KIX 大阪 8/23–8/31 降至 NT$11,500（原 NT$12,900）', data: { url: '?route=TPE-KIX' } },
  );
  // 多筆 → '\n' 連接（單則摘要），data.url 取第一筆
  const multi = formatNotification([
    { route: 'TPE-NRT', outbound_date: '2026-08-22', return_date: '2026-08-30', old_price: 26008, new_price: 24120 },
    { route: 'TPE-KIX', outbound_date: '2026-08-23', return_date: '2026-08-31', old_price: 12900, new_price: 11500 },
  ]);
  assert.equal(multi.body.split('\n').length, 2);
  assert.equal(multi.data.url, '?route=TPE-NRT');
  // 超過 3 筆 → slice(0,3)（E11 防禦）
  const overflow = formatNotification(Array.from({ length: 5 }, (_, i) => ({
    route: 'TPE-NRT', outbound_date: '2026-08-22', return_date: '2026-08-30', old_price: 100 + i, new_price: 10 + i,
  })));
  assert.equal(overflow.body.split('\n').length, 3);
  // 空／非陣列 → fallback「有票價更新」、data.url '?route='（§5.4）
  const fallback = { title: '✈️ 票價下降了！', body: '有票價更新', data: { url: '?route=' } };
  assert.deepEqual(formatNotification([]), fallback);
  assert.deepEqual(formatNotification(null), fallback);
  assert.deepEqual(formatNotification(undefined), fallback);
  // 未知 route → 名稱回退為 route 本身；月日去前導零
  const unknown = formatNotification([{ route: 'TPE-XXX', outbound_date: '2026-08-05', return_date: '2026-09-01', old_price: 100, new_price: 90 }]);
  assert.ok(unknown.body.startsWith('TPE-XXX TPE-XXX 8/5–9/1 '), unknown.body);
  // 無效日期 → '—'（不拋例外）
  const badDate = formatNotification([{ route: 'TPE-NRT', outbound_date: null, return_date: 'garbage', old_price: 100, new_price: 90 }]);
  assert.ok(badDate.body.includes('—–—'), badDate.body);
});

test('HDL-08b formatNotification 自訂訊息模式（title/body/url 覆寫，不需 drops）', async () => {
  const mod = await loadWorker();
  const { formatNotification } = mod;
  const DEFAULT_TITLE = '✈️ 票價下降了！';
  // title+body+url 全自訂
  assert.deepEqual(formatNotification(null, { title: '重要通知', body: '測試', url: './' }),
    { title: '重要通知', body: '測試', data: { url: './' } });
  // 只給 body → title 維持預設；url 預設 './'
  assert.deepEqual(formatNotification(null, { body: '測試' }),
    { title: DEFAULT_TITLE, body: '測試', data: { url: './' } });
  // 只給 title → body 回退為 title
  assert.deepEqual(formatNotification(null, { title: '重要通知' }),
    { title: '重要通知', body: '重要通知', data: { url: './' } });
  // 自訂優先於 drops 明細（同時送 drops 時仍以自訂為準）
  assert.deepEqual(formatNotification([{ route: 'TPE-NRT', outbound_date: '2026-08-22', return_date: '2026-08-30', old_price: 26008, new_price: 24120 }],
    { title: '手動測試', body: '自訂內容', url: '?route=TPE-NRT' }),
    { title: '手動測試', body: '自訂內容', data: { url: '?route=TPE-NRT' } });
  // 無自訂 → 既有行為不變
  assert.deepEqual(formatNotification(null, {}), { title: DEFAULT_TITLE, body: '有票價更新', data: { url: '?route=' } });
  assert.equal(formatNotification(undefined, { title: '' }).title, DEFAULT_TITLE, '空字串 title 不算自訂');
});
