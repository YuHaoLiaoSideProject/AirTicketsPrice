# Spike S1 — Cloudflare Workers 上 Web Push（VAPID + RFC 8291）可行性驗證報告

> **對應**：`docs/tech-decisions/PWA-2026-08-15.md`（D2 自建推播、T7 Spike S1、風險登錄「CF Workers 上 VAPID 簽章實作複雜」）；`docs/development/PWA.md` §8 步驟 7、§1.4（worker/src/index.js 設計）、§9.1
> **Spike 產物**：`worker/spike/vapid.mjs`（純函式庫）+ `worker/spike/verify.mjs`（驗證腳本）+ `worker/spike/gen-vapid-keys.mjs`（dev-only 金鑰產生）
> **驗證環境**：Node v22.23.1（Web Crypto API 與 CF Workers 同 surface；**Node 驗證通過**，CF Workers 差異風險見 §5）
> **狀態**：✅ **結論：可行——原生 Web Crypto 即可，不需第三方套件，備援路徑不觸發**（2026-08-15）

---

## 1. 結論

| 項目 | 結論 |
|------|------|
| **可行性** | ✅ **可行**。`worker/spike/verify.mjs` 12/12 全綠（含 RFC 8291 §5 官方範例解密、與 `web-push` 底層 `http_ece` 雙向互通） |
| **採用方式** | **原生 Web Crypto（`crypto.subtle`）**，零依賴、零 build。**不需要** `@block65/webcrypto-web-push`（評估見 §4） |
| **環境驗證** | Node 22 Web Crypto 驗證通過；CF Workers 的 `crypto.subtle` 支援本 Spike 用到的全部演算法（ECDSA/ECDH/HKDF/AES-GCM）且規範相同，殘餘風險僅部署環境差異（§5.6） |
| **對 T8 的影響** | `worker/spike/vapid.mjs` 可直接移植為 `worker/src/` 的加密層（§3 建議）；無需改架構、無需改 §1.4 設計 |
| **失敗備援** | **不觸發**。S1 失敗才需考慮的備援（Vercel/Netlify function + `web-push` npm）維持備而不用（§6） |

**一句話**：Web Push 最複雜的部分（VAPID ES256 簽章 + RFC 8291 兩階段 HKDF + aes128gcm 加密）已用純 Web Crypto 實作並以「RFC 官方範例」+「第三方實作互通」雙重驗證通過；CF Workers 與 Node 22 用的是同一套 API，因此 Spike 結論可代表 CF Workers 環境。

---

## 2. 驗證結果

```bash
cd /fork/YuHaoLiaoSideProject/AirTicketsPrice
node worker/spike/verify.mjs        # 全部驗證通過 = Spike 可行（exit 0）
```

實際輸出（12 PASS / 0 FAIL）：

```
=== Spike S1 驗證（Node v22.23.1 / Web Crypto: available ）===
  ✔ V-1a  VAPID JWT 簽章可用公鑰驗證（ES256）+ claims 正確
  ✔ V-1b  JWT 結構：三段、header {"typ":"JWT","alg":"ES256"}、簽章段 64 bytes
  ✔ V-1c  竄改偵測：改 claims 任一字元 → 簽章驗證失敗
  ✔ V-1d  k= 參數：raw point base64url（65B、0x04 開頭）
  ✔ V-2a  加密 → 模擬接收端解密 → 原文一致（含中文/emoji）
  ✔ V-2b  aes128gcm body 形狀：salt(16)‖rs(4)=4096‖idlen(1)=65‖keyid=as_public‖data
  ✔ V-2c  傳入已知 ephemeral 金鑰對也可加密（serverKeys 參數）
  ✔ V-3a  buildRequest：method/url/headers 符合 Web Push 協定
  ✔ V-3b  buildRequest 的 body 可被模擬接收端解密（用 Crypto-Key: dh=）
  ✔ V-4  解密 RFC 8291 §5 官方範例（標準合規的黃金測試）
  ✔ V-5a  互通：我方加密 → http_ece 解密（keyid path，等同 Firefox 收端）
  ✔ V-5b  互通：http_ece 加密 → 我方解密（dh header path，等同 Chrome/web-push 收端）
=== 結果：12 PASS / 0 FAIL / 12 總計 ===
```

