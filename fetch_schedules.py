#!/usr/bin/env python3
"""中華航空（CI）TPE ↔ NRT 班表抓取器

來源：`info.flightmapper.net` 的航線排班頁（伺服器端渲染 HTML，純 requests 即可）。
規格與實測證據見 `docs/research/ci-automation-alternatives.md` §A.2；
ground truth 見 `docs/research/ci-tpe-nrt-schedule.md`。

本檔只處理 **TPE↔NRT** 一條航線（預設正反兩方向，共 2 個 GET）。

用法：
    python fetch_schedules.py                        # 抓兩個方向，寫入 schedules/
    python fetch_schedules.py --json                 # 結構化結果印到 stdout（進度訊息走 stderr）
    python fetch_schedules.py --dry-run              # 只抓取／解析，不寫檔
    python fetch_schedules.py --routes TPE-NRT       # 只抓單一方向
    python fetch_schedules.py --date 2026-10-31      # 指定參考日期（決定哪個生效區段算數）

輸出：schedules/ci_tpe_nrt_schedule.json

⚠️ 刻意不寫入 data/：`build_api.py` 只掃 `data/*.json`，寫進去會被既有價格 pipeline
   當成票價紀錄吃進 api/，造成非預期影響。`schedules/` 不被任何既有程式或 workflow 讀取。

⚠️ 誠實標記：本來源（FlightMapper）**已知會缺筆**（漏營運日，例如 CI104/CI105
   冬季缺 Sat，但官方 FIDS 證實實際有飛）。程式**不補值**，只在每筆航班與檔案頂層
   標上 `days_unverified: true` + `unverified_reason`，並在終端機印出警告。
   見 `schedules/README.md`、`docs/research/ci104-ci105-saturday-check.md`。
"""
import argparse
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------- 設定
SOURCE_URL = "https://info.flightmapper.net/route/China_Airlines_CI_{origin}_{destination}"

# 只支援 TPE↔NRT（本階段刻意不擴大）
DEFAULT_ROUTES = ["TPE-NRT", "NRT-TPE"]
SUPPORTED_ROUTES = {"TPE-NRT": ("TPE", "NRT"), "NRT-TPE": ("NRT", "TPE")}

# 輸出目錄（刻意獨立於 data/，見模組 docstring）
OUTPUT_DIR = "schedules"
OUTPUT_FILENAME = "ci_tpe_nrt_schedule.json"

# User-Agent 池：沿用 fetch_prices.py 的做法，每次請求隨機挑一個
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://info.flightmapper.net/",
}

# 請求紀律：單次執行上限 4 個 GET（本航線只需 2 個）
MAX_REQUESTS = 4

# 請求間隨機延遲（秒）
REQUEST_DELAY_MIN = 2.0
REQUEST_DELAY_MAX = 4.0

# 重試設定：失敗最多再試 RETRIES 次（初始 1 次 + 重試 2 次 = 3 次嘗試）
RETRIES = 2
RETRY_BACKOFF = 3  # 秒（指數退避：3s, 6s）

# 星期代碼（頁面用英文縮寫，固定 Mon..Sun 順序）
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# 排班頁不是「定期客運航班」的標記（包機、共掛由他航執飛），一律排除
NON_REGULAR_MARKERS = ("Non-scheduled", "Codeshare flight")

# ---------------------------------------------------------------- 誠實標記
# 上游 FlightMapper 已知會「缺筆」（漏掉部分營運日），且無法從其資料內部察覺。
# 本程式**不補值、不猜測**，只把「這些營運日未經交叉驗證」明確標示出來。
# 證據：docs/research/ci104-ci105-saturday-check.md
DAYS_UNVERIFIED_REASON = (
    "營運日只來自單一來源 FlightMapper，未經第二來源交叉驗證。"
    "該來源已知會缺筆：CI104 / CI105 冬季營運日缺 Sat，"
    "但桃園機場官方 FIDS 於 2026-10-31（週六）實證 CI104 確實有飛"
    "（OTime 12:35、RTime 12:42、Gate D5），營運日實為每日。"
    "其他航班也可能有尚未發現的缺筆，因此 days_of_operation 僅供參考，"
    "不可視為完整或已確認的清單。"
)


