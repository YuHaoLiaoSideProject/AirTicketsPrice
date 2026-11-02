#!/usr/bin/env python3
"""中華航空（CI）TPE → NRT 逐班票價抓取器（單一航線、本地手動執行）

來源與完整實測證據見 `docs/research/ci-deeplink-api-feasibility.md`。
本檔只做 **TPE → NRT 單程方向、單一日期（或日期區間）** 的逐班票價查詢，
預設 **不寫入 `data/`**（`build_api.py` 只 glob `data/*.json`，寫進去會被既有價格
pipeline 當成星宇票價紀錄吃進 `api/`）。輸出目錄為 `ci_fares/`。

架構（混合式：一次瀏覽器 + 之後純 HTTP）
------------------------------------------------
    ① 冷啟動：Playwright 走一次真實官網 UI（flights.china-airlines.com →「現在預訂」）
       → 瀏覽器自行通過 Imperva / Akamai，落到 des-portal
       → 從瀏覽器自己的網路流量取出 cookie / `x-d-token` / `privateFacts` / `fact` 樣板
       → 落地成 session 快取（含到期時間，TTL 20 分鐘；實測可重用 ≥25 分鐘）
    ② 暖查詢（純 requests，每個日期 2 個請求）：
       POST api-des…/v1/security/oauth2/token/initialization → Bearer
       POST api-des…/v2/search/air-bounds → 逐班 × 艙等 × 艙等代碼 × quota × 含稅總價

三態分離（嚴格，不可混用）
------------------------------------------------
    `ok`      ：HTTP 200 且解析到至少一個航班票價
    `no_data` ：HTTP 200 但沒有 airBoundGroups/airBounds（＝查無，**不是** 0 元）
    `failed`  ：4xx/5xx/timeout/解析失敗（保留 HTTP status 與錯誤訊息）

誠實標記
------------------------------------------------
- 這是**華航官網前台自己的 API**（前端明碼 client_id/secret + 必須由真實瀏覽器通過的
  Imperva/DataDome）。屬持續性自動化存取，**很可能違反服務條款**；本工具刻意
  **不接排程、不高頻、不快取轉售**，只供人工低頻取樣／對照。
- cookie 可重用時間是**實測值（≥25 分鐘）而非保證**；本檔保守用 20 分鐘。
- 被 403/429（DataDome captcha / Imperva）時**立即停止**，不重試、不繞過、
  不偽造指紋、不用代理換 IP。

用法
------------------------------------------------
    python3 fetch_ci_fares.py --date 2026-11-20
    python3 fetch_ci_fares.py --start-date 2026-11-20 --end-date 2026-11-24
    python3 fetch_ci_fares.py --date 2026-11-20 --json
    python3 fetch_ci_fares.py --date 2026-11-20 --dry-run
    python3 fetch_ci_fares.py --date 2026-11-20 --refresh-cookie

無 DISPLAY 的機器請用 xvfb：`xvfb-run -a python3 fetch_ci_fares.py --date 2026-11-20`
`--dry-run` 是**純本地檢查**：不開瀏覽器、不發任何 HTTP 請求、不寫檔；
沒有可用 session 快取時會明確報錯（exit 1），請先正常執行一次留下快取。

請求紀律（P1-1 / P1-2 / P1-3）
------------------------------------------------
- **所有對外 HTTP 請求之間**都隨機等待 2–4 秒（`--sleep-min` / `--sleep-max` 可調）：
  同日 token → air-bounds、每次重試之前、401 重新武裝之後、不同日期之間。
  讀快取／寫檔等本地操作**不**延遲，`--dry-run` 因為不發請求所以也不延遲。
- 只有 **5xx / timeout / 連線錯誤**才重試（最多 2 次、退避 3s/6s）；
  400/404/422 及其餘 4xx（401/403/429 另有專門處理）是**終止性錯誤**：
  立即停止該日期、不重試。
- `--max-requests` 用盡 → **立即中止整輪**（exit 7），剩餘日期標記為
  「因請求上限中止、未執行」（與 403/429 中止用不同原因字串）。

結束碼（exit code）語意
------------------------------------------------
    0  EXIT_OK           全部日期皆為 `ok` 或 `no_data`（完整成功）
    1  EXIT_ENV          環境／IO 錯誤（playwright/chromium 不可用、冷啟動失敗、
                         session 快取或輸出檔寫入失敗、--dry-run 沒有可用快取）
    2  EXIT_USAGE        參數錯誤（日期格式、互斥參數、不支援航線、輸出路徑等）
    3  EXIT_ALL_FAILED   所有查詢日期皆為 `failed`（非被攔截的中止）
    4  EXIT_PARTIAL      部分日期失敗（至少一個 `ok`/`no_data` 也有 `failed`）
    5  EXIT_ABORTED      被反爬攔截（403/429）而中止整輪，或有日期因此未查
    6  EXIT_UNEXPECTED   未預期例外（本工具的 bug 或未涵蓋的環境問題）
    7  EXIT_BUDGET_ABORT 達到 `--max-requests` 上限而中止整輪（剩餘日期未執行）

輸出安全
------------------------------------------------
- 輸出檔採原子寫入（先寫 `*.tmp` 再 `os.replace()`），中途失敗不會留下半截 JSON。
- 本輪若有任何 `failed` 或被中止，**不會覆寫**既有含可用資料（`ok`/`no_data`）的輸出檔，
  舊檔原樣保留（摘要會提示「保留上一輪資料」）。
- 正常覆寫前會把上一版備份成 `<name>.bak.json`，並在新檔的 `previous` 欄位記錄舊版摘要。

輸出：`ci_fares/TPE-NRT_2026-11-20.json`
（session 快取與瀏覽器 profile 放在 `~/.cache/ci_fares/`，可用 `CI_FARES_CACHE_DIR` 覆寫）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

import requests

# ---------------------------------------------------------------- 設定
#: 只允許「已實測驗證」的航線（本階段刻意不擴大；見規格書 §五）
SUPPORTED_ROUTES = {"TPE-NRT": ("TPE", "NRT")}
DEFAULT_ROUTE = "TPE-NRT"

BOOKINGPORTAL_SEARCH_URL = (
    "https://bookingportal.china-airlines.com/eRetailPortal/Booking.svc/Booking/Search")
DES_PORTAL_URL = "https://des-portal.china-airlines.com/booking?lang=zh-Hant"
TOKEN_URL = "https://api-des.china-airlines.com/v1/security/oauth2/token/initialization"
AIR_BOUNDS_URL = "https://api-des.china-airlines.com/v2/search/air-bounds"

#: 前端明碼（來自官網 bundle；換版就可能輪替 → 取不到 token 就停下，不硬重試）
CLIENT_ID = "r7UuPcZTW4PK5XWFt2swQtoSC1HBGhxc"
CLIENT_SECRET = "7il3cPVgbyttXH3a"
GRANT_TYPE = "client_credentials"

#: 官網前台預設的三個票價家族（實測值）
FARE_FAMILIES = ["ECONEW", "PREMIUMECO", "BZCLASS"]

FLIGHTS_HOME_URL = "https://flights.china-airlines.com/zh-tw"
WWW_HOME_URL = "https://www.china-airlines.com/tw/zh"

#: 輸出目錄（刻意獨立於 data/，見模組 docstring）
OUTPUT_DIR = "ci_fares"

#: session 快取 / 瀏覽器 profile（放在 repo 之外，避免把 cookie 與 100MB profile 塞進版本庫）
CACHE_DIR = Path(os.environ.get("CI_FARES_CACHE_DIR", Path.home() / ".cache" / "ci_fares"))
SESSION_FILENAME = "ci_session.json"
PROFILE_DIRNAME = "browser_profile"

#: 快取 TTL：實測 ≥25 分鐘可重用，這裡保守取 20 分鐘
COOKIE_TTL_SECONDS = 20 * 60
COOKIE_TTL_OBSERVED_MINUTES = 25

#: 請求紀律
REQUEST_DELAY_MIN = 2.0          # 不同日期之間隨機延遲（秒）
REQUEST_DELAY_MAX = 4.0
RETRIES = 2                      # 失敗最多再試 2 次（共 3 次嘗試）
RETRY_BACKOFF = 3                # 秒（指數退避 3s / 6s）
DEFAULT_MAX_REQUESTS = 30        # 單次執行 HTTP 請求上限（暖查詢；瀏覽器流量另計）
HTTP_TIMEOUT = 30

#: 403 / 429 = 反爬攔截 → 立即停止，不退避硬闖（規格書 §4.3）
BLOCK_STATUSES = {403, 429}

#: 三態（機器值 / 人類可讀標籤）
STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_FAILED = "failed"
STATUS_LABELS = {
    STATUS_OK: "有價",
    STATUS_NO_DATA: "API 回 200 但無資料",
    STATUS_FAILED: "請求失敗",
}

#: 中止原因（機器可判讀；不同原因用不同字串，下游不必比對中文 error）
ABORT_REASON_BLOCKED = "blocked"          # 403/429 反爬攔截
ABORT_REASON_BUDGET = "budget_exhausted"  # --max-requests 用盡
ABORT_REASON_ERROR = "error"              # 防禦性：query_leg 拋出未預期 RuntimeError

#: `queries[]` 每個日期物件的欄位集合（**所有狀態一律相同**，缺值用 null）
QUERY_RESULT_KEYS = (
    "query_date", "return_leg_date", "route_id",
    "status", "status_label",
    "flights", "flight_count", "fare_count",
    "error", "http_status", "raw_bounds_count",
    "parse_stats", "evidence", "summary",
    "aborted", "abort_reason", "blocked", "budget_exhausted",
)


def make_query_result(query_date: date, route_id: str, *,
                      status: str = STATUS_FAILED,
                      error: str | None = None) -> dict:
    """建立 **schema 一致** 的查詢結果物件（P1-5）。

    四種狀態（`ok` / `no_data` / `failed` / 中止）的 key 集合完全相同，
    取不到的值一律填 `null` 而不是省略 key → 下游固定 schema 解析不會 KeyError。
    """
    return {
        "query_date": query_date.isoformat(),
        "return_leg_date": (query_date + timedelta(days=RETURN_LEG_DAYS)).isoformat(),
        "route_id": route_id,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "flights": [],
        "flight_count": 0,
        "fare_count": 0,
        "error": error,
        "http_status": None,
        "raw_bounds_count": None,
        "parse_stats": None,
        "evidence": None,
        "summary": None,
        "aborted": False,
        "abort_reason": None,
        "blocked": False,
        "budget_exhausted": False,
    }


def make_aborted_result(query_date: date, route_id: str,
                        reason: str, message: str) -> dict:
    """「未執行」的日期（整輪已中止）：schema 與其他狀態完全一致。"""
    result = make_query_result(query_date, route_id, error=message)
    result["aborted"] = True
    result["abort_reason"] = reason
    if reason == ABORT_REASON_BLOCKED:
        result["blocked"] = True
    elif reason == ABORT_REASON_BUDGET:
        result["budget_exhausted"] = True
    return result


def abort_message_for(reason: str, max_requests: int | None = None) -> str:
    """中止原因 → 給「未執行日期」與摘要的錯誤字串（兩種原因刻意用不同字串）。"""
    if reason == ABORT_REASON_BUDGET:
        return f"因請求上限中止：--max-requests {max_requests} 已用盡，其餘日期未執行"
    if reason == ABORT_REASON_BLOCKED:
        return "先前的查詢被反爬攔截（403/429），依規則不繼續硬闖；其餘日期未執行"
    return "前一個日期發生未預期錯誤，其餘日期未執行"

#: 結束碼語意（見模組 docstring 與 README；--help 亦列出）
EXIT_OK = 0            # 全部日期皆為 ok 或 no_data
EXIT_ENV = 1           # 環境／IO 錯誤（playwright/chromium/冷啟動/寫檔）
EXIT_USAGE = 2         # 參數錯誤
EXIT_ALL_FAILED = 3    # 全部日期 failed
EXIT_PARTIAL = 4       # 部分日期 failed（也有成功/查無）
EXIT_ABORTED = 5       # 被反爬中止（403/429）或有日期因此未查
EXIT_UNEXPECTED = 6    # 未預期例外
EXIT_BUDGET_ABORT = 7  # 達 --max-requests 上限而中止整輪（剩餘日期未執行）

EXIT_CODE_LEGEND = (
    "結束碼：0=全部 ok/no_data；1=環境或 IO 錯誤（含 --dry-run 無可用快取）；"
    "2=參數錯誤；3=全部日期失敗；4=部分日期失敗；5=被反爬中止（403/429）；"
    "6=未預期例外；7=達 --max-requests 上限中止整輪"
)

#: 輸出檔備份／暫存副檔名
BACKUP_SUFFIX = ".bak.json"
TMP_SUFFIX = ".tmp"

#: 冷啟動最多等多久（秒）。實測：bookingportal 的 Akamai proof-of-work 挑戰頁
#: 約 25–30 秒後自行放行；這裡給足餘裕，但**不介入、不代解**挑戰。
ARM_TOTAL_WAIT_SECONDS = 180

#: 第二腿（回程）只是為了還原官網請求形狀；`isRequestedBound: false`
#: → 回應不會包含回程票價（規格書 R7：回程要另發一次）。本工具不查回程。
RETURN_LEG_DAYS = 1

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

RISK_NOTES = [
    "資料來自華航官網前台自己的 API（api-des…/v2/search/air-bounds），"
    "不是公開或授權的資料源；持續性自動化存取很可能違反服務條款（ToS）。",
    "cookie / x-d-token 的重用時間是實測值（≥25 分鐘），不是保證值；"
    "本工具保守以 20 分鐘為快取 TTL，失效時必須重新開一次瀏覽器。",
    "api-des 由 DataDome、des-portal 由 Imperva 保護；被攔截時本工具會直接停止，"
    "不重試、不繞過、不偽造瀏覽器指紋、不使用代理或更換 IP。",
    "quota 是查詢當下的剩餘席位，重跑必然會有小幅跳動（±1 已在實測中出現）。",
    "票價為 pointOfSale=SEA（海外 POS）的含稅總價；與台灣官網顯示的一致性"
    "目前只在 CI100 / CI104 兩個點驗證過。",
]

FLIGHT_ID_RE = re.compile(
    r"^SEG-(?P<carrier>[A-Z]{2})(?P<number>\d+)-"
    r"(?P<origin>[A-Z]{3})(?P<destination>[A-Z]{3})-"
    r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<time>\d{4})$"
)


class ArmError(RuntimeError):
    """冷啟動（瀏覽器武裝）失敗。"""


class BlockedError(RuntimeError):
    """被反爬機制攔截（403/429）→ 必須立即停止整條路徑。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BudgetExceeded(RuntimeError):
    """達到 --max-requests 上限 → 停止整輪（不重試）。"""