| 驗證項 | 驗證內容 | 通過 |
|--------|---------|:---:|
| V-1a~d | VAPID JWT：`crypto.subtle.verify` 驗簽（ES256）+ claims（aud/exp/sub）正確 + 竄改偵測 + k= 為 65B raw point | ✅ |
| V-2a~c | RFC 8291 round-trip：用自製「模擬接收端」（subscription p256dh 私鑰 + auth secret）解回原文（含中文/emoji）；body 各欄位（salt/rs/idlen/keyid）逐一檢查 | ✅ |
| V-3a~b | `buildRequest` 產物：method/url、`Authorization: vapid t=..,k=..`、`Content-Encoding: aes128gcm`、`Crypto-Key: dh=`、TTL、Content-Length 全符合協定；body 可被模擬接收端解回原文 | ✅ |
| V-4 | **RFC 8291 §5 官方範例解密**：用標準給的金鑰解出 "When I grow up, I want to be a watermelon"——證明整條 ECDH+HKDF+AES 鏈與公開標準完全一致（黃金測試） | ✅ |
| V-5a~b | 與 `web-push` npm 底層加密引擎 `http_ece` 雙向互通：我方加密→http_ece 解（keyid path）；http_ece 加密→我方解（dh header path） | ✅ |

> V-5 依賴 `http_ece` 可載入（本機 `/tmp/wp-ref/` 或 repo `node_modules/`）；未安裝時自動跳過不影響其餘 10 項。

---

## 3. 對 T8（worker/src/index.js）的具體建議

1. **直接搬 `worker/spike/vapid.mjs` 進 `worker/src/`**（或保留為共用檔 `worker/src/vapid.mjs` 由 index.js import）。它已零 Node-only API（無 Buffer/node:crypto/process，見 §5.1），純 ESM，與 §1.4 的 `makeVapidAuth` / `encryptPayload` / `sendPush` 設計一一對應：

   | §1.4 設計 | Spike 對應 |
   |-----------|-----------|
   | `makeVapidAuth(subscription, privateKeyJwk, subject)` | `signVapidJwt({aud, sub, privateKeyJwk})` + `publicKeyBase64url`（k= 參數） |
   | `encryptPayload(payload, subscription, vapidPublicKey)` | `encryptPayload(serverKeys, subscription, payload)`（serverKeys=null 即內部產生 ephemeral） |
   | `sendPush(subscription, payload, env)` | `buildRequest(subscription, payload, vapid)` → `fetch(url, {method:'POST', headers, body})` |

2. **VAPID 私鑰格式（與 §9.1 的差異——務必注意）**：§9.1 建議 `npx web-push generate-vapid-keys` 產生的 raw base64url 私鑰（32B）**無法直接被 Web Crypto 當簽章金鑰**——`crypto.subtle.importKey('raw')` 不吃 EC 私鑰標量（只吃 PKCS8），JWK import 需 x/y/d 齊全，且 Web Crypto 無法從私鑰標量反推公鑰點。**改法**：用本 Spike 的 `node worker/spike/gen-vapid-keys.mjs` 一次輸出 JWK + raw point：
   - `privateKeyJwk`（JSON）→ `wrangler secret put VAPID_PRIVATE_KEY`（Worker 內 `JSON.parse` 後 `importKey('jwk', …, ['sign'])`）
   - `publicKey`（87 chars raw point）→ `wrangler.toml [vars] VAPID_PUBLIC_KEY`、`GET /vapid-public-key` 回傳、前端 `PushManager.subscribe(applicationServerKey)` 用同一值（前端也要吃 raw point，非 SPKI——與 web-push 生態一致）

3. **JWT 每訂閱者重簽（aud 不同）**：VAPID JWT 的 `aud` = 該訂閱 endpoint 的 origin（`new URL(endpoint).origin`）。FCM / APNs / Mozilla 各 origin 不同 → 每個 origin 簽一次即可，同 origin 多訂閱者可共用（可做 `Map<aud, jwt>` cache，12h 效期內重複用）。`signVapidJwt` 預設 exp=12h（RFC 8292 上限 24h）。

