#!/usr/bin/env node
// worker/spike/gen-vapid-keys.mjs — Spike S1 附屬工具（dev-only，T8 用）
// 產生一組 VAPID 金鑰對，輸出 JSON（印到 stdout，金鑰絕不寫入 repo）：
//   - privateKeyJwk：完整 JWK（含 d）→ 存 Cloudflare secret（`wrangler secret put VAPID_PRIVATE_KEY`）
//   - publicKey：raw point base64url（87 chars，0x04 開頭）→ wrangler.toml [vars] VAPID_PUBLIC_KEY
//                 以及 /vapid-public-key 回傳、前端 PushManager.subscribe(applicationServerKey) 用同值
// 執行：node worker/spike/gen-vapid-keys.mjs
// ---------------------------------------------------------------------------------------------
// 為什麼不用 `npx web-push generate-vapid-keys`（它輸出 raw 32B 私鑰 base64url）？
// Web Crypto（crypto.subtle.importKey）不吃 raw EC 私鑰標量——簽章要 JWK（需 x/y/d）或 PKCS8，
// 且無法只靠 Web Crypto 從私鑰標量反推公鑰點。本工具一次給齊 JWK + raw point，可直接進 CF secret。
import { generateVapidKeyPair } from './vapid.mjs';

const pair = await generateVapidKeyPair();
const out = {
  publicKey: pair.publicKeyBase64url,          // 87 chars raw point → VAPID_PUBLIC_KEY
  privateKeyJwk: pair.privateKeyJwk,           // JSON → VAPID_PRIVATE_KEY secret
  privateKeyJwkJson: JSON.stringify(pair.privateKeyJwk),
};
process.stdout.write(JSON.stringify(out, null, 2) + '\n');