class HttpStatusError(RuntimeError):
    """HTTP 4xx/5xx（非攔截碼）；保留 status 讓上層能分辨 401 與其他失敗。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


#: 只有「網路層」的暫時性錯誤才重試（P1-2：5xx / timeout / 連線錯誤）
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.RequestException,  # 含 Timeout / ConnectionError / …
    TimeoutError,
    ConnectionError,
)


def _is_transient_error(exc: BaseException) -> bool:
    """是否屬「值得重試」的暫時性錯誤（其餘例外＝確定性失敗，重試無意義）。"""
    return isinstance(exc, RETRYABLE_EXCEPTIONS)


# ---------------------------------------------------------------- 工具
def log(msg: str) -> None:
    """進度訊息一律走 stderr，讓 --json 的 stdout 保持乾淨。"""
    print(msg, file=sys.stderr)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def taipei_today() -> date:
    return (now_utc() + timedelta(hours=8)).date()


def fmt_flight_no(carrier: str, number: str) -> str:
    """("CI", "100") → "CI 100"。"""
    return f"{carrier} {number}"


def usage_error(msg: str) -> None:
    """參數／設定用法錯誤 → 印訊息後以 EXIT_USAGE 結束（不用 traceback）。"""
    log(msg)
    raise SystemExit(EXIT_USAGE)


def parse_flight_id(flight_id: str) -> dict:
    """`SEG-CI100-TPENRT-2026-11-20-0930` → 結構化欄位（解析不到回傳空 dict）。"""
    m = FLIGHT_ID_RE.match(flight_id or "")
    if not m:
        return {}
    hhmm = m.group("time")
    return {
        "carrier": m.group("carrier"),
        "flight_number": f"{m.group('carrier')}{m.group('number')}",
        "flight_no": fmt_flight_no(m.group("carrier"), m.group("number")),
        "origin": m.group("origin"),
        "destination": m.group("destination"),
        "departure_date": m.group("date"),
        "departure_time": f"{hhmm[:2]}:{hhmm[2:]}",
    }


# ---------------------------------------------------------------- 航線 / 日期
def parse_route(raw: str) -> tuple[str, str, str]:
    route_id = (raw or DEFAULT_ROUTE).strip().upper()
    if route_id not in SUPPORTED_ROUTES:
        usage_error(
            f"❌ 不支援的航線 {route_id}；本階段僅允許已實測驗證的 "
            f"{sorted(SUPPORTED_ROUTES)}（刻意不擴大範圍）")
    origin, destination = SUPPORTED_ROUTES[route_id]
    return route_id, origin, destination


def _iso_date_arg(raw: str, flag: str) -> date:
    """CLI 日期字串 → date；格式錯誤一律給友善訊息 + EXIT_USAGE。"""
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        usage_error(f"❌ {flag} 日期格式錯誤：{raw!r}（需 YYYY-MM-DD）")


def parse_dates(args) -> list[date]:
    """--date 或 --start-date/--end-date → 日期清單。"""
    if args.date and (args.start_date or args.end_date):
        usage_error("❌ --date 與 --start-date/--end-date 不可同時使用")
    if args.date:
        return [_iso_date_arg(args.date, "--date")]
    if args.start_date or args.end_date:
        if not (args.start_date and args.end_date):
            usage_error("❌ --start-date 與 --end-date 必須成對出現")
        start = _iso_date_arg(args.start_date, "--start-date")
        end = _iso_date_arg(args.end_date, "--end-date")
        if end < start:
            usage_error("❌ --end-date 不可早於 --start-date")
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]
    usage_error("❌ 請指定 --date 或 --start-date/--end-date")


def guard_output_path(out_path: Path) -> Path:
    """禁止寫入 data/（`build_api.py` 只 glob data/*.json，會被既有 pipeline 撿走）。"""
    try:
        out_path.resolve().relative_to(Path("data").resolve())
    except ValueError:
        return out_path
    usage_error("❌ 輸出路徑不可位於 data/（會被 build_api.py 吃進票價 API）")


def default_out_path(route_id: str, dates: list[date]) -> Path:
    if len(dates) == 1:
        name = f"{route_id}_{dates[0].isoformat()}.json"
    else:
        name = f"{route_id}_{dates[0].isoformat()}_{dates[-1].isoformat()}.json"
    return Path(OUTPUT_DIR) / name


def atomic_write_text(path: Path, text: str) -> None:
    """原子寫檔：先寫 `<name>.tmp` 再 `os.replace()`。

    任何一步失敗都會清掉 tmp 並把 `OSError` 丢給上層，**不會**留下半截檔，
    也不會先把原檔 truncate。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def read_previous_summary(path: Path) -> dict | None:
    """讀既有輸出檔的摘要（generated_at / totals）；壞檔或不存在 → None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {"generated_at": data.get("generated_at"), "totals": data.get("totals")}


def existing_has_usable_data(path: Path) -> bool:
    """既有輸出檔是否含有可用資料（任何 `ok` / `no_data` 查詢）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    queries = data.get("queries")
    if not isinstance(queries, list):
        return False
    return any(isinstance(q, dict) and q.get("status") in (STATUS_OK, STATUS_NO_DATA)
               for q in queries)


def should_preserve_existing(out_path: Path, totals: dict, aborted: bool) -> bool:
    """本輪不完整（有 failed 或被中止）且既有檔有可用資料 → 保留舊檔不覆寫。"""
    if not out_path.exists():
        return False
    if not (totals.get("failed") or aborted):
        return False
    return existing_has_usable_data(out_path)


def backup_existing(out_path: Path) -> Path | None:
    """把既有輸出檔備份為 `<stem>.bak.json`（原子寫入；失敗只警告）。"""
    if not out_path.exists():
        return None
    bak = out_path.with_name(out_path.stem + BACKUP_SUFFIX)
    try:
        atomic_write_text(bak, out_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        log(f"⚠️  無法備份上一版輸出檔（{exc.__class__.__name__}: {exc}），仍繼續寫入")
        return None
    return bak


def compute_exit_code(totals: dict, aborted: bool = False,
                      abort_reason: str | None = None) -> int:
    """依三態統計與是否被中止，給出明確的結束碼（語意見模組 docstring）。

    中止時一律非 0：`--max-requests` 用盡 → 7，其他中止（403/429 等）→ 5。
    """
    if aborted:
        if abort_reason == ABORT_REASON_BUDGET:
            return EXIT_BUDGET_ABORT
        return EXIT_ABORTED
    if not totals.get("failed"):
        return EXIT_OK
    if totals.get("ok") or totals.get("no_data"):
        return EXIT_PARTIAL
    return EXIT_ALL_FAILED


# ---------------------------------------------------------------- session 快取
def session_cache_path() -> Path:
    return CACHE_DIR / SESSION_FILENAME


def profile_dir() -> Path:
    return CACHE_DIR / PROFILE_DIRNAME


def load_session(path: Path, ttl: int = COOKIE_TTL_SECONDS,
                 now: datetime | None = None) -> dict | None:
    """讀快取；不存在 / 損壞 / 格式不符 / 過期 → None（過期就不冒險重用）。"""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        log(f"⚠️  {path} 不是合法 UTF-8（{exc.__class__.__name__}），忽略並重新武裝")
        return None
    except OSError as exc:
        log(f"⚠️  {path} 無法讀取（{exc.__class__.__name__}: {exc}），忽略並重新武裝")
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        log(f"⚠️  {path} 損壞（不是合法 JSON），忽略並重新武裝")
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("cookies") or not data.get("x_d_token") or not data.get("private_facts"):
        return None
    saved_at = data.get("saved_at")
    if not isinstance(saved_at, str) or not saved_at:
        log(f"⚠️  {path} 的 saved_at 格式不符（{type(saved_at).__name__}），忽略並重新武裝")
        return None
    try:
        saved = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        log(f"⚠️  {path} 的 saved_at 無法解析（{saved_at!r}），忽略並重新武裝")
        return None
    age = ((now or now_utc()) - saved).total_seconds()
    if age < 0 or age > ttl:
        return None
    data["_age_seconds"] = int(age)
    return data


def save_session(path: Path, data: dict) -> bool:
    """寫 session 快取（原子）；失敗只警告不中斷，回傳是否成功。"""
    try:
        atomic_write_text(
            path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    except OSError as exc:
        log(f"⚠️  無法寫入 session 快取 {path}"
            f"（{exc.__class__.__name__}: {exc}）；本輪仍會繼續，"
            f"但下次執行需要重新武裝")
        return False
    try:
        path.chmod(0o600)  # 內含 cookie / x-d-token
    except OSError:
        pass
    return True


def cookie_header(cookies: list[dict], host: str) -> str:
    """依 host 篩選 cookie 並組成 Cookie header（domain 尾比對，含 host-only）。"""
    parts = []
    for c in cookies or []:
        domain = (c.get("domain") or "").lstrip(".").lower()
        if not domain:
            continue
        if host == domain or host.endswith("." + domain):
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def incap_spa_info(session: dict, host: str = "api-des.china-airlines.com") -> str:
    """重建 `x-incap-spa-info`：`visid_incap_*=<v>; incap_ses_724_*=<v>`（取自當前 cookie）。

    優先由快取 cookie 重建（cookie 會被站方更新），重建不出來時退回武裝當下擷取的值。
    """
    jar = {c["name"]: c["value"] for c in session.get("cookies") or []
           if (c.get("domain") or "").lstrip(".").endswith("china-airlines.com")}
    visid = next((k for k in jar if k.startswith("visid_incap_")), None)
    ses = next((k for k in jar if k.startswith("incap_ses_724_")), None) \
        or next((k for k in jar if k.startswith("incap_ses_")), None)
    if visid and ses:
        return f"{visid}={jar[visid]}; {ses}={jar[ses]}"
    return session.get("x_incap_spa_info") or ""


# ---------------------------------------------------------------- 請求形狀（純函式）
def build_fact(template: dict | None, origin: str, destination: str,
               departure: date, return_leg: date) -> dict:
    """組 token/initialization 的 `fact`。

    以武裝時擷取到的真實 fact 為樣板（pointOfSale / officeId / device / ip … 全部照抄），
    只替換航段與日期 —— 實測「自組 fact 換日期」仍 200（規格書 §2.2 #14–18）。
    """
    base_pairs: dict[str, str] = {}
    for pair in (template or {}).get("keyValuePairs") or []:
        if isinstance(pair, dict) and "key" in pair:
            base_pairs[str(pair["key"])] = str(pair.get("value", ""))

    base_pairs["originLocationCode1"] = origin
    base_pairs["destinationLocationCode1"] = destination
    base_pairs["departureDateTime1"] = departure.isoformat()
    base_pairs["originLocationCode2"] = destination
    base_pairs["destinationLocationCode2"] = origin
    base_pairs["departureDateTime2"] = return_leg.isoformat()
    base_pairs.setdefault("pointOfSale", "SEA")
    base_pairs.setdefault("officeId", "TPECI08AB")
    base_pairs.setdefault("device", "SITE-DESKTOP-EVERYMUNDO-SEA/TW")
    base_pairs.setdefault("useMiles", "false")
    base_pairs.setdefault("useInsurance", "true")

    order = [
        "originLocationCode1", "destinationLocationCode1", "departureDateTime1",
        "originLocationCode2", "destinationLocationCode2", "departureDateTime2",
        "pointOfSale", "officeId", "device", "ip", "sessionId", "useMiles", "useInsurance",
    ]
    keys = order + [k for k in base_pairs if k not in order]
    return {"keyValuePairs": [{"key": k, "value": base_pairs[k]}
                              for k in keys if k in base_pairs]}


def build_token_form(fact: dict) -> dict:
    return {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "fact": json.dumps(fact, separators=(",", ":"), ensure_ascii=False),
        "grant_type": GRANT_TYPE,
    }


def build_air_bounds_payload(origin: str, destination: str,
                             departure: date, return_leg: date) -> dict:
    """單程方向查詢：只請求去程那一腿，回程腿 `isRequestedBound: false`。

    對照實測樣本（規格書 §3.1 ②）：`isRequestedBound: true` 的那一腿才會回票價。
    """
    return {
        "commercialFareFamilies": list(FARE_FAMILIES),
        "itineraries": [
            {"originLocationCode": origin, "destinationLocationCode": destination,
             "departureDateTime": f"{departure.isoformat()}T00:00:00.000",
             "isRequestedBound": True},
            {"originLocationCode": destination, "destinationLocationCode": origin,
             "departureDateTime": f"{return_leg.isoformat()}T00:00:00.000",
             "isRequestedBound": False},
        ],
        "travelers": [{"passengerTypeCode": "ADT"}],
        "searchPreferences": {"showMilesPrice": False},
    }


def build_public_facts_jwt(fact: dict) -> str:
    """自己組 `ama-client-facts` 前半段（`alg:none` 的未簽章 JWT）。

    實測該段 claims = fact 的扁平欄位（不含 `keyValuePairs`）加上 `sub: fact`；
    與武裝時擷取到的樣本一致，所以取不到樣本時可用它補上。
    """
    import base64

    claims: dict[str, Any] = {"sub": "fact"}
    for pair in (fact or {}).get("keyValuePairs") or []:
        if isinstance(pair, dict) and "key" in pair:
            claims[str(pair["key"])] = str(pair.get("value", ""))
    claims.pop("originLocationCode1", None)
    claims.pop("destinationLocationCode1", None)
    claims.pop("departureDateTime1", None)
    claims.pop("originLocationCode2", None)
    claims.pop("destinationLocationCode2", None)
    claims.pop("departureDateTime2", None)

    def enc(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc(claims)}."


def build_client_facts(session: dict) -> str:
    """`ama-client-facts` = `<alg:none JWT>. , <privateFacts JWE>`（實測格式，逗號後空一格）。"""
    public = (session.get("public_facts_jwt") or "").strip()
    private = (session.get("private_facts") or "").strip()
    return f"{public}, {private}" if public else private


# ---------------------------------------------------------------- 解析（純函式）
def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _first_total_price(air_bound: dict) -> dict:
    price, _field, _problem = _first_price_entry(air_bound.get("prices"))
    return price or {}


def _first_price_entry(prices) -> tuple[dict | None, str | None, str | None]:
    """從 `prices` 取第一筆票價。

    回傳 `(price, field_path, problem)`：
    - `problem is None` 且 `price is not None` → 有票價物件（total 另行驗證）
    - `problem is None` 且 `price is None` → 結構完整、但**明確**沒有票價（no_data 方向）
    - `problem is not None`        → 結構異常（不可當成查無）
    """
    if not isinstance(prices, dict):
        return None, "prices", (f"prices 缺少或型別錯誤"
                                 f"（{type(prices).__name__}）")
    if "totalPrices" not in prices and "unitPrices" not in prices:
        return None, "prices.totalPrices", "缺少 prices.totalPrices"
    for field in ("totalPrices", "unitPrices"):
        if field not in prices:
            continue
        bucket = prices.get(field)
        if bucket is None:
            continue
        if not isinstance(bucket, list):
            return None, field, f"prices.{field} 型別錯誤（{type(bucket).__name__}，應為 list）"
        if not bucket:
            continue  # 明確的空陣列 → 這班沒票價
        first = bucket[0]
        if not isinstance(first, dict):
            return None, field, f"prices.{field}[0] 型別錯誤（{type(first).__name__}）"
        if "prices" in first:  # unitPrices 形狀
            inner = first.get("prices")
            if not isinstance(inner, list) or not inner:
                return None, f"{field}.prices", f"prices.{field}[0].prices 缺少或為空"
            if not isinstance(inner[0], dict):
                return None, f"{field}.prices", f"prices.{field}[0].prices[0] 型別錯誤"
            return inner[0], field, None
        return first, field, None
    return None, None, None  # 兩種 bucket 都明確為空 → 確定無票價


def parse_air_bounds_report(body: dict, route_id: str) -> tuple[list[dict], dict]:
    """解析 air-bounds 回應，並回傳 `(flights, stats)`。

    `stats["anomalies"]` 記錄所有「結構異常」——呼叫端必須靠它把
    「確定無資料」與「看不懂資料」分開（**不得**把結構異常當成 `no_data`）。
    """
    stats: dict[str, Any] = {
        "airBoundGroups": 0,
        "bounds_seen": 0,
        "price_entries_seen": 0,
        "no_price_entries": 0,
        "rejected_no_total": 0,
        "rejected_bad_total": 0,
        "zero_total": 0,
        "anomalies": [],
    }
    anomalies: list[str] = stats["anomalies"]

    def anomaly(msg: str) -> None:
        if len(anomalies) < 8:  # 只保留前幾筆，避免輸出爆炸
            anomalies.append(msg)

    if not isinstance(body, dict):
        anomaly(f"回應最外層不是 object（{type(body).__name__}）")
        return [], stats
    data = body.get("data")
    if data is None:
        anomaly("缺少 data（無法判定是否有資料）")
        return [], stats
    if not isinstance(data, dict):
        anomaly(f"data 型別錯誤（{type(data).__name__}，應為 object）")
        return [], stats
    groups = data.get("airBoundGroups")
    if groups is None:
        anomaly("缺少 data.airBoundGroups（無法判定是否有資料）")
        return [], stats
    if not isinstance(groups, list):
        anomaly(f"data.airBoundGroups 型別錯誤（{type(groups).__name__}，應為 list）")
        return [], stats
    stats["airBoundGroups"] = len(groups)

    by_flight: dict[str, dict] = {}

    for gi, group in enumerate(groups):
        if not isinstance(group, dict):
            anomaly(f"airBoundGroups[{gi}] 不是 object（{type(group).__name__}）")
            continue
        bound_details = group.get("boundDetails")
        if not isinstance(bound_details, dict):
            anomaly(f"airBoundGroups[{gi}].boundDetails 缺少或型別錯誤")
            continue
        segments = bound_details.get("segments")
        if not isinstance(segments, list) or not segments:
            anomaly(f"airBoundGroups[{gi}].boundDetails.segments 缺少、型別錯誤或為空")
            continue
        if not isinstance(segments[0], dict):
            anomaly(f"airBoundGroups[{gi}].boundDetails.segments[0] 不是 object")
            continue
        air_bounds = group.get("airBounds")
        if air_bounds is None:
            anomaly(f"airBoundGroups[{gi}].airBounds 缺少")
            continue
        if not isinstance(air_bounds, list):
            anomaly(f"airBoundGroups[{gi}].airBounds 型別錯誤"
                    f"（{type(air_bounds).__name__}，應為 list）")
            continue

        default_id = segments[0].get("flightId") or ""
        default_meta = parse_flight_id(default_id)

        for ai, air_bound in enumerate(air_bounds):
            stats["bounds_seen"] += 1
            where = f"airBoundGroups[{gi}].airBounds[{ai}]"
            if not isinstance(air_bound, dict):
                anomaly(f"{where} 不是 object（{type(air_bound).__name__}）")
                continue
            availability = (air_bound.get("availabilityDetails") or [{}])[0] or {}
            if not isinstance(availability, dict):
                anomaly(f"{where}.availabilityDetails[0] 不是 object")
                continue
            flight_id = availability.get("flightId") or default_id
            meta = parse_flight_id(flight_id) or default_meta
            price, field, problem = _first_price_entry(air_bound.get("prices"))
            if problem:
                anomaly(f"{where}.{problem}")
                continue
            if price is None:
                # 結構完整、票價清單明確為空 → 確定沒有票價
                stats["no_price_entries"] += 1
                continue
            stats["price_entries_seen"] += 1
            total = price.get("total")
            if total is None:
                stats["rejected_no_total"] += 1
                anomaly(f"{where}.prices.{field}.total 缺少"
                        f"（站方欄位可能已改名）")
                continue
            if not _is_number(total):
                stats["rejected_bad_total"] += 1
                anomaly(f"{where}.prices.{field}.total 型別錯誤"
                        f"（{type(total).__name__}，應為數字）")
                continue
            if total == 0:
                # total 明確為 0 → 視為「沒有可售票價」而非異常
                stats["zero_total"] += 1
                continue

            record = by_flight.get(flight_id)
            if record is None:
                record = {
                    "flight_id": flight_id,
                    "flight_no": meta.get("flight_no") or "",
                    "flight_number": meta.get("flight_number") or "",
                    "carrier": meta.get("carrier") or "CI",
                    "origin": meta.get("origin") or bound_details.get("originLocationCode"),
                    "destination": meta.get("destination")
                    or bound_details.get("destinationLocationCode"),
                    "departure_date": meta.get("departure_date"),
                    "departure_time": meta.get("departure_time"),
                    "route_id": route_id,
                    "fares": [],
                }
                by_flight[flight_id] = record

            record["fares"].append({
                "fare_family_code": air_bound.get("fareFamilyCode"),
                "cabin": availability.get("cabin"),
                "booking_class": availability.get("bookingClass"),
                "status_code": availability.get("statusCode"),
                "quota": availability.get("quota"),
                "price_total": total,
                "base": price.get("base"),
                "total_taxes": price.get("totalTaxes"),
                "currency": price.get("currencyCode"),
                "is_cheapest_offer": air_bound.get("isCheapestOffer", False),
                "fare_type": ((air_bound.get("fareInfos") or [{}])[0] or {}).get("fareType"),
                "fare_basis": ((air_bound.get("fareInfos") or [{}])[0] or {}).get("fareClass"),
            })

    flights = []
    for record in by_flight.values():
        record["fares"].sort(key=lambda f: (f["price_total"], str(f["cabin"]),
                                            str(f["booking_class"])))
        record["fare_count"] = len(record["fares"])
        cheapest = record["fares"][0] if record["fares"] else None
        record["cheapest_fare"] = cheapest
        record["cheapest_price"] = cheapest["price_total"] if cheapest else None
        flights.append(record)

    flights.sort(key=lambda f: (f["departure_time"] or "", f["flight_no"]))
    return flights, stats


def parse_air_bounds(body: dict, route_id: str) -> list[dict]:
    """把 air-bounds 回應轉成「逐班 → 逐票種」的乾淨結構（純函式，測試直接餵 fixture）。

    只需要航班清單時用這個；要分辨「確定無資料 / 看不懂資料」請用
    `classify_air_bounds()` 或 `parse_air_bounds_report()`。
    """
    flights, _stats = parse_air_bounds_report(body, route_id)
    return flights


def classify_air_bounds(body: dict, route_id: str) -> dict:
    """三態分類（嚴格）：

    - `ok`      ：解析到至少一筆票價
    - `no_data` ：**結構完整**且明確沒有票價（例如 `airBoundGroups: []`、
                  `totalPrices: []`、`total == 0`）
    - `failed`  ：任何結構異常（缺 `total`、欄位改名、型別錯誤、bounds 為空陣列
                  但缺必要 key…）——寧可 failed，也不要給出「看起來像查無」的結果
    """
    flights, stats = parse_air_bounds_report(body, route_id)
    data = body.get("data") if isinstance(body, dict) else None
    evidence = {
        "airBoundGroups": stats["airBoundGroups"],
        "response_keys": sorted(data.keys()) if isinstance(data, dict) else [],
        "present": {k: k in data for k in ("airBoundGroups",)}
        if isinstance(data, dict) else {},
        "parse_stats": stats,
    }
    result: dict[str, Any] = {
        "flights": flights,
        "raw_bounds_count": stats["airBoundGroups"],
        "parse_stats": stats,
        "evidence": evidence,
        "error": None,
    }
    if stats["anomalies"]:
        result["status"] = STATUS_FAILED
        result["error"] = ("回應結構異常（無法安全判定為查無）："
                          + "；".join(stats["anomalies"][:3]))
    elif flights:
        result["status"] = STATUS_OK
    else:
        result["status"] = STATUS_NO_DATA
    return result


def summarize_prices(flights: list[dict]) -> dict:
    """每班最低價的 min / max / 全部票種筆數（供人類快速核對）。"""
    cheapest = [f["cheapest_price"] for f in flights if f.get("cheapest_price") is not None]
    if not cheapest:
        return {"flight_count": len(flights), "fare_count": 0}
    return {
        "flight_count": len(flights),
        "fare_count": sum(f.get("fare_count", 0) for f in flights),
        "price_min": min(cheapest),
        "price_max": max(cheapest),
        "currency": (flights[0]["cheapest_fare"] or {}).get("currency"),
    }


# ---------------------------------------------------------------- 冷啟動（瀏覽器武裝）
def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 環境相依
        raise ArmError(
            "需要 Playwright 才能冷啟動取得 cookie（本專案刻意不列入 requirements.txt）：\n"
            "    pip install playwright && playwright install chromium\n"
            "（若機器沒有 DISPLAY，請用 `xvfb-run -a python3 fetch_ci_fares.py ...`）"
        ) from exc
    return sync_playwright


def _find_chromium(playwright) -> str | None:
    """找可用的 Chromium：env 指定 > Playwright 預設 > 本機 ms-playwright 快取。"""
    env_path = os.environ.get("CI_CHROMIUM_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    try:  # Playwright 內建版本（若剛好有安裝對應 build）
        default = playwright.chromium.executable_path
        if default and Path(default).exists():
            return default
    except Exception:  # noqa: BLE001
        pass
    roots = [Path.home() / ".cache" / "ms-playwright",
             Path("/root/.cache/ms-playwright"),
             Path("/usr/local/share/ms-playwright")]
    candidates: list[Path] = []
    for root in roots:
        candidates.extend(root.glob("chromium-*/chrome-linux64/chrome"))
        candidates.extend(root.glob("chromium-*/chrome-linux/chrome"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _human_pause(page, low: float = 0.4, high: float = 0.9) -> None:
    page.wait_for_timeout(int(random.uniform(low, high) * 1000))


def _pick_date(page, target: date) -> bool:
    """在官網日曆 popover 點選目標日期（用 aria-label 的 YYYY/MM/DD 定位）。"""
    want_iso = target.strftime("%Y/%m/%d")
    for _ in range(4):  # 最多往後翻 4 個月
        cell = page.locator(f'[data-date="{target.isoformat()}"]')
        if cell.count() == 0:
            cell = page.locator(f'button[aria-label^="{want_iso},"]')
        if cell.count() > 0:
            try:
                cell.first.click()
                _human_pause(page)
                return True
            except Exception:  # noqa: BLE001
                pass
        moved = page.evaluate(
            """() => {
                const btns = [...document.querySelectorAll('button')].filter(b => b.offsetParent);
                const nxt = btns.find(b => /next|下一|下個|下個月/i.test(
                    (b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')));
                if (nxt) { nxt.click(); return true; }
                return false;
            }"""
        )
        if not moved:
            return False
        _human_pause(page)
    return False


def _browser_failure_message(exc: BaseException) -> str:
    """把瀏覽器相關例外轉成人類可讀的冷啟動失敗訊息（保留原始錯誤）。"""
    detail = f"{exc.__class__.__name__}: {exc}"
    text = str(exc).lower()
    hints: list[str] = []
    if ("executable doesn't exist" in text or "executable does not exist" in text
            or "browsertype.launch" in text or "chromium" in text):
        hints.append("chromium 可能未安裝 → 執行 `playwright install chromium`")
    if "x server" in text or "display" in text or "headed" in text:
        hints.append("沒有 DISPLAY → 請用 `xvfb-run -a python3 fetch_ci_fares.py ...`")
    if "timeout" in exc.__class__.__name__.lower() or "timeout" in text:
        hints.append("官網頁面載入或元素互動逾時（版面可能已改版、或網路不通、或被反爬阻擋）")
    if "permission" in text or "read-only" in text or "no space" in text:
        hints.append("檢查磁碟空間與寫入權限（瀏覽器 profile 位於 ~/.cache/ci_fares/，"
                     "可用 CI_FARES_CACHE_DIR 覆寫）")
    lines = [f"冷啟動失敗（瀏覽器操作未完成）：{detail}"]
    lines += [f"   → {hint}" for hint in hints]
    return "\n".join(lines)


def arm_session(route_id: str, origin: str, destination: str,
                departure: date, return_leg: date,
                headless: bool | None = None) -> dict:
    """冷啟動外殼：任何未預期例外（playwright Error / TimeoutError / OSError）
    都轉成 `ArmError`，讓上層能以友善訊息 + 對應結束碼收尾，不以 traceback 結束。
    """
    try:
        return _arm_session_browser(route_id, origin, destination, departure,
                                    return_leg, headless)
    except ArmError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ArmError(_browser_failure_message(exc)) from exc


def _arm_session_browser(route_id: str, origin: str, destination: str,
                         departure: date, return_leg: date,
                         headless: bool | None = None) -> dict:
    """開一次瀏覽器走真實官網 UI，把反爬 cookie / x-d-token / privateFacts 取回來。

    只做「照 UI 操作」：逐字輸入出發地/目的地、點日曆日期、按一次「現在預訂」。
    不做任何指紋偽造、不注入 stealth patch、不使用代理。
    """
    sync_playwright = _load_playwright()
    profile = profile_dir()
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArmError(
            f"無法建立瀏覽器 profile 目錄 {profile}：{exc.__class__.__name__}: {exc}\n"
            f"   → 檢查磁碟空間／權限，或用 CI_FARES_CACHE_DIR 指到可寫入的位置"
        ) from exc
    if headless is None:
        headless = not bool(os.environ.get("DISPLAY"))

    captured: dict[str, Any] = {
        "x_d_token": None, "x_incap_spa_info": None,
        "token_post": None, "token_headers": None,
        "air_bounds_headers": None, "air_bounds_post": None,
        "private_facts": None, "public_facts_jwt": None, "ama_client_ref": None,
        "statuses": [], "errors": [],
    }

    log(f"🌐 冷啟動：開瀏覽器（{'headless' if headless else 'headed'}）"
        f"走一次真實官網查詢 {origin}→{destination} {departure}…")
    started = time.time()

    with sync_playwright() as playwright:
        exe = _find_chromium(playwright)
        launch_kwargs: dict[str, Any] = dict(
            headless=headless,
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            viewport={"width": 1440, "height": 950},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage"],
        )
        if exe:
            launch_kwargs["executable_path"] = exe
        ctx = playwright.chromium.launch_persistent_context(str(profile), **launch_kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(25000)

        def on_request(req):
            try:
                url = req.url
                if "api-des.china-airlines.com" in url:
                    headers = req.headers or {}
                    if headers.get("x-d-token"):
                        captured["x_d_token"] = headers["x-d-token"]
                    if headers.get("x-incap-spa-info"):
                        captured["x_incap_spa_info"] = headers["x-incap-spa-info"]
                    if "/token/initialization" in url:
                        captured["token_post"] = req.post_data
                        captured["token_headers"] = headers
                    if "/search/air-bounds" in url:
                        captured["air_bounds_headers"] = headers
                        captured["air_bounds_post"] = req.post_data
                        facts = headers.get("ama-client-facts") or ""
                        if "," in facts:
                            public, private = facts.split(",", 1)
                            captured["public_facts_jwt"] = public.strip()
                            captured["private_facts"] = private.strip()
                        if headers.get("ama-client-ref"):
                            captured["ama_client_ref"] = headers["ama-client-ref"]
                # 備援：bookingportal → des-portal 的 document POST 也帶 privateFacts
                body = req.post_data or ""
                if "privateFacts=" in body and not captured["private_facts"]:
                    from urllib.parse import parse_qs
                    parsed = parse_qs(body)
                    if parsed.get("privateFacts"):
                        captured["private_facts"] = parsed["privateFacts"][0]
            except Exception as exc:  # noqa: BLE001
                captured["errors"].append(f"request hook: {exc!r}")

        def on_response(resp):
            try:
                url = resp.url
                if "api-des.china-airlines.com" in url:
                    if resp.headers.get("x-d-token"):
                        captured["x_d_token"] = resp.headers["x-d-token"]
                    captured["statuses"].append(("api-des", resp.status, url.split("?")[0]))
                elif "/Booking/Search" in url:
                    captured["statuses"].append(("bookingportal", resp.status, "/Booking/Search"))
                elif "des-portal.china-airlines.com" in url:
                    captured["statuses"].append(("des-portal", resp.status, url.split("?")[0]))
            except Exception as exc:  # noqa: BLE001
                captured["errors"].append(f"response hook: {exc!r}")

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            # 暖身：主站（真 Akamai 站）→ flights 子網域（Cloudflare）
            try:
                page.goto(WWW_HOME_URL, wait_until="domcontentloaded", timeout=45000)
                page.mouse.move(420, 320)
                page.wait_for_timeout(1500)
                page.mouse.wheel(0, 700)
                page.wait_for_timeout(1200)
            except Exception as exc:  # noqa: BLE001
                log(f"⚠️  主站暖身略過（{exc.__class__.__name__}）")

            resp = page.goto(FLIGHTS_HOME_URL, wait_until="domcontentloaded", timeout=60000)
            log(f"   flights 首頁 HTTP {getattr(resp, 'status', '?')}，填寫表單…")
            page.wait_for_timeout(2500)

            consent = page.locator("button:has-text('我同意')")
            for i in range(min(consent.count(), 3)):
                try:
                    if consent.nth(i).is_visible():
                        consent.nth(i).click()
                        page.wait_for_timeout(600)
                        break
                except Exception:  # noqa: BLE001
                    pass

            for placeholder, code, option_id in (
                ("選擇出發地", origin, f"option-{origin}"),
                ("選擇目的地", destination, f"option-{destination}"),
            ):
                box = page.locator(f"input[placeholder='{placeholder}']").first
                box.scroll_into_view_if_needed()
                box.click()
                page.wait_for_timeout(400)
                for ch in code:  # 逐字輸入（避免一次塞值）
                    page.keyboard.type(ch)
                    page.wait_for_timeout(random.randint(90, 200))
                page.wait_for_timeout(1600)
                option = page.locator(f"#{option_id}")
                if option.count() == 0:
                    option = page.locator("[role=option]").first
                option.click()
                page.wait_for_timeout(700)

            triggers = page.locator("[data-att$='date-toggler'] button")
            if triggers.count() < 2:  # 備援：用 YYYY/MM/DD 文字的按鈕
                triggers = page.locator("button", has_text=re.compile(r"^\d{4}/\d{2}/\d{2}$"))
            for idx, target in ((0, departure), (1, return_leg)):
                if triggers.count() <= idx:
                    raise ArmError("找不到官網的日期選擇器（版面可能已改版）")
                triggers.nth(idx).click()
                page.wait_for_timeout(1200)
                if not _pick_date(page, target):
                    raise ArmError(f"無法在官網日曆點選 {target}（版面可能已改版）")
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)

            submit = page.locator("button:has-text('現在預訂')")
            clicked = False
            for i in range(min(submit.count(), 40)):
                button = submit.nth(i)
                try:
                    if not button.is_visible():
                        continue
                    button.click()
                    clicked = True
                    break
                except Exception:  # noqa: BLE001
                    continue
            if not clicked:
                raise ArmError("找不到「現在預訂」按鈕（版面可能已改版）")

            # 等站方自己把 api-des 的請求打出來。
            # 冷客戶端會先遇到 Akamai `Challenge Validation`（proof-of-work），
            # 實測約 25–30 秒自行清關 → 這裡只等待，**不介入、不代解**。
            deadline = time.time() + ARM_TOTAL_WAIT_SECONDS
            challenge_noted = False
            last_note = 0.0
            while time.time() < deadline:
                if (captured["private_facts"] and captured["x_d_token"]
                        and captured["token_post"]):
                    break
                title = ""
                try:
                    title = page.title()
                except Exception:  # noqa: BLE001
                    pass
                if "challenge" in title.lower() and not challenge_noted:
                    log("   ⏳ 站方反爬挑戰頁（Challenge Validation），等待瀏覽器自行完成…")
                    challenge_noted = True
                elif time.time() - last_note > 20:
                    log(f"   …等待查詢結果（{page.url.split('?')[0][:70]}）")
                    last_note = time.time()
                page.wait_for_timeout(1500)
            page.wait_for_timeout(2000)
            cookies = ctx.cookies()
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

    elapsed = time.time() - started
    statuses = captured["statuses"]
    if not captured["private_facts"] or not captured["x_d_token"]:
        blocks = [s for s in statuses if s[1] in (403, 429, 503)]
        details = ", ".join(f"{name} {code}" for name, code, _ in statuses[-8:]) or "沒有任何相關回應"
        hint = ""
        if blocks:
            hint = ("\n   → 站方反爬（Imperva / Akamai / DataDome）目前擋下這個客戶端；"
                    "本工具不繞過、不重試硬闖，請稍後再試或改用人工查詢。")
        raise ArmError(
            "冷啟動失敗：沒有取得 cookie / x-d-token / privateFacts。\n"
            f"   觀測到的回應：{details}{hint}\n"
            f"   （詳見 docs/research/ci-deeplink-api-feasibility.md §二、§六）"
        )

    token_post = captured["token_post"] or ""
    from urllib.parse import parse_qs
    parsed_post = parse_qs(token_post)
    fact_template = None
    if parsed_post.get("fact"):
        try:
            fact_template = json.loads(parsed_post["fact"][0])
        except json.JSONDecodeError:
            fact_template = None

    session = {
        "saved_at": iso(now_utc()),
        "ttl_seconds": COOKIE_TTL_SECONDS,
        "cookies": cookies,
        "x_d_token": captured["x_d_token"],
        "x_incap_spa_info": captured["x_incap_spa_info"]
        or incap_spa_info({"cookies": cookies}),
        "private_facts": captured["private_facts"],
        "public_facts_jwt": captured["public_facts_jwt"]
        or (build_public_facts_jwt(fact_template) if fact_template else None),
        "ama_client_ref": captured["ama_client_ref"],
        "user_agent": (captured["air_bounds_headers"] or {}).get("user-agent")
        or (captured["token_headers"] or {}).get("user-agent")
        or UA_POOL[0],
        "fact_template": fact_template,
        "arm_route_id": route_id,
        "arm_departure": departure.isoformat(),
        "arm_return": return_leg.isoformat(),
        "arm_elapsed_seconds": round(elapsed, 1),
        "arm_observed_statuses": [f"{n} {c}" for n, c, _ in statuses[-12:]],
    }
    return session


# ---------------------------------------------------------------- 暖查詢（純 HTTP）
def _headers(session: dict, host: str) -> dict:
    headers = {
        "accept": "application/json",
        "accept-language": "zh-TW",
        "origin": "https://des-portal.china-airlines.com",
        "referer": "https://des-portal.china-airlines.com/",
        "x-spa": "1",
        "x-d-token": session.get("x_d_token") or "",
        "user-agent": session.get("user_agent") or UA_POOL[0],
        "cookie": cookie_header(session.get("cookies") or [], host),
    }
    info = incap_spa_info(session, host)
    if info:
        headers["x-incap-spa-info"] = info
    return headers


def _post(session: requests.Session, url: str, *, data: str | None, headers: dict,
          budget: "RequestBudget", post: Callable[..., Any] | None = None):
    if post is None:
        post = session.post
    budget.spend()   # 先檢查上限（超過就不發、也不白等）
    budget.pace()    # 再與上一個對外請求保持隨機間隔（P1-3）
    return post(url, data=data, headers=headers, timeout=HTTP_TIMEOUT)


def _status_of(resp) -> int:
    return int(getattr(resp, "status_code", 0) or 0)


def _json_or_error(resp) -> dict:
    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"回應不是 JSON（HTTP {_status_of(resp)}）") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"回應 JSON 不是 object（HTTP {_status_of(resp)}）")
    return body