4. **fetch 實作注意**：
   - 直接 `fetch(endpoint, {method:'POST', headers, body})`（body 為 Uint8Array）。**不要手動設 Content-Length**——CF Runtime 依 body 自動計算，手動設可能被忽略或報錯；`buildRequest` 回傳 Content-Length 僅供形狀驗證。
   - `TTL` 依訂閱來源設合理值（預設 86400；FCM 上限 2419200；Mozilla autopush 要求必帶）。
   - 回應分流維持 §1.4 設計：`404/410` → 刪 KV 訂閱；`401/403`（VAPID 驗證失敗）與 `429/5xx` → 記入 failed（HDL-11 語意）。
   - 加密層所有操作皆 async（subtle 皆 Promise），`/notify` handler 已是 async，無結構問題。

5. **金鑰與密鑰生命週期**：
   - VAPID 簽章金鑰（ECDSA）與訊息加密 ephemeral 金鑰（ECDH）**必須分開**（RFC 8292 §3.2：push service SHOULD 拒絕兩者相同）——本 Spike 兩者本就是不同 key pair，符合。
   - 每次 push 用**新的** ephemeral ECDH 金鑰（`generateEphemeralKeys()`）＋新 salt（RFC 8291 要求 salt 不可重用）。
   - `importKey` 的 CryptoKey 可模組層 cache（Worker 全域變數），避免每封通知重複 import。

6. **測試**：`worker/src/index.test.js`（HDL-01~11）可直接 mock `globalThis.fetch`，用 `buildRequest` 產物斷言 headers/body（如本 Spike V-3 的做法）；加密層合規性已有 V-4/V-5 背書，單元測試不需重驗密碼學，只需驗「傳了正確的 endpoint/headers/body」。

7. **部署後 smoke（T12 前建議）**：`wrangler dev` 或部署後，用真實瀏覽器訂閱（或 T12 的 mock push service）發一則通知實測。若想先打真 push service，可用本機 `web-push` 套件產一組訂閱對照組交叉比對（本 Spike V-5 的做法）——不需真裝置。

---

## 4. 採用方式：原生 Web Crypto（不需第三方套件）

| 選項 | 評估 | 結論 |
|------|------|------|
| **原生 Web Crypto（採用）** | 本 Spike 完整實作並通過「RFC 官方範例 + http_ece 互通」雙重驗證；零依賴、零 build、~330 行含註解；與專案「純靜態、零第三方」路線一致 | ✅ |
| `@block65/webcrypto-web-push` | 唯一主流的「無 Node crypto」Web Push 套件，可省自寫功夫；但多一個第三方依賴、需評估其維護狀態與 API 穩定度；本 Spike 證明所需功能 ~330 行即可，依賴成本 > 自寫成本 | ❌ 不採用 |
| `web-push` npm（Node crypto） | CF Workers **無法執行**（依賴 node:crypto 的 createECDH/createSign/Buffer）——正是 Spike 要驗證的替代方案，僅作為**互通參照**使用 | ❌ 不能直接用 |

**Spike 實作規模**（`worker/spike/vapid.mjs`）：位元組工具（b64url/串接，無 Buffer）+ 金鑰格式轉換 + `signVapidJwt`（~15 行）+ `encryptPayload`（~35 行）+ `decryptPayload`（收端模擬，~35 行）+ `buildRequest`（~15 行）。與決策文件「~150 行 Worker」的估算相符。

---

## 5. 關鍵實作注意點（踩過的坑，T8 必讀）

### 5.1 CF Workers 與 Node 的 API 差異（本 Spike 實測）

| 面向 | Node 22 | CF Workers | 影響 |
|------|---------|-----------|------|
| `crypto.subtle`（ECDSA/ECDH/HKDF/AES-GCM） | ✅ 全域 | ✅ 全域，規範相同 | 無——本 Spike 驗證過的 API 兩邊都有 |
| `crypto.getRandomValues` | ✅ | ✅ | 無（salt/eph 金鑰用） |
| `TextEncoder/TextDecoder`、`atob` | ✅（Node 16+） | ✅ | 無——**b64url 工具刻意用 atob 而非 Buffer**，可原樣搬遷 |
| `Buffer` / `node:crypto`（createECDH/createSign） | ✅ | ❌ **不存在** | Spike 庫**零使用**（grep 驗證）；Node 專用 API 只在 verify.mjs 測試端出現 |
| `URL` / `fetch` | ✅ | ✅ | 無（aud 計算與 push POST 用） |
| `Content-Length` 手動設定 | 可 | ⚠️ Runtime 自動計算，手動設可能被忽略 | T8 的 fetch 不設（§3.4） |
| ECDSA 簽章輸出 | raw r‖s（Web Crypto 規範） | 同（同一份規範） | 兩邊一致，無差異 |

