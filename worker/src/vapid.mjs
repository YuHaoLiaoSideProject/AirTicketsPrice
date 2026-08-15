// worker/src/vapid.mjs — T8 生產加密層（移植自 worker/spike/vapid.mjs，Spike S1 結論：可原樣搬進 CF Workers）
// ---------------------------------------------------------------------------------------------
// 純 ES module、零依賴、只用標準 Web Crypto API（crypto.subtle / getRandomValues / TextEncoder /
// TextDecoder / atob）——與 Cloudflare Workers 執行環境完全同 API surface（無 Node Buffer、
// 無 node:crypto、無 process）。spike 副本保持原樣（verify.mjs 依賴），本檔為 worker/src 使用的生產版。
//
// 內容：
//   - VAPID JWT 簽章（ES256 / ECDSA P-256，RFC 8292）
//   - Web Push 訊息加密（RFC 8291 / aes128gcm，RFC 8188）
//   - buildRequest：組出 push service POST（Authorization / Content-Encoding / Crypto-Key / TTL…）
//   - 收端解密（decryptPayload）：模擬瀏覽器接收端，供 verify.mjs 驗證用
//
// 參考標準：
//   - RFC 8291（Web Push Message Encryption，aes128gcm）
//   - RFC 8292（VAPID）
//   - RFC 8188（aes128gcm content coding）
//   - RFC 7515 / 7518（JWS / ES256）
// 驗證：node worker/spike/verify.mjs（全綠 = Spike 可行）；T8 引用本檔：worker/src/index.js + index.test.js
// ---------------------------------------------------------------------------------------------

/* ── 小型位元組工具（無 Buffer，可原樣搬進 CF Workers）── */

const TE = new TextEncoder();
const TD = new TextDecoder();
const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

/** Uint8Array → base64url（無 padding） */
export function bytesToBase64url(bytes) {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let out = '';
  for (let i = 0; i < u8.length; i += 3) {
    const b0 = u8[i];
    const b1 = i + 1 < u8.length ? u8[i + 1] : 0;
    const b2 = i + 2 < u8.length ? u8[i + 2] : 0;
    out += B64[b0 >> 2] + B64[((b0 & 3) << 4) | (b1 >> 4)];
    if (i + 1 < u8.length) out += B64[((b1 & 15) << 2) | (b2 >> 6)];
    if (i + 2 < u8.length) out += B64[b2 & 63];
  }
  return out;
}

