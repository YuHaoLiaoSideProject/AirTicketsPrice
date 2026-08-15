// worker/spike/verify.mjs — Spike S1 驗證腳本（跑起來即驗證；全 PASS 且無 FAIL = Spike 可行）
// 執行：node worker/spike/verify.mjs
// ---------------------------------------------------------------------------------------------
// 驗證項目：
//   V-1  VAPID JWT：ES256 簽章可用公鑰驗證（crypto.subtle.verify）+ claims（aud/exp/sub）正確
//        + k= 參數是 65-byte raw point + 竄改偵測
//   V-2  RFC 8291 加密↔解密 round-trip（模擬接收端用 subscription 私鑰 + auth secret 解回原文）
//   V-3  buildRequest 產物符合 Web Push 協定形狀（headers/body/aud）
//   V-4  RFC 8291 §5 官方範例解密（用標準給的金鑰解出 "When I grow up, I want to be a watermelon"）
//   V-5  （可選，需 http_ece 可載入）與 web-push 底層 http_ece 雙向互通（keyid path + dh header path）
// ---------------------------------------------------------------------------------------------
import { createRequire } from 'node:module';
import nodeCrypto from 'node:crypto';
import {
  bytesToBase64url, base64urlToBytes, concatBytes,
  generateVapidKeyPair, signVapidJwt, verifyVapidJwt,
  generateEphemeralKeys, encryptPayload, decryptPayload, buildRequest,
} from './vapid.mjs';

const TE = new TextEncoder();
const TD = new TextDecoder();