### 5.2 簽章格式陷阱（VAPID JWT）

- `crypto.subtle.sign({name:'ECDSA', hash:'SHA-256'})` 回傳的是 **JOSE 格式 raw r‖s（64 bytes）**，**不是** Node `crypto.sign`/OpenSSL 的 DER ASN.1。JWT 簽章段可直接 `base64url(64B)`，`crypto.subtle.verify` 也直接吃 raw r‖s。
- 因此**不需要任何 DER↔JOSE 轉換**。若在網路上看到「crypto.subtle 要轉 DER」的說法，多半是混用了非 Web Crypto 的簽章函式（如 `crypto.sign`）——那是本 Spike 唯一的格式陷阱，且結論是「Web Crypto 反而更簡單」。
- 簽章輸入必須是 `b64url(header) + '.' + b64url(claims)` 的字串本身（SHA-256 後簽）；`typ/alg` header 不可省略或改序（本 Spike 固定 `{"typ":"JWT","alg":"ES256"}`）。

### 5.3 HKDF info 字串（RFC 8291 兩階段，最容易寫錯）

RFC 8291 **不是**一次 HKDF 就完事，是兩階段（與 `web-push`/`http_ece` 實作完全一致）：

```
階段 1（RFC 8291 §3.3）：IKM = HKDF(salt=auth_secret, ikm=ecdh_secret, info="WebPush: info"‖0x00‖ua_public‖as_public, L=32)
階段 2（RFC 8188 §2.2/2.3）：CEK   = HKDF(salt=content_salt, ikm=IKM, info="Content-Encoding: aes128gcm"‖0x00, L=16)
                            NONCE = HKDF(salt=content_salt, ikm=IKM, info="Content-Encoding: nonce"‖0x00, L=12)
```

注意點：
- **info 字串結尾要帶一個 0x00 位元組**（"WebPush: info" 不是 NUL 結尾，而是「字串 + 0x00」）。
- `ua_public`/`as_public` 是 **65B 未壓縮點（0x04 開頭）**，順序固定「收端(UA)在前、發端(AS)在後」。
- 階段 2 的 salt 是 **content coding header 的 16B salt**（隨機），不是 auth secret。
- **`crypto.subtle.deriveBits` 的 length 是「位元」不是位元組**：IKM 傳 256（32B）、CEK 傳 128（16B）、NONCE 傳 96（12B）。傳 12 會報 "length must be a multiple of 8"（本 Spike 第一次跑就踩到）。

### 5.4 aes128gcm 加密格式（RFC 8188 + RFC 8291 §4）

- **body 佈局**：`salt(16) ‖ rs(4, big-endian=4096) ‖ idlen(1) ‖ keyid(idlen) ‖ ciphertext(+16B tag)`。
- **keyid 必須放 as_public（65B raw point）**——RFC 8291 §4 明文 MUST，RFC §5 範例即此形式（本 Spike V-4 用 keyid 成功解密）。**同時**也放 `Crypto-Key: dh=<as_public>` header（web-push/Chrome 習慣）——雙保險，兩大瀏覽器都能解（V-5 兩條路徑都驗過）。
- **GCM 的 additionalData 是「空」**（RFC 8188 §2.1：zero-length AAD）——不是 header！舊 `aesgcm`（aesgcm-04）才用 header 當 AAD；RFC 8291 用 `aes128gcm` 後 AAD 為空。若誤帶 AAD 會解不開。
- **明文 = payload ‖ 0x02**（padding delimiter，單一 record 最後一筆=2）；收端檢查最後非 0 位元組必須是 0x02 並剝除（RFC 8291 §4 MUST check）。
- `rs` 需大於明文+1+16；本 Spike 固定 4096（RFC 8291 建議值，payload <3993B 都適用；通知摘要遠小於此）。
- 單一 record、序列號 0 → nonce 不與任何值 XOR（RFC 8291 §3.4 註）。

### 5.5 VAPID 其他細節（RFC 8292）

