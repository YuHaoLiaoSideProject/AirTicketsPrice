# worker/ — 星宇票價趨勢推播服務（Cloudflare Worker，Phase 2 / T8）

對外三端點的推播中繼（決策邏輯在爬蟲端，Worker 只當推送中繼，D3）：

| 方法 | 路徑 | 認證 | Request | Response |
|------|------|------|---------|----------|
| GET | `/vapid-public-key` | 無 | — | `200 { publicKey }`（raw point b64url） |
| POST | `/subscribe` | `Bearer PUSH_API_TOKEN` | `{endpoint, keys:{p256dh,auth}, action?:'add'\|'remove'}` | `200 {ok:true}`；`400 invalid subscription` / `400 subscription limit reached`；`401 unauthorized` |
| POST | `/notify` | `Bearer PUSH_API_TOKEN` | `{drops:[{route,outbound_date,return_date,flight_no,old_price,new_price}...]}`（最多 3 條） | `200 {ok:true,sent,failed:0}`；`400 drops required`；`401 unauthorized`；`500 {ok:false,sent,failed}`（部分/全部失敗） |

- 全端點帶 CORS（`Access-Control-Allow-Origin: *` + OPTIONS 204 preflight）——頁面（github.io）與 Worker（workers.dev）跨域必需。
- Web Push 廣播：VAPID（ES256 JWT，RFC 8292）+ RFC 8291 加密（aes128gcm），實作為純 Web Crypto（`src/vapid.mjs`，Spike S1 移植，零依賴零 build）。
- push service 回 `404/410` → 該訂閱從 KV 自動刪除（E5 清理）；其他失敗（5xx/網路）→ 計入 `failed` 並保留訂閱、回應 500（HDL-11 不誤報成功）。

## 目錄

```
worker/
├── wrangler.toml        # Worker 設定（name/main/KV binding/[vars]）
├── README.md            # 本檔（部署步驟）
└── src/
    ├── index.js         # 三端點 handler + 純函式（可測；index.test.js 注入 KV/fetch mock）
    ├── index.test.js    # 單元測試（HDL-01~11；node --test）
    └── vapid.mjs        # 加密層（Spike S1 移植：VAPID 簽章 + RFC 8291 加密 + 收端解密模擬）
```

## 部署步驟（一次性）

```bash
# ① 安裝 wrangler 並登入（一次性）
npm i -g wrangler
wrangler login

# ② 建立 KV namespace（訂閱名單）
wrangler kv namespace create SUBSCRIPTIONS
#   → 輸出 binding id，貼到 wrangler.toml [[kv_namespaces]] 的 id = "<...>"

# ③ 產生 VAPID 金鑰對（一次性，本機；金鑰不進 repo、不 commit）
node worker/spike/gen-vapid-keys.mjs
#   → stdout JSON：{ publicKey, privateKeyJwk, privateKeyJwkJson }
#      - publicKey（87 chars raw point b64url）→ 貼到 wrangler.toml [vars] VAPID_PUBLIC_KEY
#      - privateKeyJwkJson → 以 secret 設定（見 ④）
#
#   ⚠️ 為什麼不用 `npx web-push generate-vapid-keys`？Web Crypto（crypto.subtle.importKey）
#      不吃 raw 32B 私鑰標量（簽章需 JWK x/y/d 齊全）——必須用本專案的 gen-vapid-keys.mjs
#      （Spike S1 §3.2 踩坑結論）。

# ④ 設定 secrets（VAPID 私鑰 + 爬蟲→Worker 共用 token）
wrangler secret put VAPID_PRIVATE_KEY   # 貼 ③ 的 privateKeyJwkJson（EC P-256 JWK JSON，含 d）
wrangler secret put PUSH_API_TOKEN      # 與 GitHub repo secret 同值（D6；可輪換，輪換時兩邊同時更新）

# ⑤ 部署
wrangler deploy
#   → 得到 https://airtickets-price-push.<account>.workers.dev
#   → 更新 web/pwa.js CONFIG.PUSH_WORKER_URL 為實際網域（§9.4；T9 一併處理）
```

## 部署後 smoke（§3 / T12 前建議）

```bash
# 公鑰端點（無需驗證）
curl https://airtickets-price-push.<account>.workers.dev/vapid-public-key

# OPTIONS preflight（CORS）
curl -i -X OPTIONS https://airtickets-price-push.<account>.workers.dev/notify

# 未附 token → 401
curl -i -X POST https://airtickets-price-push.<account>.workers.dev/notify \
  -H 'Content-Type: application/json' -d '{"drops":[]}'

# 附 token 的 /notify（空訂閱者 → 200 空廣播；drops 為空 → 400）
curl -i -X POST https://airtickets-price-push.<account>.workers.dev/notify \
  -H "Authorization: Bearer $PUSH_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"drops":[{"route":"TPE-NRT","outbound_date":"2026-08-22","return_date":"2026-08-30","flight_no":"JX 804","old_price":26008,"new_price":24120}]}'
```

真實裝置/瀏覽器訂閱後，以正確 token 呼叫 `/notify` 實測收到通知（或等 T12 的 mock push service E2E）。

## 測試

```bash
cd /fork/YuHaoLiaoSideProject/AirTicketsPrice
node --test worker/src/index.test.js   # HDL-01~11（16 案例；KV / push service 以 mock 注入）
node worker/spike/verify.mjs           # Spike S1 加密層 12 驗證（含 RFC 8291 官方範例 + http_ece 互通）
```

## 金鑰與安全備註（D6 憑證分層）

- 公鑰公開（`/vapid-public-key` / wrangler.toml `[vars]`）；私鑰只在 `VAPID_PRIVATE_KEY` secret。
- 訂閱名單只在 KV（`sub:{endpoint}`，上限 `MAX_SUBS=1000` 防灌爆）。
- `/subscribe` 與 `/notify` 皆以 `PUSH_API_TOKEN`（Bearer）保護（T8 合約；與爬蟲共用）。
- 簽章金鑰（ECDSA）與訊息加密 ephemeral 金鑰（ECDH）分開（RFC 8292 §3.2）；每次 push 新 salt + 新 ephemeral 金鑰（RFC 8291）。
- 部署後更新 `web/pwa.js` 的 `CONFIG.PUSH_WORKER_URL` 並 commit（§9.4）。