class RequestBudget:
    """單次執行的 HTTP 請求紀律：總上限（預設 30）+ 相鄰請求之間的隨機間隔。

    間隔由 `pace()` 在**每一個**對外請求之前決定，因此涵蓋：同日 token → air-bounds、
    每次重試之前、401 重新武裝之後、不同日期之間。本地操作（讀快取、寫檔）
    不會經過這裡，因此不會 sleep。
    """

    def __init__(self, max_requests: int, sleep_min: float = REQUEST_DELAY_MIN,
                 sleep_max: float = REQUEST_DELAY_MAX):
        self.max_requests = max_requests
        self.used = 0
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max

    def spend(self) -> None:
        if self.max_requests and self.used >= self.max_requests:
            raise BudgetExceeded(
                f"已達本次執行的請求上限（--max-requests {self.max_requests}），停止查詢")
        self.used += 1

    def pace(self) -> None:
        """兩個對外 HTTP 請求之間的隨機延遲（第一個請求之前不等）。"""
        if self.used <= 1:
            return
        delay = random.uniform(self.sleep_min, self.sleep_max)
        if delay <= 0:
            return
        log(f"⏳ 請求間隔 {delay:.1f}s ...")
        time.sleep(delay)

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.used) if self.max_requests else 10 ** 9


