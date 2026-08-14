"""星宇航空機票價格爬蟲 — 設定檔

日期規則：去程=週六，回程=下週日（8 天旅程）
查詢範圍：未來 NUM_WEEKS 週
輸出：data/YYYYMMDD.json（每次執行存一個檔，累積歷史）
"""

# 要追蹤的航線（星宇航空 ST JX）
# 可用航線：TPE-NRT 東京 / TPE-KIX 大阪 / TPE-FUK 福岡 / TPE-CTS 札幌 ...
ROUTES = [
    {"route_id": "TPE-NRT", "origin": "TPE", "destination": "NRT"},  # 東京成田
    {"route_id": "TPE-KIX", "origin": "TPE", "destination": "KIX"},  # 大阪關西
    {"route_id": "TPE-FUK", "origin": "TPE", "destination": "FUK"},  # 福岡
    {"route_id": "TPE-CTS", "origin": "TPE", "destination": "CTS"},  # 札幌新千歲
]

# 查詢未來幾週（去週六 → 下週日）
NUM_WEEKS = 40

# 回程 = 去程 + 8 天（去週六 → 下週日）
RETURN_AFTER_DAYS = 8

# 艙等：eco / business / first
CABIN = "eco"

# 輸出目錄（GitHub Actions 會把 data/ 提交回 repo）
OUTPUT_DIR = "data"

# API 設定（星宇官方訂票 API，已實測）
API_URL = "https://ecapi.starlux-airlines.com/searchFlight/v2/flights/search"

# User-Agent 池：每次請求隨機挑一個瀏覽器 UA，避免固定機器人標記
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

API_HEADERS = {
    "Content-Type": "application/json",
    "jx-lang": "zh-TW",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.starlux-airlines.com/",
    "Origin": "https://www.starlux-airlines.com",
}

# 請求間隨機延遲（秒）：打亂節奏，避免固定頻率被識別
REQUEST_DELAY_MIN = 2.0
REQUEST_DELAY_MAX = 6.0

# 重試設定
RETRIES = 3
RETRY_BACKOFF = 3  # 秒（指數退避：3s, 6s）
