#!/usr/bin/env python3
"""中華航空（CI）票價 API「覆蓋率」探測工具

⚠️ 這不是正式爬蟲，是探測腳本 —— 目的是回答「airtrfx histogram API 的票價覆蓋率夠不夠用」。
   不寫入 data/、不寫入 _site/，結果只印到 stdout/stderr（--json 時 stdout 只有 JSON）。

背景與實測依據：docs/research/ci-graphql-feasibility.md
  - 首頁 HTML 的 __NEXT_DATA__ 內嵌 em-api-key（64 chars，公開發給任何訪客）
  - POST airfare-sputnik-service/v3/ci/fares/histogram-distribution → 200 + 乾淨 JSON
  - 坑：fares/search 的 origin/destination 過濾無效 → 只用 histogram-distribution

用法：
    python scripts/probe_ci_coverage.py                 # 預設 N=8（未來 8 組週六去/週日回）
    python scripts/probe_ci_coverage.py --n 4           # 只探 4 組
    python scripts/probe_ci_coverage.py --json          # 摘要→stderr、結構化結果→stdout
    python scripts/probe_ci_coverage.py --start-date 2026-11-28 --n 3

請求紀律：預設總請求數 = 1（首頁）+ N（逐組）+ 1（區間對照）+ 1（token 重複使用）
          → N=8 時共 11 次。超過 --max-requests 直接中止；遇 429/403 立即停止，不繞過。
"""
import argparse
import json
import random
import re
import statistics
import sys
import time
from datetime import date, timedelta

import requests

# ---------------------------------------------------------------- 常數
HOME_URL = "https://flights.china-airlines.com/zh-tw/flights"
HIST_URL = (
    "https://openair-california.airtrfx.com"
    "/airfare-sputnik-service/v3/ci/fares/histogram-distribution"
)

# 首頁 HTML 內嵌的公開 token（與 config.py 的星宇設定無關，刻意不進 config）
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "user-agent": UA,
    "accept-language": "zh-TW,zh;q=0.9",
}
HIST_HEADERS = {
    **BASE_HEADERS,
    "content-type": "application/json",
    "origin": "https://flights.china-airlines.com",
    "referer": "https://flights.china-airlines.com/",
}

# 三種狀態（不可混為一談）
STATUS_PRICED = "有票價"
STATUS_EMPTY = "API 回 200 但無資料"
STATUS_FAILED = "請求失敗"

# 經濟艙來回 TWD 合理區間（TPE↔NRT）；超出視為異常值，僅記錄不丟棄
REASONABLE_MIN = 13000
REASONABLE_MAX = 25000

# 已知的「坑 1」驗證目標（首頁促銷卡聲稱的日期）
PITFALL_1_DEP = "2026-11-07"
PITFALL_1_RET = "2026-11-08"

JWT_RE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
KEY_RE = re.compile(r'"em-api-key"\s*:\s*"([^"]+)"')