def fetch_token(session: requests.Session, ctx: dict, fact: dict,
                budget: RequestBudget, post: Callable[..., Any] | None = None) -> str:
    headers = _headers(ctx, "api-des.china-airlines.com")
    headers["content-type"] = "application/x-www-form-urlencoded"
    resp = _post(session, TOKEN_URL, data=urlencode(build_token_form(fact)),
                 headers=headers, budget=budget, post=post)
    status = _status_of(resp)
    if status in BLOCK_STATUSES:
        raise BlockedError(f"取得 token 被攔截（HTTP {status}）", status)
    if status == 401:
        raise HttpStatusError("取得 token 未授權（HTTP 401）", 401)
    if status >= 400:
        raise HttpStatusError(f"取得 token 失敗（HTTP {status}）", status)
    body = _json_or_error(resp)
    token = body.get("access_token")
    if not token:
        raise RuntimeError("取得 token 失敗：回應沒有 access_token")
    return token


def fetch_air_bounds(session: requests.Session, ctx: dict, token: str,
                     payload: dict, ref_suffix: int,
                     budget: RequestBudget,
                     post: Callable[..., Any] | None = None) -> dict:
    headers = _headers(ctx, "api-des.china-airlines.com")
    headers.update({
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "ama-client-facts": build_client_facts(ctx),
        "ama-client-ref": f"{(ctx.get('ama_client_ref') or '').split(':')[0] or uuid.uuid4()}"
                          f":{ref_suffix}",
        "cache-control": "max-age=0",
    })
    resp = _post(session, AIR_BOUNDS_URL,
                 data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                 headers=headers, budget=budget, post=post)
    status = _status_of(resp)
    if status in BLOCK_STATUSES:
        raise BlockedError(f"查詢 air-bounds 被攔截（HTTP {status}，反爬攔截）", status)
    if status == 401:
        raise HttpStatusError("查詢 air-bounds 未授權（HTTP 401）", 401)
    if status >= 400:
        raise HttpStatusError(f"查詢 air-bounds 失敗（HTTP {status}）", status)
    return _json_or_error(resp)