/** base64url（可含 padding）→ Uint8Array */
export function base64urlToBytes(str) {
  const b64 = String(str).replace(/-/g, '+').replace(/_/g, '/');
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** 多段 Uint8Array 串接 */
export function concatBytes(...arrays) {
  const total = arrays.reduce((n, a) => n + a.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const a of arrays) {
    out.set(a, off);
    off += a.length;
  }
  return out;
}

/** 4-byte big-endian 的 uint32 */
function u32be(n) {
  return new Uint8Array([(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff]);
}

function readU32be(u8, off) {
  return ((u8[off] << 24) | (u8[off + 1] << 16) | (u8[off + 2] << 8) | u8[off + 3]) >>> 0;
}

export function randomBytes(n) {
  return crypto.getRandomValues(new Uint8Array(n));
}

function toBytes(payload) {
  return typeof payload === 'string' ? TE.encode(payload) : new Uint8Array(payload);
}

/* ── 金鑰格式轉換 ── */

/** JWK（x/y 座標）→ SEC1 未壓縮點（0x04 ‖ x ‖ y，65 bytes）——VAPID k= 參數 / Crypto-Key dh 用的格式 */
export function jwkToRawPoint(jwk) {
  return concatBytes(new Uint8Array([4]), base64urlToBytes(jwk.x), base64urlToBytes(jwk.y));
}

/* 簽章格式（重要）：Web Crypto 的 crypto.subtle.sign({name:'ECDSA'}) 回傳的是 JOSE 格式的
 * raw r‖s（64 bytes，RFC 6090），不是 Node crypto.sign / OpenSSL 的 DER ASN.1——
 * 因此 JWT 簽章段可直接 base64url，crypto.subtle.verify 也直接吃 raw r‖s，
 * **不需要任何 DER ↔ JOSE 轉換**（若在別處看到需轉換，多半是用了非 Web Crypto 的簽章函式）。 */
/* ── VAPID（RFC 8292）── */

/** 產生 VAPID 金鑰對；回傳 JWK（可存 CF secret）+ raw point base64url（k= 參數、前端訂閱用） */
export async function generateVapidKeyPair() {
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const jwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
  return {
    privateKeyJwk: jwk, // { kty, crv, x, y, d }——T8 存 CF secret
    publicKeyJwk: { kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y },
    publicKeyBase64url: bytesToBase64url(jwkToRawPoint(jwk)), // 87 chars raw point（0x04 開頭）
  };
}

/**
 * 簽出 VAPID JWT（ES256）。
 * header = {"typ":"JWT","alg":"ES256"}；claims = { aud, exp, sub }（RFC 8292 §2）
 * 簽章 = ECDSA P-256/SHA-256 的 raw r‖s（64B）base64url（Web Crypto 原生輸出即此格式，無需轉換，見檔案上方註解）。
 * @param {object} opts
 * @param {string} opts.aud   push endpoint 的 origin（new URL(endpoint).origin）
 * @param {string} opts.sub   subject（mailto: 或 https:）
 * @param {object} opts.privateKeyJwk  ECDSA P-256 JWK（含 d）
 * @param {number} [opts.expSeconds=43200]  exp 有效秒數（RFC 8292 上限 24h；預設 12h）
 * @returns {Promise<string>} JWT
 */
export async function signVapidJwt({ aud, sub, privateKeyJwk, expSeconds = 12 * 3600, now }) {
  const iat = now ?? Math.floor(Date.now() / 1000);
  const enc = (o) => bytesToBase64url(TE.encode(JSON.stringify(o)));
  const header = { typ: 'JWT', alg: 'ES256' };
  const claims = { aud, exp: iat + expSeconds, sub };
  const signingInput = `${enc(header)}.${enc(claims)}`;
  const key = await crypto.subtle.importKey('jwk', privateKeyJwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, TE.encode(signingInput)));
  return `${signingInput}.${bytesToBase64url(sig)}`;
}

/** 驗證 VAPID JWT（簽章 + claims）。回傳 { ok, reason?, claims? } */
export async function verifyVapidJwt({ jwt, publicKeyJwk, expectedAud, now }) {
  const parts = String(jwt).split('.');
  if (parts.length !== 3) return { ok: false, reason: 'JWT 不是三段結構' };
  const [h, p, s] = parts;
  let header, claims;
  try {
    header = JSON.parse(TD.decode(base64urlToBytes(h)));
    claims = JSON.parse(TD.decode(base64urlToBytes(p)));
  } catch {
    return { ok: false, reason: 'JWT header/claims 無法解析' };
  }
  if (header.alg !== 'ES256' || header.typ !== 'JWT') return { ok: false, reason: `header 不符：${JSON.stringify(header)}` };
  const key = await crypto.subtle.importKey('jwk', publicKeyJwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  const sigOk = await crypto.subtle.verify(
    { name: 'ECDSA', hash: 'SHA-256' },
    key,
    base64urlToBytes(s), // raw r‖s（Web Crypto 原生格式）
    TE.encode(`${h}.${p}`),
  );
  if (!sigOk) return { ok: false, reason: '簽章驗證失敗（公鑰或簽章不匹配）' };
  const nowSec = now ?? Math.floor(Date.now() / 1000);
  if (expectedAud !== undefined && claims.aud !== expectedAud) return { ok: false, reason: `aud 不符：${claims.aud} ≠ ${expectedAud}` };
  if (typeof claims.exp !== 'number' || claims.exp < nowSec) return { ok: false, reason: `exp 已過期：${claims.exp} < ${nowSec}` };
  if (claims.exp > nowSec + 24 * 3600) return { ok: false, reason: 'exp 超過 RFC 8292 的 24h 上限' };
  return { ok: true, claims };
}

/* ── Web Push 訊息加密（RFC 8291 / aes128gcm）── */

/**
 * 產生一次性（ephemeral）ECDH 金鑰對——每次 push 用新金鑰（RFC 8291 要求；不要複用 VAPID 簽章金鑰）。
 * @returns {Promise<CryptoKeyPair>} extractable=true（需 export raw 公鑰）
 */
export async function generateEphemeralKeys() {
  return crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
}

/**
 * RFC 8291 訊息加密。
 * 步驟：ECDH(ua_public, as_private) → HKDF(salt=auth, info="WebPush: info\0‖ua‖as", L=32) [IKM]
 *       → HKDF(salt=content_salt, info="Content-Encoding: aes128gcm\0"/"…nonce\0", L=16/12)
 *       → AES-128-GCM（AAD = 空，RFC 8188 §2.1）對 plaintext = payload ‖ 0x02（padding delimiter）
 * body = salt(16) ‖ rs(4=4096) ‖ idlen(1=65) ‖ keyid(as_public) ‖ ciphertext+tag（RFC 8291 §4：keyid 必含 as_public）
 *
 * @param {CryptoKeyPair|null} serverKeys  發送端 ECDH 金鑰對（null → 內部產生 ephemeral）
 * @param {object} subscription  { endpoint, keys: { p256dh: b64url(65B raw point), auth: b64url(16B) } }
 * @param {string|Uint8Array} payload 明文
 * @returns {Promise<{body: Uint8Array, salt: Uint8Array, serverPublicKey: Uint8Array, ciphertext: Uint8Array}>}
 */
export async function encryptPayload(serverKeys, subscription, payload) {
  const uaPublicRaw = base64urlToBytes(subscription.keys.p256dh);
  if (uaPublicRaw.length !== 65 || uaPublicRaw[0] !== 4) throw new Error('keys.p256dh 必須是 65-byte 未壓縮點（0x04 開頭）');
  const auth = base64urlToBytes(subscription.keys.auth);
  if (auth.length < 16) throw new Error('keys.auth 必須 ≥ 16 bytes');

  const eph = serverKeys ?? (await generateEphemeralKeys());
  const asPublicRaw = new Uint8Array(await crypto.subtle.exportKey('raw', eph.publicKey));
  const uaPublicKey = await crypto.subtle.importKey('raw', uaPublicRaw, { name: 'ECDH', namedCurve: 'P-256' }, false, []);

  // ① ECDH shared secret（X 座標，32B）
  const ecdhSecret = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaPublicKey }, eph.privateKey, 256));

  // ② RFC 8291 §3.3 第一階段：PRK_key = Extract(auth, ecdh)；IKM = Expand(PRK_key, key_info, L=32 bytes)
  //    （crypto.subtle.deriveBits 的 length 是位元，故傳 256）
  const keyInfo = concatBytes(TE.encode('WebPush: info'), new Uint8Array([0]), uaPublicRaw, asPublicRaw);
  const ecdhHkdfKey = await crypto.subtle.importKey('raw', ecdhSecret, 'HKDF', false, ['deriveBits']);
  const ikm = new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt: auth, info: keyInfo }, ecdhHkdfKey, 256));

  // ③ RFC 8188 第二階段：PRK = Extract(content_salt, ikm)；CEK = Expand(PRK, cek_info, 16B=128b)；NONCE = Expand(PRK, nonce_info, 12B=96b)
  const salt = randomBytes(16);
  const ikmHkdfKey = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  const cek = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt, info: concatBytes(TE.encode('Content-Encoding: aes128gcm'), new Uint8Array([0])) }, ikmHkdfKey, 128));
  const nonce = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt, info: concatBytes(TE.encode('Content-Encoding: nonce'), new Uint8Array([0])) }, ikmHkdfKey, 96));

  // ④ AES-128-GCM；AAD = 空（RFC 8188 §2.1）；plaintext = payload ‖ 0x02（padding delimiter，RFC 8291 §4 收端會檢查）
  const aesKey = await crypto.subtle.importKey('raw', cek, { name: 'AES-GCM' }, false, ['encrypt']);
  const plaintext = concatBytes(toBytes(payload), new Uint8Array([2]));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, aesKey, plaintext));

  // ⑤ aes128gcm record：salt(16) ‖ rs(4=4096) ‖ idlen(1=65) ‖ keyid(as_public) ‖ ciphertext+tag
  const rs = 4096; // RFC 8291 §4：單一 record，rs 需 > 明文+1+16
  const body = concatBytes(salt, u32be(rs), new Uint8Array([asPublicRaw.length]), asPublicRaw, ciphertext);
  return { body, salt, serverPublicKey: asPublicRaw, ciphertext };
}