- `Authorization: vapid t=<JWT>,k=<base64url(65B raw point)>`（RFC 8292 §3）。`k=` 是 **X9.62 未壓縮點**，不是 SPKI/JWK（注意 `web-push` npm 用舊 draft 風格的 `Authorization: WebPush <jwt>` + `Crypto-Key: p256ecdsa=`，push service 兩者都收；本專案採 RFC 8292 正式格式）。
- claims：`aud` = endpoint **origin**（scheme://host[:port]，不含路徑）；`exp` ≤ 24h（RFC 8292 §2，預設 12h）；`sub` = mailto: 或 https: URI（SHOULD，§9.1 的 VAPID_SUBJECT）。
- 簽章金鑰（ECDSA）≠ 加密金鑰（ECDH）：RFC 8292 §3.2 要求不同 key pair，push service 可能以 400 拒絕相同者——本 Spike 天然分開。

### 5.6 CF Workers 殘餘差異風險（誠實評估）

| 風險 | 等級 | 說明與緩解 |
|------|:---:|-----------|
| Workers `crypto.subtle` 與 Node 實作細節差異（如 ECDSA 隨機 k、演算法支援） | 低 | 兩者同一 W3C 規範；ECDSA 簽章本身隨機（每次不同但皆可驗證），不影響；V-4（RFC 官方範例）證明的密碼鏈是確定性的（HKDF/ECDH/AES），兩邊結果位元級相同 |
| Workers 對手動 `Content-Length`/部分 header 的限制 | 低 | fetch 時不設 Content-Length（§3.4） |
| 真 push service（FCM/APNs/Mozilla）的 VAPID 相容細節 | 低 | V-5 已與主流實作（web-push/http_ece）互通；真服務差異只剩 HTTP 層，T8 部署後 smoke 即可確認 |
| VAPID 私鑰 secret 注入格式 | 低 | 用 JWK JSON（§3.2），`wrangler secret put` 原樣傳入即可 |

---

## 6. 失敗備援路徑

| 備援 | 觸發條件（皆未發生） | 狀態 |
|------|---------------------|------|
| Vercel/Netlify function（Node 環境用 `web-push` npm） | CF Workers `crypto.subtle` 缺演算法／Spike 加密鏈驗證失敗 | **不觸發**——Spike 全綠，無需換環境 |
| OneSignal 復議（決策 D2 放棄的方案） | 上述兩者皆不可行 | 不觸發（最後手段，維持原狀） |

備援路徑的決策文件紀錄（`docs/tech-decisions/PWA-2026-08-15.md` 風險登錄）維持有效，但本次 Spike 後**無需執行**。

---

## 7. 交付物

| 檔案 | 內容 |
|------|------|
| `worker/spike/vapid.mjs` | Spike 純函式庫：VAPID 簽章/驗證、RFC 8291 加密、收端解密模擬、buildRequest；零 Node-only API，可直接移植 CF Workers |
| `worker/spike/verify.mjs` | 驗證腳本（`node worker/spike/verify.mjs`，exit 0 = 可行）：V-1~V-5 共 12 項（含 RFC 官方範例 + http_ece 互通） |
| `worker/spike/gen-vapid-keys.mjs` | dev-only：產出 JWK（CF secret）+ raw point 公鑰（wrangler.toml / 前端訂閱用），供 T8 金鑰配置 |
| `docs/development/spike-s1.md` | 本報告 |

**未動**：`web/`、`fetch_prices.py`、`tests/`、`api/`、GitHub Actions（本 Spike 為 scratch，僅新增 `worker/spike/` 與本報告）。

---

## 附錄 A：RFC 8291 §5 官方範例驗證細節（V-4）

用 RFC 8291 附錄給定的金鑰組（receiver 私鑰 `q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94`、auth `BTBZMqHH6r4Tts7J_aSIgg`、範例 body 144 bytes）走完整解密鏈：

```
body 解析：salt(16) ‖ rs=4096 ‖ idlen=65 ‖ keyid(65B=發端公鑰) ‖ data(58B)
ECDH(ua_private, keyid) → HKDF 階段1/2 → CEK/NONCE → AES-GCM(data) → plaintext(42B)
→ 去 0x02 padding → "When I grow up, I want to be a watermelon" ✓
```

這證明本 Spike 的 ECDH 共享金鑰、兩階段 HKDF（salt/info/L 全對）、AES-128-GCM（AAD=空）與公開標準**位元級一致**——是比 round-trip 更強的合規證據（round-trip 只證明自洽，V-4 證明與標準一致）。