def query_leg(session: requests.Session, ctx: dict, route_id: str,
              origin: str, destination: str, query_date: date,
              budget: RequestBudget, ref_index: int = 0,
              post: Callable[..., Any] | None = None) -> dict:
    """查一個日期的逐班票價（正常 2 個 HTTP 請求）。

    回傳 schema 一律相同（見 `make_query_result`：所有狀態 key 集合相同、缺值為 null）；
    **不把失敗吞成空陣列**，狀態一律三態分離。

    重試規則（P1-2）：只有 **5xx / timeout / 連線錯誤**才重試（2 次、退避 3s/6s）；
    400/404/422 及其餘 4xx 是**終止性錯誤** → 立即停止該日期、不重試
    （401 → 交上層重新武裝一次；403/429 → `blocked` 中止整輪）。
    """
    return_leg = query_date + timedelta(days=RETURN_LEG_DAYS)
    fact = build_fact(ctx.get("fact_template"), origin, destination, query_date, return_leg)
    payload = build_air_bounds_payload(origin, destination, query_date, return_leg)

    result = make_query_result(query_date, route_id)

    last_error: Exception | None = None
    last_status: int | None = None
    for attempt in range(RETRIES + 1):
        try:
            token = fetch_token(session, ctx, fact, budget, post=post)
            body = fetch_air_bounds(session, ctx, token, payload, ref_index,
                                    budget, post=post)
            # 嚴格三態分類：結構異常一律 failed，**不可**偽裝成 no_data
            classified = classify_air_bounds(body, route_id)
            flights = classified["flights"]
            result["http_status"] = 200
            result["raw_bounds_count"] = classified["raw_bounds_count"]
            result["flights"] = flights
            result["flight_count"] = len(flights)
            result["fare_count"] = sum(f.get("fare_count", 0) for f in flights)
            result["parse_stats"] = classified["parse_stats"]
            result["evidence"] = classified["evidence"]
            result["status"] = classified["status"]
            if classified["status"] == STATUS_OK:
                result["summary"] = summarize_prices(flights)
                result["error"] = None
            elif classified["status"] == STATUS_NO_DATA:
                # 200 且結構完整但明確沒有票價：保留原始證據，絕不寫成 0 元
                result["error"] = None
            else:
                # 解析結果屬確定性失敗（同回應重試仍會一樣）→ 不重試，直接 failed
                result["error"] = classified["error"]
            result["status_label"] = STATUS_LABELS[result["status"]]
            return result
        except BlockedError as exc:
            # 403 / 429：立刻停止，不退避硬闖（規格書 §4.3）
            result["status"] = STATUS_FAILED
            result["status_label"] = STATUS_LABELS[STATUS_FAILED]
            result["error"] = str(exc)
            result["http_status"] = exc.status
            result["blocked"] = True
            result["aborted"] = True
            result["abort_reason"] = ABORT_REASON_BLOCKED
            return result
        except BudgetExceeded as exc:
            result["status"] = STATUS_FAILED
            result["status_label"] = STATUS_LABELS[STATUS_FAILED]
            result["error"] = str(exc)
            result["budget_exhausted"] = True
            result["aborted"] = True
            result["abort_reason"] = ABORT_REASON_BUDGET
            return result
        except HttpStatusError as exc:
            last_error = exc
            last_status = exc.status
            if exc.status == 401:
                # 401（session 過期）不重試：交由上層「重新武裝一次後重試一次」處理
                result["status"] = STATUS_FAILED
                result["status_label"] = STATUS_LABELS[STATUS_FAILED]
                result["error"] = str(exc)
                result["http_status"] = 401
                return result
            if exc.status is not None and exc.status >= 500:
                # 只有 5xx 才重試（P1-2）
                if attempt < RETRIES:
                    delay = RETRY_BACKOFF * (attempt + 1)
                    log(f"⚠️  {query_date} 第 {attempt + 1} 次查詢失敗（{exc}），{delay}s 後重試")
                    time.sleep(delay)
                    continue
                break  # 5xx 重試耗盡 → 收尾（保留最後的 http_status）
            # 400/404/422 及其餘 4xx：確定性錯誤，重試只是燒請求、惹站方 → 立即停止該日期
            log(f"❌ {query_date} 終止性錯誤（HTTP {exc.status}），不重試")
            result["status"] = STATUS_FAILED
            result["status_label"] = STATUS_LABELS[STATUS_FAILED]
            result["error"] = str(exc)
            result["http_status"] = exc.status
            return result
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            last_status = None
            if _is_transient_error(exc) and attempt < RETRIES:
                # 只有 timeout / 連線錯誤等暫時性例外才重試（P1-2）
                delay = RETRY_BACKOFF * (attempt + 1)
                log(f"⚠️  {query_date} 第 {attempt + 1} 次查詢失敗（{exc}），{delay}s 後重試")
                time.sleep(delay)
                continue
            # 非網路型例外（非 JSON 回應、型別錯誤…）＝對方已回應，重試無意義
            result["status"] = STATUS_FAILED
            result["status_label"] = STATUS_LABELS[STATUS_FAILED]
            result["error"] = str(exc) or exc.__class__.__name__
            return result

    result["status"] = STATUS_FAILED
    result["status_label"] = STATUS_LABELS[STATUS_FAILED]
    result["error"] = str(last_error) if last_error else "未知錯誤"
    result["http_status"] = last_status
    return result