/**
 * 收端解密（模擬瀏覽器接收端；verify.mjs 驗證用）。
 * 依 RFC 8291 §4 優先讀 body keyid 內含的 as_public；keyid 為空時退回 Crypto-Key: dh= header（Chrome/web-push 相容）。
 * @param {object} args
 * @param {Uint8Array} args.body            aes128gcm body
 * @param {object} args.subscription        { keys: { p256dh, auth } }（收端自己的公鑰與 auth secret）
 * @param {object} args.uaPrivateKeyJwk     收端 ECDH 私鑰 JWK（含 d）
 * @param {string} [args.dhBase64url]       Crypto-Key: dh= 值（keyid 為空時使用）
 * @returns {Promise<Uint8Array>} 解密且去 padding 後的明文
 */
export async function decryptPayload({ body, subscription, uaPrivateKeyJwk, dhBase64url }) {
  if (body.length < 21) throw new Error('body 太短（缺 aes128gcm header）');
  const salt = body.slice(0, 16);
  const rs = readU32be(body, 16);
  const idlen = body[20];
  if (idlen > 0 && body.length < 21 + idlen) throw new Error('body keyid 長度不符');
  const keyid = body.slice(21, 21 + idlen);
  const data = body.slice(21 + idlen);
  if (idlen === 0 && !dhBase64url) throw new Error('keyid 為空且無 Crypto-Key: dh= 可參考');

  const asPublicRaw = idlen > 0 ? keyid : base64urlToBytes(dhBase64url);
  const uaPublicRaw = base64urlToBytes(subscription.keys.p256dh);
  const auth = base64urlToBytes(subscription.keys.auth);

  const uaPrivateKey = await crypto.subtle.importKey('jwk', uaPrivateKeyJwk, { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
  const asPublicKey = await crypto.subtle.importKey('raw', asPublicRaw, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const ecdhSecret = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: asPublicKey }, uaPrivateKey, 256));

  const keyInfo = concatBytes(TE.encode('WebPush: info'), new Uint8Array([0]), uaPublicRaw, asPublicRaw);
  const ikm = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt: auth, info: keyInfo },
    await crypto.subtle.importKey('raw', ecdhSecret, 'HKDF', false, ['deriveBits']), 256));
  const ikmKey = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  const cek = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt, info: concatBytes(TE.encode('Content-Encoding: aes128gcm'), new Uint8Array([0])) }, ikmKey, 128));
  const nonce = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt, info: concatBytes(TE.encode('Content-Encoding: nonce'), new Uint8Array([0])) }, ikmKey, 96));

  const aesKey = await crypto.subtle.importKey('raw', cek, { name: 'AES-GCM' }, false, ['decrypt']);
  const plaintext = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: nonce }, aesKey, data));

  // 去 padding：尾端 0x00* 之後必須是 0x02（RFC 8291 §4 收端檢查）
  let end = plaintext.length - 1;
  while (end >= 0 && plaintext[end] === 0) end--;
  if (end < 0 || plaintext[end] !== 2) throw new Error('padding delimiter 不是 0x02');
  return plaintext.slice(0, end);
}