def mark_days_unverified(flights: list[dict]) -> list[dict]:
    """為每個航班蓋上「營運日未經交叉驗證」的標記（不改動任何數值）。

    刻意標記**全部**航班，而不是只標已知缺筆的 CI104/CI105：
    缺筆在來源資料內部是自洽的（看不出矛盾），所以無法判斷「哪些班是乾淨的」。
    只標已知個案會讓其他航班被誤認為已驗證。
    """
    for f in flights:
        f["days_unverified"] = True
        f["unverified_reason"] = DAYS_UNVERIFIED_REASON
    return flights


# ---------------------------------------------------------------- 工具
def log(msg: str) -> None:
    """進度訊息一律走 stderr，讓 --json 的 stdout 保持乾淨。"""
    print(msg, file=sys.stderr)


def taipei_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def source_url(route_id: str) -> str:
    origin, destination = SUPPORTED_ROUTES[route_id]
    return SOURCE_URL.format(origin=origin, destination=destination)


# ---------------------------------------------------------------- 抓取
def fetch_html(session: requests.Session, url: str) -> str:
    """GET 一頁排班表，失敗重試（最多 RETRIES 次），最終失敗拋例外。"""
    last_err = None
    for attempt in range(RETRIES + 1):
        try:
            resp = session.get(
                url,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=30,
            )
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = "utf-8"
            html = resp.text
            if "FLIGHT SCHEDULE" not in html:
                raise RuntimeError("回應中找不到排班表區塊（版面可能已改版）")
            return html
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < RETRIES:
                delay = RETRY_BACKOFF * (attempt + 1)
                log(f"⚠️  {url} 第 {attempt + 1} 次失敗（{e}），{delay}s 後重試")
                time.sleep(delay)
    raise RuntimeError(f"抓取 {url} 失敗（已重試 {RETRIES} 次）: {last_err}")


# ---------------------------------------------------------------- 解析
def strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return text


def parse_period(info_text: str) -> tuple[str | None, str | None] | None:
    """把 m-infobox 文字轉成 (effective_from, effective_to)。

    支援頁面上實際出現的四種寫法：
      Effective from YYYY-MM-DD                    → (start, None)
      Effective YYYY-MM-DD through YYYY-MM-DD      → (start, end)
      Valid until YYYY-MM-DD                       → (None, end)
      Operates only on YYYY-MM-DD                  → (d, d)
    """
    m = re.search(r"Effective from (\d{4}-\d{2}-\d{2})", info_text)
    if m:
        return m.group(1), None
    m = re.search(r"Effective (\d{4}-\d{2}-\d{2}) through (\d{4}-\d{2}-\d{2})", info_text)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"Valid until (\d{4}-\d{2}-\d{2})", info_text)
    if m:
        return None, m.group(1)
    m = re.search(r"Operates only on (\d{4}-\d{2}-\d{2})", info_text)
    if m:
        return m.group(1), m.group(1)
    return None


def parse_aircraft(detail_html: str) -> str | None:
    """從 m-detailbox 取機型（如 "Airbus A330-300 (333)"）。"""
    text = strip_tags(detail_html)
    for line in text.splitlines():
        line = " ".join(line.split())
        if re.search(r"\(\w+\)$", line) and not line.startswith(("Duration", "Codeshare")):
            return line
    return None


def is_active(start: str | None, end: str | None, ref: date) -> bool:
    """區段是否在參考日期當天生效（含頭尾）。"""
    if start and ref < date.fromisoformat(start):
        return False
    if end and ref > date.fromisoformat(end):
        return False
    return True