# ---------------------------------------------------------------- 主流程
def build_payload(route_id: str, origin: str, destination: str,
                  results: list[dict], ctx: dict, budget: RequestBudget,
                  generated_at: str, *, aborted: bool = False,
                  abort_reason: str | None = None,
                  abort_message: str | None = None) -> dict:
    total_flights = sum(r["flight_count"] for r in results)
    total_fares = sum(r["fare_count"] for r in results)
    return {
        "generated_at": generated_at,
        "source": "china_airlines_official_api_des_air_bounds",
        "airline_code": "CI",
        "airline_name": "China Airlines",
        "route_id": route_id,
        "origin": origin,
        "destination": destination,
        "query_shape": "one-way direction (return leg sent with isRequestedBound=false)",
        "status_legend": {
            STATUS_OK: "HTTP 200 且解析到逐班票價",
            STATUS_NO_DATA: "HTTP 200 但沒有 airBoundGroups / 沒有任何票價（＝查無，不是 0 元）",
            STATUS_FAILED: "4xx/5xx/timeout/解析失敗（見 error 欄位）",
        },
        "totals": {
            "queries": len(results),
            "ok": sum(1 for r in results if r["status"] == STATUS_OK),
            "no_data": sum(1 for r in results if r["status"] == STATUS_NO_DATA),
            "failed": sum(1 for r in results if r["status"] == STATUS_FAILED),
            "flights": total_flights,
            "fares": total_fares,
        },
        "requests_used": budget.used,
        "requests_limit": budget.max_requests,
        "aborted": bool(aborted),
        "abort_reason": abort_reason,
        "abort_message": abort_message,
        "session": {
            "armed_at": ctx.get("saved_at"),
            "session_age_seconds": ctx.get("_age_seconds"),
            "cookie_ttl_assumed_seconds": ctx.get("ttl_seconds", COOKIE_TTL_SECONDS),
            "cookie_ttl_observed_minutes": COOKIE_TTL_OBSERVED_MINUTES,
            "x_d_token_present": bool(ctx.get("x_d_token")),
            "private_facts_present": bool(ctx.get("private_facts")),
            "bot_protection": ["Imperva (des-portal)", "DataDome (api-des)"],
            "cookie_reuse_is_measured_not_guaranteed": True,
        },
        "risk_notes": RISK_NOTES,
        "queries": results,
    }


