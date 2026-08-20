# worker/ — 星宇票價推播服務（Cloudflare Worker）

三端點的推播中繼服務（決策邏輯在爬蟲端，Worker 僅負責推送）：

| 方法 | 路徑 | 認證 | Request | Response |
|------|------|------|---------|----------|
| GET | `/vapid-public-key` | 無 | — | `200 { publicKey }`（raw point b64url） |
| POST | `/subscribe` | `Bearer PUSH_API_TOKEN` | `{endpoint, keys:{p256dh,auth}, action?:'add'\|'remove'}` | `200 {ok:true}`；`400 invalid subscription` / `400 subscription limit reached`；`401 unauthorized` |
| POST | `/notify` | `Bearer PUSH_API_TOKEN` | `{drops:[{route,outbound_date,return_date,flight_no,old_price,new_price}...]}`（最多 3 條） | `200 {ok:true,sent,failed:0}`；`400 drops required`；`401 unauthorized`；`500 {ok:false,sent,failed}`（部分/全部失敗） |
| POST | `/notify`（自訂訊息） | `Bearer PUSH_API_TOKEN` | `{title?, body?, url?}`（title/body 任一非空即自訂模式，不需 drops；url 相對 SW scope，預設 `'./'`） | 同上；自訂模式用於手動測試／公告，爬蟲仍送 drops 格式不受影響 |

- 所有端點皆支援 CORS（`Access-Control-Allow-Origin: *` + OPTIONS preflight）
- Web Push 使用 VAPID + RFC 8291 加密，實作為純 Web Crypto（零依賴零 build）
- push service 回傳 `404/410` 時，該訂閱從 KV 自動刪除；其他失敗計入 `failed` 並保留訂閱，回應 500

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

# ③ 產生 VAPID 金鑰對（一次性，金鑰不會進入 repo）
node worker/spike/gen-vapid-keys.mjs
#   → stdout JSON：{ publicKey, privateKeyJwkJson }
#      - publicKey → 貼到 wrangler.toml [vars] VAPID_PUBLIC_KEY
#      - privateKeyJwkJson → 以 secret 設定（見 ④）

# ④ 設定 secrets
wrangler secret put VAPID_PRIVATE_KEY   # 貼 ③ 的 privateKeyJwkJson
wrangler secret put PUSH_API_TOKEN      # 與 GitHub repo secret 同值

# ⑤ 部署
wrangler deploy
#   → 更新 web/pwa.js CONFIG.PUSH_WORKER_URL 為實際網域
```

## 部署後驗證

```bash
# 公鑰端點
curl https://airtickets-price-push.<account>.workers.dev/vapid-public-key

# 無 token → 401
curl -i -X POST https://airtickets-price-push.<account>.workers.dev/notify \
  -H 'Content-Type: application/json' -d '{"drops":[]}'

# 有 token + drops → 200
curl -i -X POST https://airtickets-price-push.<account>.workers.dev/notify \
  -H "Authorization: Bearer $PUSH_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"drops":[{"route":"TPE-NRT","outbound_date":"2026-08-22","return_date":"2026-08-30","flight_no":"JX 804","old_price":26008,"new_price":24120}]}'
```

## 測試

```bash
cd /fork/YuHaoLiaoSideProject/AirTicketsPrice
node --test worker/src/index.test.js   # HDL-01~11（16 案例；KV / push service 以 mock 注入）
node worker/spike/verify.mjs           # Spike S1 加密層 12 驗證（含 RFC 8291 官方範例 + http_ece 互通）
```

## 安全備註

- 公鑰公開（`/vapid-public-key`）；私鑰只在 `VAPID_PRIVATE_KEY` secret
- 訂閱名單存於 KV（`sub:{endpoint}`，上限 1000 筆）
- `/subscribe` 與 `/notify` 皆以 `PUSH_API_TOKEN`（Bearer）保護
- 簽章金鑰（ECDSA）與加密 ephemeral 金鑰（ECDH）分開，每次 push 使用新金鑰