def parse_route_html(html: str, route_id: str, ref: date,
                     stats: dict | None = None) -> list[dict]:
    """解析一頁排班表 HTML，回傳在 ref 當天生效的航班（同一班號已聯集）。

    純函式：不發網路請求，測試直接餵 fixture。
    stats（可選）會被填入 {"blocks", "skipped_irregular", "inactive"} 供 logging 用。
    """
    if stats is None:
        stats = {}
    stats.setdefault("blocks", 0)
    stats.setdefault("skipped_irregular", 0)
    stats.setdefault("inactive", 0)

    origin, destination = SUPPORTED_ROUTES[route_id]
    blocks = html.split('<div class="m-coll">')[1:]
    stats["blocks"] += len(blocks)

    by_flight: dict[str, list[dict]] = {}

    for block in blocks:
        # 排除包機與他航執飛的共掛班號（CI2100/CI2108/CI9902 等噪音）
        if any(marker in block for marker in NON_REGULAR_MARKERS):
            stats["skipped_irregular"] += 1
            continue

        m = re.search(r'href="/flight/[^"]*">(CI\d+)</a>', block)
        if not m:
            stats["skipped_irregular"] += 1
            continue
        flight_number = m.group(1)

        times = re.findall(r'class="m-time">(\d{2}:\d{2})</div>', block)
        airports = re.findall(r'<a href="/airport/([A-Z]{3})">', block)
        if len(times) < 2 or len(airports) < 2:
            raise RuntimeError(f"{route_id} {flight_number} 區段缺少時刻或機場，版面可能已改版")
        departure_time, arrival_time = times[0], times[1]
        block_origin, block_destination = airports[0], airports[1]

        info_m = re.search(r'class="m-infobox">(.*?)</div>', block, re.S)
        info_text = " ".join(strip_tags(info_m.group(1)).split()) if info_m else ""
        period = parse_period(info_text)
        if period is None:
            raise RuntimeError(
                f"{route_id} {flight_number} 無法解析生效期間（m-infobox={info_text!r}）")
        start, end = period

        if not is_active(start, end, ref):
            stats["inactive"] += 1
            continue

        days = [d for d, cell in
                re.findall(r'class="m-weekday">(\w{3})<br>(.*?)</div>', block, re.S)
                if "icon-ok" in cell]
        days = [d for d in WEEKDAYS if d in days]  # 固定 Mon..Sun 順序

        detail_m = re.search(r'class="m-detailbox">(.*?)</span>', block, re.S)
        aircraft = parse_aircraft(detail_m.group(1)) if detail_m else None

        by_flight.setdefault(flight_number, []).append({
            "effective_from": start,
            "effective_to": end,
            "days_of_operation": days,
            "departure_time": departure_time,
            "arrival_time": arrival_time,
            "aircraft": aircraft,
            "origin": block_origin,
            "destination": block_destination,
        })

    flights = []
    for flight_number, segments in by_flight.items():
        if not segments:
            continue
        # 分段重疊：營運日取聯集（多段區段各自的有效期限內都算同一班）
        union_days = [d for d in WEEKDAYS
                      if any(d in seg["days_of_operation"] for seg in segments)]
        starts = [s["effective_from"] for s in segments if s["effective_from"]]
        ends = [s["effective_to"] for s in segments]
        # 只要有一段是 open-ended（Effective from），整班的 effective_to 就是 None
        effective_to = None if any(e is None for e in ends) else max(ends)
        effective_from = min(starts) if starts else None

        # 時刻若在生效區段間變動，以「最晚開始的那一段」為準，並記入警告
        segments_sorted = sorted(segments, key=lambda s: s["effective_from"] or "")
        canonical = segments_sorted[-1]
        variants = sorted({(s["departure_time"], s["arrival_time"]) for s in segments})
        if len(variants) > 1:
            log(f"⚠️  {route_id} {flight_number} 生效期間內時刻不一致 {variants}，"
                f"採 {canonical['departure_time']}→{canonical['arrival_time']}")

        aircraft = []
        for seg in segments_sorted:
            if seg["aircraft"] and seg["aircraft"] not in aircraft:
                aircraft.append(seg["aircraft"])

        flights.append({
            "flight_number": flight_number,
            "origin": canonical["origin"],
            "destination": canonical["destination"],
            "departure_time": canonical["departure_time"],
            "arrival_time": canonical["arrival_time"],
            "aircraft": aircraft,
            "days_of_operation": union_days,
            "is_daily": len(union_days) == len(WEEKDAYS),
            "effective_from": effective_from,
            "effective_to": effective_to,
            "segments": [
                {
                    "effective_from": s["effective_from"],
                    "effective_to": s["effective_to"],
                    "days_of_operation": s["days_of_operation"],
                    "departure_time": s["departure_time"],
                    "arrival_time": s["arrival_time"],
                    "aircraft": s["aircraft"],
                }
                for s in segments_sorted
            ],
        })

    if not flights:
        raise RuntimeError(
            f"{route_id} 在 {ref} 沒有任何生效航班（解析 {stats['blocks']} 個區段，"
            f"其中 {stats['inactive']} 段不在有效期內）——不視為空結果")
    return mark_days_unverified(
        sorted(flights, key=lambda f: (f["departure_time"], f["flight_number"])))


