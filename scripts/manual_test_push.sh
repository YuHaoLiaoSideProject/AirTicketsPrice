#!/usr/bin/env bash
# =============================================================================
# 手動測試推播通知（本機執行）
# -----------------------------------------------------------------------------
# 兩種模式：
#   ./scripts/manual_test_push.sh --status    # 不需 token：檢查 Worker 狀態＋訂閱人數
#   ./scripts/manual_test_push.sh --notify    # 需要 token：從真實 data/ 取航班，觸發一次推播
#   ./scripts/manual_test_push.sh --help      # 本說明
#
# 需要環境變數：
#   PUSH_API_TOKEN    # 與 GitHub secret / Worker secret 同值（寫入型 secret，無法讀回，
#                     # 若忘了只能「輪換」：wrangler secret put + GitHub secret 各設新值）
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

WORKER_BASE="${PUSH_WORKER_URL:-https://airtickets-price-push.h770320.workers.dev}"
WORKER_NOTIFY="$WORKER_BASE/notify"
WORKER_PUBKEY="$WORKER_BASE/vapid-public-key"
KV_NAMESPACE_ID="c826435583dc465394ea302b19e38293"   # 與 worker/wrangler.toml 同步

say()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; }

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

check_worker() {
  say "① Worker 狀態"
  pubkey="$(curl -sf --max-time 10 "$WORKER_PUBKEY" || true)"
  if [[ -n "$pubkey" ]]; then
    ok "Worker 上線：$WORKER_PUBKEY"
    ok "VAPID 公鑰取得成功（${#pubkey} chars）"
  else
    fail "Worker 無法連線 / 公鑰端點異常：$WORKER_PUBKEY"
    return 1
  fi

  say "② 訂閱人數（Cloudflare KV）"
  if command -v wrangler >/dev/null 2>&1; then
    local subs
    subs="$(wrangler kv key list --namespace-id "$KV_NAMESPACE_ID" 2>/dev/null | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo '?')"
    if [[ "$subs" == "0" ]]; then
      warn "目前 0 筆訂閱 —— 推播會「成功但沒人收到」。請先開網頁點「開啟票價提醒」訂閱。"
    else
      ok "目前 $subs 筆訂閱"
    fi
  else
    warn "未安裝 wrangler，跳過訂閱人數檢查"
  fi
}

cmd_status() {
  check_worker || true
  say "提示：訂閱頁面 https://yuhaoliaosideproject.github.io/AirTicketsPrice/web/"
  say "      （iOS 需先「加到主畫面」且 iOS 16.4+；訂閱後等 ~60s KV 同步再 notify）"
}

cmd_notify() {
  check_worker

  if [[ -z "${PUSH_API_TOKEN:-}" ]]; then
    fail "缺少 PUSH_API_TOKEN（無法呼叫 /notify；secret 寫入後無法讀回，忘了就輪換重設）"
    return 1
  fi

  say "③ 從真實資料取 1 筆航班當測試 payload"
  payload="$(python3 - <<'PY'
import json, glob
files = sorted(glob.glob('data/2026*.json'))
records = []
for f in files:
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if isinstance(d, list):
        records.extend(d)
if not records:
    print('{"drops":[{"route":"TPE-NRT","outbound_date":"2026-08-22","return_date":"2026-08-30","flight_no":"JX 804","old_price":26008,"new_price":24120}]}')
    raise SystemExit(0)
r = min(records, key=lambda x: x.get('price_total', 10**9))
print(json.dumps({"drops": [{
    "route": r.get("route_id", "TPE-XXX"),
    "outbound_date": r.get("outbound_date", ""),
    "return_date": r.get("return_date", ""),
    "flight_no": r.get("outbound_flight_no", ""),
    "old_price": int(r.get("price_total", 0) * 1.1),
    "new_price": int(r.get("price_total", 0)),
}]}, ensure_ascii=False))
PY
)"
  ok "payload: $payload"

  say "④ 觸發 /notify"
  resp="$(curl -sS --max-time 15 -X POST "$WORKER_NOTIFY" \
    -H "Authorization: Bearer $PUSH_API_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "$payload")"
  echo "  HTTP 回應：$resp"
  if [[ "$resp" == *'"ok":true'* || "$resp" == *'"ok": true'* ]]; then
    ok "推播已送出 → 檢查手機/瀏覽器是否收到通知"
  else
    fail "推播失敗（見上方回應）"
  fi
}

case "${1:---notify}" in
  --status) cmd_status ;;
  --notify) cmd_notify ;;
  --help|-h) usage ;;
  *) usage ;;
esac
