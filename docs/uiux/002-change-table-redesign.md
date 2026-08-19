# 每週票價變動 — 表格改卡片設計規格

> 對應元件：`web/index.html` `#changeTableWrap` + `web/styles.css` `.ct-table`
> 互動 Mockup：`docs/uiux/002-change-table-redesign-mockup.html`
> 設計日期：2026-08-19

---

## 1. 現況審計

| # | 問題 | 嚴重度 | 位置 |
|---|------|--------|------|
| 1 | 表格 6 欄在 mobile（≤767px）靠 `overflow-x: auto` 橫向捲動，需左右滑動才能看完一列 | P1 | `styles.css .change-table-wrap` |
| 2 | 漲跌方向（↑/↓）與百分比在同一欄，視覺層級扁平，不易快速掃描哪些週漲幅最大 | P2 | `.ct-table .ct-change` |
| 3 | 最低價與上次價格並列但無視覺關聯，需自行比較才能判斷變化 | P2 | `.ct-table .ct-price` |
| 4 | 表格列數多（最多 40 週）時滾動疲勞，缺乏分組或摘要 | P3 | `app.js renderChangeTable` |
| 5 | 無 hover/focus 互動態定義（table row 僅有 `tr:hover background`） | P3 | `styles.css .ct-table tr:hover` |

### 實測數據（CSS 計算值）

- 表格列高度：`padding 0.4rem × 2 + line-height ≈ 38px`（字級 0.78rem × 1.5 ≈ 18.7px）
- 表格最小寬度：6 欄 × ~100px = ~600px（觸發 mobile 橫向捲動斷點）
- 手機 375px 可見欄數：約 2.5 欄（需捲動 3.5 欄寬）

---

## 2. 設計原則

1. **一卡一週** — 每張卡片代表一週的變動資訊，mobile 自然堆疊，不需橫向捲動。
2. **漲跌即視覺** — 用色彩（success/danger）與箭頭大小直接傳達方向與幅度，不需閱讀文字。
3. **價格關聯** — 新舊價格上下排列，中間以差額連接，形成「從→到」的閱讀動線。
4. **漸進式揭露** — 卡片顯示核心（出發日、新價、漲跌%）；次要資訊（回程日、舊價、絕對金額）以較小字級呈現。
5. **響應式優先** — Desktop 3 欄 → Tablet 2 欄 → Mobile 1 欄，無需橫向捲動。

---

## 3. 目標設計

### 卡片結構

```
┌─────────────────────────────┐
│  📅 09/19 出發 → 09/27 回程  │  ← 日期行（muted, 0.72rem）
│                             │
│     NT$14,139               │  ← 當前最低價（bold, 1.05rem, accent）
│     ↓ -8%  (-NT$1,200)     │  ← 漲跌指示（success/danger 色）
│     上次 NT$15,339          │  ← 上次價格（muted, 0.75rem）
└─────────────────────────────┘
```

### 佈局

- **Desktop（≥1024px）**：3 欄 grid，gap 12px
- **Tablet（768–1023px）**：2 欄 grid
- **Mobile（≤767px）**：1 欄，卡片全寬

### 卡片尺寸

- 寬度：自適應 grid（minmax(220px, 1fr)）
- 最小高度：約 100px（含 padding）
- padding：12px 16px
- 圓角：10px（與 Summary 三卡一致）

---

## 4. 狀態矩陣

| 狀態 | 視覺 | 互動 |
|------|------|------|
| 降價 | success 色箭頭 ↓ + 百分比 | — |
| 漲價 | danger 色箭頭 ↑ + 百分比 | — |
| 持平 | muted 色 — + 0% | — |
| 無變動資料 | 不顯示卡片（filter out） | — |
| Hover | 卡片陰影加深 +微微上移 | — |
| Focus（鍵盤） | 3px accent ring | — |
| Loading | 不適用（資料隨圖表同步載入） | — |
| 空結果 | 整區隱藏（hidden） | — |

---

## 5. 無障礙（WCAG）

- **1.4.1** 不單靠顏色：漲跌同時有文字箭頭（↑/↓）與百分比數字。
- **2.4.7** focus ring：卡片可 focus（tabindex=0），3px accent ring。
- **2.5.5** 觸控目標：卡片整體可點擊（如需），最小高度 ≥44px。
- **4.1.2** 語意：容器 `role="list"`，每張卡片 `role="listitem"` + `aria-label` 含完整變動資訊。

---

## 6. RWD 行為

| 斷點 | 行為 |
|------|------|
| ≥1024px | 3 欄 grid |
| 768–1023px | 2 欄 grid |
| ≤767px | 1 欄，卡片全寬，日期行改垂直堆疊 |

---

## 7. 實作建議

1. **替換 `renderChangeTable`**：改為 `renderChangeCards`，產生 `.change-cards` grid 容器。
2. **CSS 新增**：`.change-cards`（grid layout）、`.ccard`（卡片樣式）、`.ccard-*`（內部元素）。
3. **移除**：`.ct-table` 相關 CSS（或保留作為 fallback，設定 `.change-table-container` 為 hidden）。
4. **資料流不變**：仍從 `visible` weeks 篩選有 `minChangePct !== null` 的週。

---

## 8. 驗收清單

- [ ] Desktop 3 欄、Tablet 2 欄、Mobile 1 欄
- [ ] 漲跌色彩正確（↓ success / ↑ danger）
- [ ] 空結果時整區隱藏
- [ ] 深色主題可讀
- [ ] 無 console error
- [ ] HTML 標籤平衡
- [ ] 無障礙標籤完整