# ---------------------------------------------------------------- 主流程
def parse_routes_arg(raw: str) -> list[str]:
    routes = [r.strip().upper() for r in raw.split(",") if r.strip()]
    unknown = [r for r in routes if r not in SUPPORTED_ROUTES]
    if unknown:
        raise SystemExit(
            f"❌ 不支援的航線 {unknown}；本階段僅支援 {sorted(SUPPORTED_ROUTES)}")
    if len(routes) > MAX_REQUESTS:
        raise SystemExit(f"❌ 一次最多 {MAX_REQUESTS} 條航線（請求紀律）")
    return routes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="中華航空 TPE↔NRT 班表抓取器（來源：info.flightmapper.net）")
    parser.add_argument("--routes", default=",".join(DEFAULT_ROUTES),
                        help=f"逗號分隔的航線，預設 {','.join(DEFAULT_ROUTES)}"
                             f"（僅支援 {sorted(SUPPORTED_ROUTES)}）")
    parser.add_argument("--date", help="參考日期 YYYY-MM-DD（決定哪個生效區段算數，預設今天）")
    parser.add_argument("--json", action="store_true", help="把結構化結果印到 stdout")
    parser.add_argument("--out", help=f"輸出路徑（預設 {OUTPUT_DIR}/{OUTPUT_FILENAME}）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取／解析，不寫檔")
    args = parser.parse_args()

    routes = parse_routes_arg(args.routes)
    ref = date.fromisoformat(args.date) if args.date else taipei_today()
    out_path = Path(args.out) if args.out else Path(OUTPUT_DIR) / OUTPUT_FILENAME
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # 保護：避免誤寫進既有價格 pipeline 會吃的路徑
    try:
        out_path.resolve().relative_to(Path("data").resolve())
        raise SystemExit("❌ 輸出路徑不可位於 data/（會被 build_api.py 吃進票價 API）")
    except ValueError:
        pass

    log(f"📅 參考日期 {ref}（{ref.strftime('%A')}），航線 {routes}，"
        f"預計 {len(routes)} 個 GET")

    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    results = []
    failed = 0
    for idx, route_id in enumerate(routes):
        if idx > 0:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            log(f"⏳ 等待 {delay:.1f}s ...")
            time.sleep(delay)
        url = source_url(route_id)
        stats: dict = {}
        try:
            html = fetch_html(session, url)
            flights = parse_route_html(html, route_id, ref, stats)
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"❌ {route_id}: {e}")
            continue
        log(f"✅ {route_id}: {len(flights)} 班 "
            f"{[f['flight_number'] for f in flights]}"
            f"（解析 {stats['blocks']} 段，排除非定期 {stats['skipped_irregular']}、"
            f"非本期間 {stats['inactive']}）")
        results.append({
            "route_id": route_id,
            "origin": SUPPORTED_ROUTES[route_id][0],
            "destination": SUPPORTED_ROUTES[route_id][1],
            "source_url": url,
            "flights": flights,
        })

    if failed:
        log(f"❌ {failed}/{len(routes)} 條航線失敗（不寫入不完整結果）")
        return 1

    payload = {
        "generated_at": scraped_at,
        "source": "flightmapper",
        "airline_code": "CI",
        "airline_name": "China Airlines",
        "reference_date": ref.isoformat(),
        "request_count": len(routes),
        # 頂層也標一次：只讀頂層的消費者（或不看單班欄位的人）同樣看得到警語
        "days_unverified": True,
        "unverified_reason": DAYS_UNVERIFIED_REASON,
        "routes": results,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for route in results:
            log(f"\n✈️  {route['route_id']} ({route['source_url']})")
            for f in route["flights"]:
                days = "每日" if f["is_daily"] else ",".join(f["days_of_operation"])
                log(f"   {f['flight_number']}  {f['departure_time']} → {f['arrival_time']}  "
                    f"{days}  從 {f['effective_from']}  "
                    f"{'/'.join(f['aircraft']) or '機型未知'}")

        # 誠實揭露：務必讓人看到，不能只藏在 JSON 裡
        log("\n" + "=" * 78)
        log("⚠️  營運日未經交叉驗證（days_unverified: true）")
        log("    上述 days_of_operation 直接照抄單一來源 FlightMapper，**未補值**。")
        log("    已知該來源會缺筆：CI104/CI105 冬季缺 Sat，但桃園機場官方 FIDS")
        log("    於 2026-10-31（週六）實證 CI104 實際有飛（12:35 起飛、Gate D5）。")
        log("    其他航班也可能有未知缺筆 → 營運日僅供參考，勿當成已驗證事實。")
        log("    詳見 docs/research/ci104-ci105-saturday-check.md、schedules/README.md")
        log("=" * 78)

    if args.dry_run:
        log("\n🔍 dry-run：不寫檔")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"\n📦 寫入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
