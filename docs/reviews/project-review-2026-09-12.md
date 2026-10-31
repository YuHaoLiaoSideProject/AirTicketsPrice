# ✈️ 星宇航空機票價格追蹤 — 專案審查報告

> 審查日期：2026-09-12
> 最後更新：2026-09-13（Phase 0–2 完成）

---

## 🌟 做得好的地方

1. **架構清晰** — 爬蟲 → build API → 前端 PWA → Worker 推播，各層職責分明
2. **文件完善** — tech decisions、BDD、interaction flow、test plans 都有，開發流程很紮實
3. **CI/CD 自動化** — GitHub Actions 排程 + Pages 部署 + 自動 commit，零人工干預
4. **離線優先設計** — IndexedDB 快取 + ETag 條件式請求 + 增量更新，PWA 做得很到位
5. **防禦性設計** — 周頻率守衛、通知失敗不中斷流程、QuotaExceeded 降級處理

---

## 🔧 建議改進

### 1. ~~app.js 太大（1714 行），建議拆分~~ ✅ 已完成

已拆分為 4 個模組（2026-09-13）：

- `chart.js`（303 行）— SVG 圖表渲染、Tooltip
- `modal.js`（450 行）— 詳情 Modal 開關、航班表格、歷史走勢圖
- `controls.js`（193 行）— 地區/航線/航班/範圍控制項
- `app.js`（998 行）— 初始化、資料層、同步、離線、PWA

拆分方式：Callback 注入、UMD 模式、每步驟測試通過才下一步。

### 2. 前端缺少打包工具，重複 code 多

目前是純靜態 UMD 模組，多個檔案各自宣告 `CONFIG` 等常數（aggregate.js 與 config.py 也有重複）。建議：

- 用 Vite / esbuild 做輕量打包（不需 webpack 那麼重）
- 或至少把航線定義抽成單一共用的 JSON，Python 與 JS 共用

### 3. `fetch_prices.py` 的例外處理太寬

```python
except Exception as e:  # noqa: BLE001
```

多處 catch 所有例外，建議至少區分 `requests.ConnectionError`、`requests.Timeout`、`json.JSONDecodeError`，方便排查問題。

### 4. 缺少 Dependabot / Renovate

`requirements.txt` 的依賴沒有自動更新機制，建議加入 Dependabot 監控 Python 與 npm 依賴的安全性更新。

### 5. data/ 目錄的 JSON 檔會持續增長 ⚠️ 已確認無清理機制

已驗證 `build_api.py` 無任何刪除/合併邏輯，檔案永久累積。建議：

- 在 `build_api.py` 加入 `keep_days=90` 參數，刪除超過 N 天的舊檔
- 或合併舊資料（如超過 3 個月的 data/*.json 合併成月彙整檔）
- 已有對應 unit test（`tests/unit/test_data_cleanup.py`）確認目前無清理行為

### 6. E2E 測試用 Python（Playwright），但前端是 JS

測試語言不一致會增加維護負擔。建議：

- E2E 改用 Playwright for Node.js（`@playwright/test`），與前端技術棧一致
- 或至少把 smoke test 用 JS 重寫

### 7. Worker 端缺少 rate limiting

Cloudflare Worker 的 `/notify` 和 `/subscribe` 端點沒有 rate limit，如果 token 洩漏或被掃到，可能被濫用。建議加 Cloudflare 自帶的 rate limiting rules。

### 8. 旺季日期硬編碼在前端 aggregate.js

```javascript
PEAKS: [
  { label: '農曆過年', from: '2027-01-30', to: '2027-02-06' },
  ...
]
```

後端 `holidays.py` 已用 lunardate 自動推算農曆過年，但前端還是一年更新一次的硬編碼。建議旺季日期完全由 `build_api.py` 產出到 `index.json`，前端只讀 API 帶入的值（目前已有 `setPeaks()` 機制，但 fallback 還是寫死的）。

### 9. 缺少 staging 環境

所有變更直接上 production（GitHub Pages）。建議用 GitHub 的 environment protection rules，或另開一個 staging branch 供測試。

### 10. 通知功能缺少使用者端的設定

目前通知是「有下降就推」，沒有讓使用者設定：

- 只推特定航線
- 價格低於某門檻才推
- 推播頻率（每週 / 每次下降）

這會影響使用者的 notification fatigue。

---

## 📋 優先順序建議

| 優先 | 項目 | 影響 | 狀態 |
|------|------|------|------|
| ~~🔴 高~~ | ~~拆分 app.js~~ | 可維護性 | ✅ 已完成 |
| 🔴 高 | 旺季日期不硬編碼 | 每年維護成本 | 待做 |
| 🟡 中 | data/ 資料清理 | 儲存與載入效能 | 已確認無清理，待實作 |
| 🟡 中 | 通知設定化 | 使用者體驗 | 待做 |
| 🟢 低 | Dependabot | 安全性 | 待做 |
| 🟢 低 | E2E 改 JS | 開發體驗 | 待做 |

---

## 結語

整體來說這是一個完成度很高的 side project，文件與測試都做得比多數專案好很多 👍

### 2026-09-13 更新

已完成 Phase 0–2：
- ✅ 測試紅燈修復（route assertion 同步、脆弱 E2E assertion 修正）
- ✅ 資料清理測試（確認無清理機制，已寫 test 確認）
- ✅ app.js 拆分（1714→998 行，新增 modal.js/chart.js/controls.js）
- ✅ 全部 187 個測試通過（135 JS + 52 Python）