# ---------------------------------------------------------------- 日期
def gen_saturday_pairs(reference: date, n: int) -> list[tuple[str, str]]:
    """未來 n 組 (週六去, 週日回)。今天已是週六 → 從下週六開始（與 fetch_prices 一致）。"""
    days_ahead = (5 - reference.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    first_sat = reference + timedelta(days=days_ahead)
    pairs = []
    for i in range(n):
        dep = first_sat + timedelta(weeks=i)
        ret = dep + timedelta(days=1)  # 週日回（1 夜）
        pairs.append((dep.isoformat(), ret.isoformat()))
    return pairs


# ---------------------------------------------------------------- 認證（token 快取）
class Credentials:
    """首頁 token 快取：同一輪只抓一次首頁；僅在 401/403 時重取（48h 效期）。"""

    def __init__(self, session: requests.Session, timeout: int):
        self.session = session
        self.timeout = timeout
        self.api_key: str | None = None
        self.jwt: str | None = None
        self.home_fetches = 0
        self.refreshes = 0

    def load(self, is_refresh: bool = False) -> None:
        self.home_fetches += 1
        if is_refresh:
            self.refreshes += 1
        resp = self.session.get(HOME_URL, headers=BASE_HEADERS, timeout=self.timeout)
        resp.raise_for_status()
        html = resp.text
        key_m = KEY_RE.search(html)
        if not key_m:
            raise RuntimeError("首頁 HTML 取不到 em-api-key（站方改版？）")
        self.api_key = key_m.group(1)
        jwt_m = JWT_RE.search(html)
        self.jwt = jwt_m.group(0) if jwt_m else None

    def masked_key(self) -> str:
        k = self.api_key or ""
        return f"{k[:4]}…{k[-4:]}" if len(k) > 8 else "(none)"


# ---------------------------------------------------------------- API 呼叫
def build_payload(origin: str, dest: str, start: str, end: str,
                  week_days: list[str] | None, nights: int) -> dict:
    payload = {
        "origin": origin,
        "destination": dest,
        "journeyType": "ROUND_TRIP",
        "travelClasses": ["ECONOMY"],
        "departure": {"start": start, "end": end},
        "tripDuration": {"minimum": nights, "maximum": nights},
        "priceBuckets": {"active": True, "priceStats": True, "field": "TOTAL_PRICE"},
        "outputFormat": {"datePattern": "YYYY-MM-DD"},
    }
    if week_days:
        payload["weekDays"] = week_days
    return payload


def query_histogram(session: requests.Session, creds: Credentials, payload: dict,
                    timeout: int, stats: dict) -> dict:
    """打一次 histogram-distribution。

    回傳 {"status": STATUS_*, "http": int|None, "body": dict|None, "error": str|None}
    → 明確區分「有票價」/「200 但無資料」/「請求失敗」，不把三者混成一團。
    """
    for attempt in range(2):
        stats["api_requests"] += 1
        try:
            resp = session.post(HIST_URL, headers={**HIST_HEADERS,
                                                   "em-api-key": creds.api_key},
                                json=payload, timeout=timeout)
        except Exception as e:  # noqa: BLE001  （timeout / DNS / connection）
            return {"status": STATUS_FAILED, "http": None, "body": None,
                    "error": f"{type(e).__name__}: {e}"}

        stats["http_codes"][str(resp.status_code)] = \
            stats["http_codes"].get(str(resp.status_code), 0) + 1

        # 429 / 403：站方壓力訊號 → 立刻放棄，不重試、不換 IP
        if resp.status_code in (429, 403):
            stats["aborted"] = f"HTTP {resp.status_code}（停止，不繞過）"
            return {"status": STATUS_FAILED, "http": resp.status_code, "body": None,
                    "error": f"HTTP {resp.status_code}（429/403 → 停止，不重試）"}

        # 401：token 可能失效 → 重取首頁一次後重試（48h 效期，這是預期行為）
        if resp.status_code == 401 and attempt == 0:
            stats["token_expired_events"].append("HTTP 401 → 重取首頁 token")
            creds.load(is_refresh=True)
            continue

        if not resp.ok:
            body = None
            try:
                body = resp.json()
            except ValueError:
                pass
            return {"status": STATUS_FAILED, "http": resp.status_code, "body": body,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        try:
            body = resp.json()
        except ValueError:
            return {"status": STATUS_FAILED, "http": resp.status_code, "body": None,
                    "error": "200 但非 JSON（schema 變動？）"}

        fares = extract_fares(body)
        return {"status": STATUS_PRICED if fares else STATUS_EMPTY,
                "http": resp.status_code, "body": body, "fares": fares,
                "error": None}

    return {"status": STATUS_FAILED, "http": 401, "body": None,
            "error": "401 重取 token 後仍失敗"}


def extract_fares(body: dict) -> list[dict]:
    """從 histogram 抽出所有非空 fares（一個 date 可能有多筆艙等價）。"""
    out = []
    for day in body.get("histogram") or []:
        for fare in day.get("fares") or []:
            spec = fare.get("priceSpecification") or {}
            ob = fare.get("outboundFlight") or {}
            out.append({
                "departure_date": day.get("date"),
                "return_date": fare.get("returnDate"),
                "price": spec.get("totalPrice"),
                "currency": spec.get("currencyCode"),
                "bucket": spec.get("bucket"),
                "cabin": ob.get("fareClassInput") or ob.get("fareClass"),
                "airline": (fare.get("airline") or {}).get("iataCode"),
            })
    return out


# ---------------------------------------------------------------- 合理性檢查
def price_sanity(prices: list[int]) -> dict:
    """價格合理性：0/1/位數錯（<1000 或 >200000）為硬異常；出合理區間為警示。"""
    if not prices:
        return {"min": None, "median": None, "max": None,
                "in_range": 0, "out_of_range": [], "hard_anomalies": []}
    hard = [p for p in prices if p is None or p < 1000 or p > 200000]
    out_of_range = [p for p in prices
                    if p is not None and 1000 <= p <= 200000
                    and not (REASONABLE_MIN <= p <= REASONABLE_MAX)]
    return {
        "min": min(prices),
        "median": int(statistics.median(prices)),
        "max": max(prices),
        "in_range": sum(1 for p in prices if p is not None
                        and REASONABLE_MIN <= p <= REASONABLE_MAX),
        "out_of_range": sorted(out_of_range),
        "hard_anomalies": sorted(hard),
        "reasonable_range": [REASONABLE_MIN, REASONABLE_MAX],
    }


# ---------------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser(
        description="中華航空票價 API 覆蓋率探測（不寫入 data/ 或 _site/）")
    parser.add_argument("--n", type=int, default=8,
                        help="探測幾組「週六去/週日回」（預設 8）")
    parser.add_argument("--origin", default="TPE", help="出發機場（預設 TPE）")
    parser.add_argument("--destination", default="NRT", help="目的機場（預設 NRT）")
    parser.add_argument("--start-date", help="第一組去程日期 YYYY-MM-DD（預設：下一個週六）")
    parser.add_argument("--json", action="store_true",
                        help="stdout 只印結構化 JSON（摘要改印到 stderr）")
    parser.add_argument("--no-range-check", action="store_true",
                        help="跳過區間對照查詢（省 1 次請求）")
    parser.add_argument("--no-token-reuse-test", action="store_true",
                        help="跳過 token 重複使用測試（省 1 次請求）")
    parser.add_argument("--sleep-min", type=float, default=2.0, help="每組間最小延遲秒（預設 2）")
    parser.add_argument("--sleep-max", type=float, default=4.0, help="每組間最大延遲秒（預設 4）")
    parser.add_argument("--timeout", type=int, default=30, help="單次請求 timeout 秒（預設 30）")
    parser.add_argument("--max-requests", type=int, default=15,
                        help="總請求數上限（預設 15，超過即中止）")
    args = parser.parse_args()

    # --json 時：摘要走 stderr，stdout 只留 JSON
    out = sys.stderr if args.json else sys.stdout

    def say(msg: str = "", end: str = "\n") -> None:
        print(msg, end=end, file=out, flush=True)

    if args.start_date:
        # --start-date 直接指定第一組去程（不強制週六，方便探測特定日期）
        first = date.fromisoformat(args.start_date)
        pairs = [((first + timedelta(weeks=i)).isoformat(),
                  (first + timedelta(weeks=i, days=1)).isoformat())
                 for i in range(args.n)]
    else:
        pairs = gen_saturday_pairs(date.today(), args.n)

    planned = 1 + len(pairs) + (0 if args.no_range_check else 1) \
        + (0 if args.no_token_reuse_test else 1)
    say(f"🔎 中華航空票價覆蓋率探測 {args.origin}→{args.destination}")
    say(f"   組數 N={len(pairs)}｜預計請求 {planned} 次（上限 {args.max_requests}）")
    for dep, ret in pairs:
        say(f"   {dep} (六) → {ret} (日)")

    stats = {
        "api_requests": 0,
        "http_codes": {},
        "token_expired_events": [],
        "aborted": None,
    }
    session = requests.Session()
    creds = Credentials(session, args.timeout)

    # ---- 1. 取首頁 token（整輪只抓一次）
    try:
        creds.load()
    except Exception as e:  # noqa: BLE001
        say(f"❌ 取首頁 token 失敗：{type(e).__name__}: {e}")
        say("   → 無法繼續（不重試、不換 IP）")
        return 1
    say(f"\n🔑 em-api-key={creds.masked_key()}（JWT {'有' if creds.jwt else '無'}，"
        f"首頁 GET {creds.home_fetches} 次）")

    # ---- 2. 逐組查詢
    groups: list[dict] = []
    for idx, (dep, ret) in enumerate(pairs):
        if stats["aborted"]:
            groups.append({"departure_date": dep, "return_date": ret,
                           "status": STATUS_FAILED, "http": None,
                           "error": "已中止（前一次 429/403）", "fares": []})
            continue
        if idx > 0:
            delay = random.uniform(args.sleep_min, args.sleep_max)
            say(f"⏳ 等待 {delay:.1f}s ...")
            time.sleep(delay)
        if stats["api_requests"] >= args.max_requests - 2:
            say(f"⚠️  已達請求上限（{args.max_requests}），停止逐組探測")
            break

        payload = build_payload(args.origin, args.destination, dep, dep, ["SAT"], 1)
        res = query_histogram(session, creds, payload, args.timeout, stats)
        fares = res.get("fares") or []
        prices = [f["price"] for f in fares if isinstance(f["price"], (int, float))]

        rec = {
            "departure_date": dep,
            "return_date": ret,
            "status": res["status"],
            "http": res["http"],
            "error": res.get("error"),
            "fare_count": len(fares),
            "prices": prices,
            # 空結果要留下「為什麼空」的證據（空陣列 / null / 錯誤碼）
            "raw_evidence": None if res["status"] == STATUS_PRICED else
            json.dumps(slim_body(res.get("body")), ensure_ascii=False)[:600],
            "fares": fares,
        }
        groups.append(rec)

        icon = {"有票價": "✅", "API 回 200 但無資料": "⚪"}.get(res["status"], "❌")
        detail = (f"TWD {min(prices):,}" if prices else
                  (res.get("error") or "fares 為空"))
        say(f"{icon} {dep}→{ret} [{res['status']}] {detail}")

    # ---- 3. 區間對照（驗證「單日查詢」是否為查詢形狀造成的假性空值）
    range_check = None
    if not args.no_range_check and not stats["aborted"] and pairs:
        first_dep, _ = pairs[0]
        last_dep, _ = pairs[-1]
        say(f"\n🔬 區間對照查詢 {first_dep}..{last_dep}（weekDays=SAT, 1 夜）")
        payload = build_payload(args.origin, args.destination, first_dep, last_dep,
                                ["SAT"], 1)
        res = query_histogram(session, creds, payload, args.timeout, stats)
        h = (res.get("body") or {}).get("histogram") or []
        hit = [d.get("date") for d in h if d.get("fares")]
        hit_prices = {d["date"]: min((f.get("priceSpecification") or {}).get("totalPrice", 0)
                                     or 0 for f in d["fares"]) for d in h if d.get("fares")}
        range_check = {
            "start": first_dep, "end": last_dep,
            "status": res["status"], "http": res["http"],
            "error": res.get("error"),
            "dates_in_histogram": len(h),
            "dates_with_fares": hit,
            "min_price_by_date": hit_prices,
        }
        per_group_empty = {g["departure_date"] for g in groups
                           if g["status"] == STATUS_EMPTY}
        range_only = sorted(set(hit) & per_group_empty)
        range_check["dates_priced_in_range_but_empty_per_group"] = range_only
        say(f"   histogram 涵蓋 {len(h)} 天，其中 {len(hit)} 天有票價：{hit or '無'}")
        if range_only:
            say(f"   ⚠️ 這些日期在區間查詢有價、在單日查詢為空 → 單日查詢不可靠：{range_only}")
        else:
            say("   ✅ 與逐組結果一致（單日查詢沒有假性空值）")

    # ---- 4. token 重複使用測試（同一 key 再打一次，看是否失效）
    reuse = None
    if not args.no_token_reuse_test and not stats["aborted"] and pairs:
        dep, ret = pairs[0]
        say(f"\n♻️  token 重用測試：同一把 key 再打 {dep}（第 {stats['api_requests'] + 1} 次）")
        time.sleep(random.uniform(args.sleep_min, args.sleep_max))
        payload = build_payload(args.origin, args.destination, dep, dep, ["SAT"], 1)
        res = query_histogram(session, creds, payload, args.timeout, stats)
        reuse = {
            "repeated_date": dep,
            "status": res["status"], "http": res["http"],
            "error": res.get("error"),
            "first_status": groups[0]["status"] if groups else None,
            "same_as_first": bool(groups) and res["status"] == groups[0]["status"],
            "home_fetches": creds.home_fetches,
            "token_refreshes": creds.refreshes,
            "http_codes_total": dict(stats["http_codes"]),
        }
        say(f"   狀態 {res['status']}（HTTP {res['http']}）；"
            f"首頁共抓 {creds.home_fetches} 次、token 重取 {creds.refreshes} 次")

    # ---- 5. 統計
    priced = [g for g in groups if g["status"] == STATUS_PRICED]
    empty = [g for g in groups if g["status"] == STATUS_EMPTY]
    failed = [g for g in groups if g["status"] == STATUS_FAILED]
    all_prices = [p for g in priced for p in g["prices"]]
    sanity = price_sanity(all_prices)
    total = len(groups) or 1

    pitfall = next((g for g in groups
                    if g["departure_date"] == PITFALL_1_DEP
                    and g["return_date"] == PITFALL_1_RET), None)

    summary = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "route": f"{args.origin}-{args.destination}",
        "n_groups": len(groups),
        "coverage": {
            "priced": len(priced),
            "empty": len(empty),
            "failed": len(failed),
            "priced_ratio": round(len(priced) / total, 3),
            "empty_ratio": round(len(empty) / total, 3),
            "failed_ratio": round(len(failed) / total, 3),
        },
        "price_sanity": sanity,
        "pitfall_1": {
            "target": f"{PITFALL_1_DEP}→{PITFALL_1_RET}",
            "tested": pitfall is not None,
            "status": pitfall["status"] if pitfall else None,
            "prices": pitfall["prices"] if pitfall else None,
            "raw_evidence": pitfall["raw_evidence"] if pitfall else None,
            "homepage_promo_claim_twd": 14676,
            "verdict": (
                "成立（API 查無該日期票價，與首頁促銷卡不一致）"
                if pitfall and pitfall["status"] == STATUS_EMPTY else
                "不成立（查得到票價）" if pitfall and pitfall["status"] == STATUS_PRICED else
                "無法判定（該日期未被探測或請求失敗）"
            ),
        },
        "range_check": range_check,
        "token_reuse": reuse,
        "requests": {
            "home_fetches": creds.home_fetches,
            "api_requests": stats["api_requests"],
            "total": creds.home_fetches + stats["api_requests"],
            "max_allowed": args.max_requests,
            "http_codes": dict(stats["http_codes"]),
            "token_expired_events": stats["token_expired_events"],
            "aborted": stats["aborted"],
        },
        "groups": groups,
    }

    # ---- 6. 終端摘要
    say("\n" + "=" * 66)
    say(f"📊 覆蓋率：{len(priced)}/{len(groups)} 有票價 "
        f"({summary['coverage']['priced_ratio']:.0%})｜"
        f"{len(empty)} 查無 ({summary['coverage']['empty_ratio']:.0%})｜"
        f"{len(failed)} 失敗 ({summary['coverage']['failed_ratio']:.0%})")
    say("=" * 66)
    say(f"{'去程':<12}{'回程':<12}{'狀態':<22}{'價格'}")
    for g in groups:
        price = f"TWD {min(g['prices']):,}" if g["prices"] else "-"
        say(f"{g['departure_date']:<12}{g['return_date']:<12}{g['status']:<22}{price}")

    if all_prices:
        say(f"\n💰 價格：min {sanity['min']:,} / median {sanity['median']:,} / "
            f"max {sanity['max']:,} TWD")
        say(f"   合理區間 {REASONABLE_MIN:,}–{REASONABLE_MAX:,}："
            f"{sanity['in_range']}/{len(all_prices)} 筆在區間內")
        if sanity["out_of_range"]:
            say(f"   ⚠️ 區間外：{sanity['out_of_range']}")
        if sanity["hard_anomalies"]:
            say(f"   ❌ 硬異常（0/1/位數錯）：{sanity['hard_anomalies']}")
    else:
        say("\n💰 沒有任何一組取得票價")

    p = summary["pitfall_1"]
    say(f"\n🕳️  坑 1（{p['target']}，首頁促銷卡稱 TWD {p['homepage_promo_claim_twd']:,}）：")
    say(f"   探測狀態 {p['status']}｜判定：{p['verdict']}")
    if p["raw_evidence"]:
        say(f"   原始證據：{p['raw_evidence'][:300]}")

    if reuse:
        say(f"\n♻️  token 重用：{reuse['first_status']} → {reuse['status']} "
            f"（首頁 {reuse['home_fetches']} 次、重取 {reuse['token_refreshes']} 次）")

    r = summary["requests"]
    say(f"\n📡 請求：首頁 {r['home_fetches']} + API {r['api_requests']} = {r['total']} 次"
        f"（上限 {r['max_allowed']}）｜HTTP {r['http_codes']}")
    if r["token_expired_events"]:
        say(f"   ⚠️ token 失效事件：{r['token_expired_events']}")
    if r["aborted"]:
        say(f"   ❌ 已中止：{r['aborted']}")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 退出碼：有失敗或全滅 → 1；純覆蓋率低不算錯誤（探測目的就是量它）
    return 1 if (failed or not priced) else 0


def slim_body(body: dict | None) -> dict:
    """只留證據欄位，避免整包 location 圖檔 URL 蓋掉重點。"""
    if not isinstance(body, dict):
        return {"body": body}
    hist = body.get("histogram") or []
    buckets = body.get("buckets") or {}
    return {
        "histogram": [{"date": d.get("date"),
                       "fares_len": len(d.get("fares") or [])} for d in hist],
        "buckets_total_results": (buckets.get("avg") or {}).get("totalResults"),
        "top_level_keys": sorted(body.keys()),
    }


if __name__ == "__main__":
    sys.exit(main())