def main() -> int:
    """CLI 入口：任何例外都轉成友善訊息 + 明確結束碼（不以 traceback 結束）。"""
    try:
        return _run_main()
    except SystemExit:
        raise  # argparse / usage_error 已帶明確結束碼與訊息
    except KeyboardInterrupt:
        log("\n⛔ 使用者中斷（Ctrl-C）；本輪結果未寫入")
        return 130
    except Exception as exc:  # noqa: BLE001
        log(f"❌ 未預期例外：{exc.__class__.__name__}: {exc}")
        if os.environ.get("CI_FARES_DEBUG"):
            import traceback
            traceback.print_exc()
        else:
            log("   這是本工具未涵蓋的環境問題或 bug；設定 CI_FARES_DEBUG=1 可印出完整 traceback")
        return EXIT_UNEXPECTED


def _run_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="中華航空 TPE→NRT 逐班票價抓取器（單一航線、手動執行、不寫入 data/）",
        epilog=EXIT_CODE_LEGEND,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="查詢日期 YYYY-MM-DD")
    parser.add_argument("--start-date", help="區間查詢起日 YYYY-MM-DD")
    parser.add_argument("--end-date", help="區間查詢迄日 YYYY-MM-DD（含）")
    parser.add_argument("--route", default=DEFAULT_ROUTE,
                        help=f"航線，預設 {DEFAULT_ROUTE}（僅允許 {sorted(SUPPORTED_ROUTES)}）")
    parser.add_argument("--json", action="store_true", help="把結構化結果印到 stdout")
    parser.add_argument("--out", help=f"輸出路徑（預設 {OUTPUT_DIR}/<route>_<date>.json）")
    parser.add_argument("--dry-run", action="store_true",
                        help="純本地檢查：不開瀏覽器、不發任何 HTTP 請求、不寫檔"
                             "（沒有可用 session 快取會報錯 exit 1）")
    parser.add_argument("--refresh-cookie", action="store_true",
                        help="忽略快取，強制重新開瀏覽器取 cookie / x-d-token")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help=f"本次執行的 HTTP 請求上限；用盡即中止整輪（exit "
                             f"{EXIT_BUDGET_ABORT}），預設 {DEFAULT_MAX_REQUESTS}")
    parser.add_argument("--sleep-min", type=float, default=REQUEST_DELAY_MIN,
                        help=f"相鄰兩次 HTTP 請求的最小間隔秒數（預設 {REQUEST_DELAY_MIN}；"
                             f"本地操作與 --dry-run 不延遲）")
    parser.add_argument("--sleep-max", type=float, default=REQUEST_DELAY_MAX,
                        help=f"相鄰兩次 HTTP 請求的最大間隔秒數（預設 {REQUEST_DELAY_MAX}）")
    parser.add_argument("--headed", action="store_true",
                        help="強制用有頭瀏覽器（沒有 DISPLAY 時請搭配 xvfb-run）")
    args = parser.parse_args(argv)

    if args.dry_run and args.refresh_cookie:
        usage_error("❌ --dry-run 不可與 --refresh-cookie 同時使用"
                    "（重新武裝必須開瀏覽器，而 --dry-run 絕不開瀏覽器）")
    if args.sleep_min < 0 or args.sleep_max < args.sleep_min:
        usage_error(f"❌ 請求間隔參數不合理：--sleep-min {args.sleep_min} / "
                    f"--sleep-max {args.sleep_max}（需 0 ≤ min ≤ max）")

    route_id, origin, destination = parse_route(args.route)
    dates = parse_dates(args)
    out_path = guard_output_path(Path(args.out) if args.out else default_out_path(route_id, dates))
    generated_at = iso(now_utc())
    cache_path = session_cache_path()
    budget = RequestBudget(args.max_requests, args.sleep_min, args.sleep_max)

    log(f"🛫 華航逐班票價抓取：{route_id}（{origin}→{destination}），"
        f"{len(dates)} 個日期，預計 ≤ {len(dates) * 2} 個 HTTP 請求（上限 {args.max_requests}）")
    log(f"   輸出路徑：{out_path}（{'dry-run 不寫檔' if args.dry_run else '寫檔'}）")

    # ---- session：先看快取，除非 --refresh-cookie
    ctx = None if args.refresh_cookie else load_session(cache_path)
    if ctx:
        log(f"♻️  重用 session 快取（{ctx.get('_age_seconds')}s 前武裝，"
            f"TTL {ctx.get('ttl_seconds', COOKIE_TTL_SECONDS)}s）")
    else:
        if args.dry_run:
            # P1-4：--dry-run 絕不開瀏覽器；沒有可用快取就是「無法在本地完成」
            log("❌ 無可用 session 快取，--dry-run 無法進行；請先正常執行一次"
                "（不加 --dry-run）完成冷啟動並留下快取，再重跑 --dry-run")
            return EXIT_ENV
        if args.refresh_cookie:
            log("🔄 --refresh-cookie：忽略快取，強制重新武裝")
        else:
            log("🗂  沒有可用的 session 快取 → 冷啟動")
        try:
            ctx = arm_session(route_id, origin, destination, dates[0],
                              dates[0] + timedelta(days=RETURN_LEG_DAYS),
                              headless=False if args.headed else None)
        except ArmError as exc:
            log(f"❌ {exc}")
            return EXIT_ENV
        save_session(cache_path, ctx)
        ctx["_age_seconds"] = 0
        log(f"✅ 冷啟動完成（{ctx.get('arm_elapsed_seconds')}s）：cookie {len(ctx['cookies'])} 個、"
            f"x-d-token {'有' if ctx.get('x_d_token') else '無'}、"
            f"privateFacts {'有' if ctx.get('private_facts') else '無'}"
            f" → 快取 {cache_path}")

    # ---- --dry-run：純本地檢查（P1-4：不開瀏覽器、不發任何 HTTP 請求、不寫檔）
    if args.dry_run:
        payload = build_payload(route_id, origin, destination, [], ctx, budget, generated_at)
        payload["dry_run"] = True
        payload["planned_dates"] = [d.isoformat() for d in dates]
        payload["planned_requests"] = len(dates) * 2
        payload["out_path"] = str(out_path)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        log("🔍 dry-run：僅本地檢查 —— 不開瀏覽器、不發任何 HTTP 請求、不寫檔")
        log(f"   session 快取可用（{ctx.get('_age_seconds')}s 前武裝）；"
            f"實際執行會發 {payload['planned_requests']} 個請求"
            f"（上限 {budget.max_requests}）並寫入 {out_path}")
        log(f"➡️  結束碼 {EXIT_OK}（{EXIT_CODE_LEGEND}）")
        return EXIT_OK

    # ---- 逐日期查詢（暖查詢：2 個請求 / 日期；相鄰請求之間隨機 2–4s，
    #      由 RequestBudget.pace() 統一處理：同日 token → air-bounds、重試之前、
    #      401 重新武裝之後、不同日期之間都涵蓋；本地操作不延遲）
    results: list[dict] = []
    session = requests.Session()
    abort_reason: str | None = None
    abort_message: str | None = None
    for idx, query_date in enumerate(dates):
        if abort_reason:
            # 整輪已中止 → 剩餘日期標記「未執行」，且**不再**發請求、**不再** sleep
            log(f"⏭  {query_date}: {abort_message}")
            results.append(
                make_aborted_result(query_date, route_id, abort_reason, abort_message))
            continue

        try:
            result = query_leg(session, ctx, route_id, origin, destination,
                               query_date, budget, ref_index=idx)
        except BudgetExceeded as exc:  # 防禦性（query_leg 已自行轉成 budget_exhausted）
            log(f"🛑 {query_date}: {exc}")
            abort_reason = ABORT_REASON_BUDGET
            abort_message = abort_message_for(abort_reason, args.max_requests)
            results.append(
                make_aborted_result(query_date, route_id, abort_reason, abort_message))
            continue
        except RuntimeError as exc:  # 防禦性（query_leg 通常已自行處理）
            log(f"❌ {query_date}: {exc}")
            abort_reason = ABORT_REASON_ERROR
            abort_message = abort_message_for(abort_reason)
            results.append(make_query_result(query_date, route_id, error=str(exc)))
            continue

        # cookie / token 失效（401）→ 重新武裝一次，只重試一次
        # 與 --dry-run 解耦：dry-run 在上面就已返回（不發請求），根本不會遇到 401
        if result["status"] == STATUS_FAILED and result.get("http_status") == 401:
            log("♻️  session 失效（401）→ 重新武裝一次並重試")
            try:
                ctx = arm_session(route_id, origin, destination, query_date,
                                  query_date + timedelta(days=RETURN_LEG_DAYS),
                                  headless=False if args.headed else None)
                save_session(cache_path, ctx)
                result = query_leg(session, ctx, route_id, origin, destination,
                                   query_date, budget, ref_index=idx)
            except ArmError as exc:
                log(f"❌ 重新武裝失敗：{exc}")

        results.append(result)

        # 中止判定（P1-1）：反爬攔截 403/429 或 --max-requests 用盡 → 停止整輪
        if abort_reason is None and result.get("blocked"):
            abort_reason = ABORT_REASON_BLOCKED
        elif abort_reason is None and result.get("budget_exhausted"):
            abort_reason = ABORT_REASON_BUDGET
        if abort_reason is not None:
            abort_message = abort_message_for(abort_reason, args.max_requests)

        if result.get("aborted"):
            log(f"🛑 {query_date} [已中止] {result.get('error')}")
            log(f"   → {abort_message}")
        elif result["status"] == STATUS_OK:
            cheapest = result["flights"][0].get("cheapest_fare") or {}
            log(f"✅ {query_date} [{result['status_label']}] {result['flight_count']} 班 / "
                f"{result['fare_count']} 個票種，最低 TWD "
                f"{cheapest.get('price_total', '?')}（{result['flights'][0]['flight_no']}）")
            for flight in result["flights"]:
                fare = flight.get("cheapest_fare") or {}
                log(f"   {flight['flight_no']:<8} {flight.get('departure_time')} "
                    f"{flight.get('origin')}→{flight.get('destination')}  "
                    f"{fare.get('cabin')} {fare.get('booking_class')} "
                    f"quota={fare.get('quota')}  TWD {fare.get('price_total')}")
        elif result["status"] == STATUS_NO_DATA:
            log(f"⚪ {query_date} [{result['status_label']}] "
                f"airBoundGroups={result.get('raw_bounds_count')}")
        else:
            log(f"❌ {query_date} [{result['status_label']}] {result.get('error')}")

    aborted = abort_reason is not None
    payload = build_payload(route_id, origin, destination, results, ctx, budget, generated_at,
                            aborted=aborted, abort_reason=abort_reason,
                            abort_message=abort_message)

    totals = payload["totals"]
    log(f"\n📊 查詢 {totals['queries']} 個日期：有價 {totals['ok']}、"
        f"查無 {totals['no_data']}、失敗 {totals['failed']}"
        f"（航班 {totals['flights']} 班 / 票種 {totals['fares']} 筆，"
        f"HTTP 請求 {budget.used}/{budget.max_requests or '∞'} 個）")
    if aborted:
        log(f"🛑 本輪中止（{abort_reason}）：{abort_message}")
    log("⚠️  資料來自華航官網前台 API（Imperva + DataDome 保護）；cookie 重用時間為實測值"
        "（≥25 分鐘）非保證，且持續自動化存取很可能違反 ToS。詳見 ci_fares/README.md")

    # ---- 輸出：原子寫入 + 失敗不覆寫既有成功資料
    preserve = False
    if not args.dry_run:
        preserve = should_preserve_existing(out_path, totals, aborted)
        if not preserve:
            previous = read_previous_summary(out_path)
            if previous:
                payload["previous"] = previous

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.dry_run:
        log("🔍 dry-run：不寫檔")
    elif preserve:
        previous = read_previous_summary(out_path) or {}
        log(f"🛡  本輪不完整（含 failed/中止），**保留上一輪資料**：{out_path}")
        log(f"   舊檔 generated_at={previous.get('generated_at')}、"
            f"totals={previous.get('totals')}")
        log("   本次結果未寫入輸出檔（可用 --json 取得）")
    else:
        try:
            bak = backup_existing(out_path)
            atomic_write_text(
                out_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            log(f"❌ 寫入輸出檔失敗：{out_path}（{exc.__class__.__name__}: {exc}）")
            log("   → 檢查磁碟空間與目錄權限；本輪結果仍可用 --json 取得")
            return EXIT_ENV
        log(f"📦 寫入 {out_path}" + (f"（上一版備份：{bak.name}）" if bak else ""))

    exit_code = compute_exit_code(totals, aborted=aborted, abort_reason=abort_reason)
    log(f"➡️  結束碼 {exit_code}（{EXIT_CODE_LEGEND}）")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