/* ── 組 push service POST（buildRequest）── */

/**
 * 組出送給 push service 的完整 POST 請求（RFC 8030 + RFC 8291 + RFC 8292）。
 * @param {object} subscription  { endpoint, keys: { p256dh, auth } }
 * @param {string|Uint8Array} payload 明文訊息
 * @param {object} vapid  { privateKeyJwk, publicKeyBase64url, subject, ttl? }
 *   publicKeyBase64url = raw point b64url（87 chars，k= 參數）；ttl 預設 86400（秒）
 * @returns {Promise<{method:'POST', url:string, headers:Record<string,string>, body:Uint8Array}>}
 *   注意：CF Workers 的 fetch 會自動依 body 算 Content-Length，實作時可略過該 header（見報告 §注意點）。
 */
export async function buildRequest(subscription, payload, vapid) {
  const { body, serverPublicKey } = await encryptPayload(null, subscription, payload);
  const aud = new URL(subscription.endpoint).origin; // RFC 8292：aud = endpoint 的 origin
  const jwt = await signVapidJwt({ aud, sub: vapid.subject, privateKeyJwk: vapid.privateKeyJwk });
  return {
    method: 'POST',
    url: subscription.endpoint,
    headers: {
      Authorization: `vapid t=${jwt},k=${vapid.publicKeyBase64url}`, // RFC 8292 §3
      'Content-Encoding': 'aes128gcm',                               // RFC 8291 §4：唯一允許的 content-encoding
      'Crypto-Key': `dh=${bytesToBase64url(serverPublicKey)}`,       // Chrome/web-push 相容（keyid 已含同值，雙保險）
      TTL: String(vapid.ttl ?? 86400),
      'Content-Length': String(body.length),
      'Content-Type': 'application/octet-stream',
    },
    body,
  };
}