/* ── 測試 harness ── */
const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, status: 'PASS' });
    console.log(`  \u2714 ${name}`);
  } catch (e) {
    results.push({ name, status: 'FAIL', error: e.message });
    console.error(`  \u2718 ${name} — ${e.message}`);
  }
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg ?? 'assertion failed');
}
function bytesEq(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/* ── 測試用假訂閱（模擬「使用者代理端」：p256dh 金鑰對 + auth secret + push service endpoint）── */
async function makeFakeSubscription() {
  const uaKeyPair = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
  const uaPublicRaw = new Uint8Array(await crypto.subtle.exportKey('raw', uaKeyPair.publicKey));
  const uaPrivateJwk = await crypto.subtle.exportKey('jwk', uaKeyPair.privateKey);
  const auth = crypto.getRandomValues(new Uint8Array(16));
  return {
    subscription: {
      endpoint: 'https://fcm.googleapis.com/fcm/send/fake-token-0123456789abcdef',
      expirationTime: null,
      keys: { p256dh: bytesToBase64url(uaPublicRaw), auth: bytesToBase64url(auth) },
    },
    uaPrivateKeyJwk: uaPrivateJwk,
    authRaw: auth,
  };
}

/* ── 測試 payload（模仿 PWA.md §3.2 通知承載）── */
const PAYLOAD = JSON.stringify({
  title: '✈️ 票價下降了！',
  body: 'TPE-NRT 東京 8/22–8/30 降至 NT$24,120（原 NT$26,008）\nTPE-KIX 大阪 8/23–8/31 降至 NT$19,800（原 NT$21,500）',
  data: { url: '?route=TPE-NRT' },
});

console.log('=== Spike S1 驗證（Node', process.version, '/ Web Crypto:', typeof crypto.subtle !== 'undefined' ? 'available' : 'MISSING', '）===\n');

/* ── V-1 VAPID JWT ── */
await check('V-1a  VAPID JWT 簽章可用公鑰驗證（ES256）+ claims 正確', async () => {
  const pair = await generateVapidKeyPair();
  const jwt = await signVapidJwt({ aud: 'https://fcm.googleapis.com', sub: 'mailto:admin@example.com', privateKeyJwk: pair.privateKeyJwk });
  const res = await verifyVapidJwt({ jwt, publicKeyJwk: pair.publicKeyJwk, expectedAud: 'https://fcm.googleapis.com' });
  assert(res.ok, `verify 失敗：${res.reason}`);
  assert(res.claims.sub === 'mailto:admin@example.com', 'sub claim 不符');
  const now = Math.floor(Date.now() / 1000);
  assert(res.claims.exp > now && res.claims.exp <= now + 12 * 3600, `exp 超出 (now, now+12h]：${res.claims.exp}`);
});

await check('V-1b  JWT 結構：三段、header {"typ":"JWT","alg":"ES256"}、簽章段 64 bytes', async () => {
  const pair = await generateVapidKeyPair();
  const jwt = await signVapidJwt({ aud: 'https://mozilla.services.mozilla.com', sub: 'mailto:a@b.c', privateKeyJwk: pair.privateKeyJwk });
  const parts = jwt.split('.');
  assert(parts.length === 3, `JWT 應為三段，實際 ${parts.length}`);
  assert(JSON.parse(TD.decode(base64urlToBytes(parts[0]))).typ === 'JWT', 'header.typ 應為 JWT');
  assert(JSON.parse(TD.decode(base64urlToBytes(parts[0]))).alg === 'ES256', 'header.alg 應為 ES256');
  assert(base64urlToBytes(parts[2]).length === 64, '簽章段應為 raw r‖s 64 bytes');
});

await check('V-1c  竄改偵測：改 claims 任一字元 → 簽章驗證失敗', async () => {
  const pair = await generateVapidKeyPair();
  const jwt = await signVapidJwt({ aud: 'https://fcm.googleapis.com', sub: 'mailto:a@b.c', privateKeyJwk: pair.privateKeyJwk });
  const parts = jwt.split('.');
  const tamperedClaims = bytesToBase64url(TE.encode(TD.decode(base64urlToBytes(parts[1])).replace('"exp"', '"exp"') + ' ')); // 多加一個空格 → 語意相同但位元組不同
  const badJwt = `${parts[0]}.${tamperedClaims}.${parts[2]}`;
  const res = await verifyVapidJwt({ jwt: badJwt, publicKeyJwk: pair.publicKeyJwk, expectedAud: 'https://fcm.googleapis.com' });
  assert(!res.ok, '竄改後的 JWT 不應通過驗證');
});

await check('V-1d  k= 參數：raw point base64url（65B、0x04 開頭）', async () => {
  const pair = await generateVapidKeyPair();
  const raw = base64urlToBytes(pair.publicKeyBase64url);
  assert(raw.length === 65 && raw[0] === 4, `k= 應解出 65B raw point，實際 ${raw.length}B`);
});

/* ── V-2 RFC 8291 round-trip ── */
await check('V-2a  加密 → 模擬接收端解密 → 原文一致（含中文/emoji）', async () => {
  const { subscription, uaPrivateKeyJwk } = await makeFakeSubscription();
  const { body, serverPublicKey } = await encryptPayload(null, subscription, PAYLOAD);
  const plain = await decryptPayload({ body, subscription, uaPrivateKeyJwk });
  assert(TD.decode(plain) === PAYLOAD, '解出的明文與原文不符');
  assert(bytesEq(serverPublicKey, new Uint8Array([4, ...serverPublicKey.slice(1)])), 'serverPublicKey 格式異常');
});

await check('V-2b  aes128gcm body 形狀：salt(16)‖rs(4)=4096‖idlen(1)=65‖keyid=as_public‖data', async () => {
  const { subscription } = await makeFakeSubscription();
  const { body, serverPublicKey } = await encryptPayload(null, subscription, PAYLOAD);
  const plainBytes = TE.encode(PAYLOAD).length; // UTF-8 位元組數（非字元數）
  assert(body.length === 16 + 4 + 1 + 65 + plainBytes + 1 + 16, `body 長度不符：${body.length}`);
  const rs = ((body[16] << 24) | (body[17] << 16) | (body[18] << 8) | body[19]) >>> 0;
  assert(rs === 4096, `rs 應為 4096，實際 ${rs}`);
  assert(body[20] === 65, `idlen 應為 65，實際 ${body[20]}`);
  assert(bytesEq(body.slice(21, 86), serverPublicKey), 'keyid 應等於 as_public（RFC 8291 §4 MUST）');
});

await check('V-2c  傳入已知 ephemeral 金鑰對也可加密（serverKeys 參數）', async () => {
  const { subscription, uaPrivateKeyJwk } = await makeFakeSubscription();
  const eph = await generateEphemeralKeys();
  const { body } = await encryptPayload(eph, subscription, 'hello');
  const plain = await decryptPayload({ body, subscription, uaPrivateKeyJwk });
  assert(TD.decode(plain) === 'hello', '已知金鑰對加密失敗');
});

/* ── V-3 buildRequest 形狀 ── */
await check('V-3a  buildRequest：method/url/headers 符合 Web Push 協定', async () => {
  const { subscription, uaPrivateKeyJwk } = await makeFakeSubscription();
  const vapid = await generateVapidKeyPair();
  const req = await buildRequest(subscription, PAYLOAD, { ...vapid, subject: 'mailto:admin@example.com' });

  assert(req.method === 'POST', 'method 應為 POST');
  assert(req.url === subscription.endpoint, 'url 應為 subscription.endpoint');

  const auth = req.headers.Authorization;
  assert(/^vapid t=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+,k=[A-Za-z0-9_-]+$/.test(auth), `Authorization 格式不符：${auth.slice(0, 40)}…`);
  const [, jwt, k] = auth.match(/^vapid t=(.+),k=(.+)$/);
  assert(base64urlToBytes(k).length === 65 && base64urlToBytes(k)[0] === 4, 'k= 應為 65B raw point');
  const claims = JSON.parse(TD.decode(base64urlToBytes(jwt.split('.')[1])));
  assert(claims.aud === new URL(subscription.endpoint).origin, `JWT aud 應為 endpoint origin：${claims.aud}`);
  assert(claims.sub === 'mailto:admin@example.com', 'JWT sub 不符');

  assert(req.headers['Content-Encoding'] === 'aes128gcm', 'Content-Encoding 應為 aes128gcm');
  const dh = req.headers['Crypto-Key'].match(/^dh=(.+)$/)?.[1];
  assert(dh, 'Crypto-Key 應為 dh=<as_public>');
  assert(base64urlToBytes(dh).length === 65, 'dh 應為 65B raw point');
  assert(/^\d+$/.test(req.headers.TTL) && Number(req.headers.TTL) > 0, 'TTL 應為正整數');
  assert(Number(req.headers['Content-Length']) === req.body.length, 'Content-Length 應等於 body 長度');
  assert(req.headers['Content-Type'] === 'application/octet-stream', 'Content-Type 應為 application/octet-stream');
});

await check('V-3b  buildRequest 的 body 可被模擬接收端解密（用 Crypto-Key: dh=）', async () => {
  const { subscription, uaPrivateKeyJwk } = await makeFakeSubscription();
  const vapid = await generateVapidKeyPair();
  const req = await buildRequest(subscription, PAYLOAD, { ...vapid, subject: 'mailto:admin@example.com' });
  const dh = req.headers['Crypto-Key'].match(/^dh=(.+)$/)[1];
  const plain = await decryptPayload({ body: req.body, subscription, uaPrivateKeyJwk, dhBase64url: dh });
  assert(TD.decode(plain) === PAYLOAD, 'buildRequest 產物解密失敗');
});

/* ── V-4 RFC 8291 §5 官方範例 ── */
await check('V-4  解密 RFC 8291 §5 官方範例（標準合規的黃金測試）', async () => {
  const exampleBody = base64urlToBytes(
    'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg' +
    'Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN');
  const receiverPubRaw = base64urlToBytes(
    'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4');
  const receiverPrivRaw = base64urlToBytes('q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94');
  const authRaw = base64urlToBytes('BTBZMqHH6r4Tts7J_aSIgg');
  const uaPrivateKeyJwk = {
    kty: 'EC', crv: 'P-256',
    x: bytesToBase64url(receiverPubRaw.slice(1, 33)),
    y: bytesToBase64url(receiverPubRaw.slice(33, 65)),
    d: bytesToBase64url(receiverPrivRaw),
  };
  const subscription = { keys: { p256dh: bytesToBase64url(receiverPubRaw), auth: bytesToBase64url(authRaw) } };
  const plain = await decryptPayload({ body: exampleBody, subscription, uaPrivateKeyJwk });
  assert(TD.decode(plain) === 'When I grow up, I want to be a watermelon',
    `RFC 8291 範例解出：${JSON.stringify(TD.decode(plain))}`);
});

/* ── V-5 與 http_ece（web-push 底層實作）雙向互通（可選）── */
const require = createRequire(import.meta.url);
let httpEce = null;
for (const p of ['node_modules/http_ece/ece.js', '/tmp/wp-ref/node_modules/http_ece/ece.js']) {
  try { httpEce = require(p); break; } catch { /* 未安裝 → 跳過互通測試 */ }
}
if (httpEce) {
  await check('V-5a  互通：我方加密 → http_ece 解密（keyid path，等同 Firefox 收端）', async () => {
    const ua = nodeCrypto.createECDH('prime256v1');
    ua.generateKeys();
    const auth = nodeCrypto.randomBytes(16);
    const sub = {
      endpoint: 'https://push.example.test/sub/xyz',
      keys: { p256dh: ua.getPublicKey().toString('base64url'), auth: auth.toString('base64url') },
    };
    const { body } = await encryptPayload(null, sub, PAYLOAD);
    const plain = httpEce.decrypt(Buffer.from(body), { version: 'aes128gcm', privateKey: ua, authSecret: auth.toString('base64url') });
    assert(Buffer.compare(plain, Buffer.from(PAYLOAD)) === 0, 'http_ece 解出的明文不符');
  });

  await check('V-5b  互通：http_ece 加密 → 我方解密（dh header path，等同 Chrome/web-push 收端）', async () => {
    const ua = nodeCrypto.createECDH('prime256v1');
    ua.generateKeys();
    const auth = nodeCrypto.randomBytes(16);
    const sub = {
      endpoint: 'https://push.example.test/sub/xyz',
      keys: { p256dh: ua.getPublicKey().toString('base64url'), auth: auth.toString('base64url') },
    };
    const uaPrivateKeyJwk = {
      kty: 'EC', crv: 'P-256',
      x: ua.getPublicKey().slice(1, 33).toString('base64url'),
      y: ua.getPublicKey().slice(33, 65).toString('base64url'),
      d: ua.getPrivateKey().toString('base64url'),
    };
    const eph = nodeCrypto.createECDH('prime256v1');
    eph.generateKeys();
    const ct = httpEce.encrypt(Buffer.from(PAYLOAD), {
      version: 'aes128gcm',
      dh: ua.getPublicKey().toString('base64url'),
      privateKey: eph,
      salt: nodeCrypto.randomBytes(16).toString('base64url'),
      authSecret: auth.toString('base64url'),
    });
    const plain = await decryptPayload({
      body: new Uint8Array(ct),
      subscription: sub,
      uaPrivateKeyJwk,
      dhBase64url: eph.getPublicKey().toString('base64url'),
    });
    assert(TD.decode(plain) === PAYLOAD, '我方解密結果不符');
  });
} else {
  console.log('  （跳過 V-5 互通測試：http_ece 不可載入。`npm i http_ece` 到 repo 或 /tmp/wp-ref 後重跑即啟用）');
}

/* ── 摘要 ── */
const fails = results.filter((r) => r.status === 'FAIL');
console.log(`\n=== 結果：${results.filter((r) => r.status === 'PASS').length} PASS / ${fails.length} FAIL / ${results.length} 總計 ===`);
if (fails.length > 0) {
  console.error('失敗項目：');
  for (const f of fails) console.error(`  - ${f.name}: ${f.error}`);
  process.exit(1);
}
console.log('Spike S1 驗證全部通過：CF Workers（Web Crypto）上自建 Web Push 可行。');
process.exit(0);
