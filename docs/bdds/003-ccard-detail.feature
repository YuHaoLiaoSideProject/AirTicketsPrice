@ccard @detail @003
Feature: ccard 點擊詳情面板
  作為一個機票比價使用者
  我希望點擊「每週票價變動」卡片後能看到航班比較與歷史走勢
  以便快速判斷最佳購票時機

  Background:
    Given 使用者已載入首頁且有航班資料

  # ── 觸發與關閉 ──────────────────────────────────

  @smoke @p0
  Scenario: 點擊 ccard 開啟詳情 modal
    When 使用者點擊任一 ccard
    Then 顯示詳情 modal
    And modal 顯示該週的出發日期與回程日期

  @smoke @p0
  Scenario: 按 ESC 關閉詳情 modal
    Given 詳情 modal 已開啟
    When 使用者按下 ESC 鍵
    Then 詳情 modal 關閉

  @smoke @p0
  Scenario: 點擊 backdrop 關閉詳情 modal
    Given 詳情 modal 已開啟
    When 使用者點擊 modal 外部區域
    Then 詳情 modal 關閉

  @smoke @p0
  Scenario: 關閉 modal 後 focus 回到觸發 ccard
    Given 詳情 modal 已開啟
    When 使用者關閉詳情 modal
    Then focus 回到原先點擊的 ccard

  # ── Section 1：航班價格比較 ─────────────────────

  @flight-comparison @p0
  Scenario: 顯示多航班價格比較表格
    Given 該出發日有 2 個以上航班
    When 詳情 modal 開啟
    Then 顯示航班價格比較表格
    And 表格欄位包含：航班、時間、價格、比平均差異百分比
    And 最低價航班以綠色背景高亮並顯示「最低」badge

  @flight-comparison @p0
  Scenario: 航班依價格由低到高排序
    Given 該出發日有 2 個以上航班
    When 詳情 modal 開啟
    Then 航班列表依價格由低到高排序

  @flight-comparison @p1
  Scenario: 單一航班降級為單行顯示
    Given 該出發日僅有 1 個航班
    When 詳情 modal 開啟
    Then 航班比較區域顯示為單行卡片（非表格）

  @flight-comparison @p1
  Scenario: 售罄航班顯示已售罄
    Given 該航班狀態為售罄
    When 詳情 modal 開啟
    Then 航班列表仍可見（灰色）
    And 價格欄顯示「已售罄」

  @flight-comparison @p2
  Scenario: 無價格時顯示暫無報價
    Given 該航班無價格資料
    When 詳情 modal 開啟
    Then 價格欄顯示「暫無報價」

  # ── Section 2：歷史價格走勢 ─────────────────────

  @history-trend @p0
  Scenario: 顯示歷史價格折線圖
    Given 該出發日有 2 次以上抓取記錄
    When 詳情 modal 開啟
    Then 顯示歷史價格折線圖
    And X 軸為抓取日期
    And Y 軸為價格
    And 最低點以綠色圓點標記
    And 最新抓取點以藍色圓點標記

  @history-trend @p0
  Scenario: 顯示歷史價格統計摘要
    Given 該出發日有 2 次以上抓取記錄
    When 詳情 modal 開啟
    Then 圖表下方顯示最低價及日期
    And 顯示最高價及日期
    And 顯示降幅百分比

  @history-trend @p1
  Scenario: 單次抓取顯示單點與提示
    Given 該出發日僅有 1 次抓取記錄
    When 詳情 modal 開啟
    Then 折線圖顯示單個資料點
    And 下方提示「僅 1 個資料點，尚無走勢可比較」

  @history-trend @p2
  Scenario: 僅 2 次抓取仍顯示折線圖
    Given 該出發日有 2 次抓取記錄
    When 詳情 modal 開啟
    Then 折線圖顯示兩點連線

  @history-trend @p2
  Scenario: 所有抓取價格相同顯示穩定
    Given 該出發日所有抓取價格均相同
    When 詳情 modal 開啟
    Then 折線圖顯示水平線
    And 下方顯示「價格穩定」

  # ── Footer：資料說明 ────────────────────────────

  @footer @p1
  Scenario: 顯示抓取次數與日期範圍
    Given 詳情 modal 已開啟
    Then modal 底部顯示抓取次數
    And 顯示抓取日期範圍

  # ── RWD 行為 ────────────────────────────────────

  @rwd @p0
  Scenario Outline: 不同斷點的 modal 寬度
    Given 詳情 modal 已開啟
    When 使用者裝置寬度為 <width>
    Then modal 寬度為 <behavior>

    Examples:
      | width     | behavior                      |
      | ≥1024px   | 固定 560px 並置中              |
      | 768-1023px | 90vw，最大 480px             |
      | ≤767px    | 從底部滑入的 bottom sheet，全寬 |

  @rwd @p1
  Scenario: Mobile 使用 bottom sheet 樣式
    Given 使用者裝置寬度 ≤767px
    When 使用者點擊 ccard
    Then 詳情面板從底部滑入（sheet style）
    And 面板圓角 16px

  # ── 無障礙 ──────────────────────────────────────

  @a11y @p0
  Scenario: modal 開啟時 focus 管理
    Given 詳情 modal 已開啟
    Then focus 移入 modal 內
    And modal 具有 aria-modal="true" 屬性

  @a11y @p1
  Scenario: 關閉按鈕具有 aria-label
    Given 詳情 modal 已開啟
    Then 關閉按鈕具有 aria-label="關閉詳情"

  @a11y @p1
  Scenario: 表格使用語意化標記
    Given 詳情 modal 顯示航班比較表格
    Then 表格使用 <table> 標記
    And 包含 <th> 表頭標記

  @a11y @p1
  Scenario: 圖表具有無障礙描述
    Given 詳情 modal 顯示歷史走勢圖
    Then 圖表具有 role="img" 屬性
    And 圖表具有 aria-label 描述趨勢

  @a11y @p1
  Scenario: 漲跌使用多重管道傳達
    Given 詳情 modal 顯示比平均差異
    Then 漲跌同時使用箭頭符號（↑↓）與文字（漲/降）
    And 不僅依賴顏色傳達資訊

  # ── 載入與錯誤 ──────────────────────────────────

  @loading @p1
  Scenario: 載入中顯示骨架屏
    When 使用者點擊 ccard
    And 資料尚未載入完成
    Then 顯示骨架屏（skeleton）佔位

  @error @p1
  Scenario: 載入失敗顯示錯誤提示
    When 使用者點擊 ccard
    And 資料載入失敗
    Then 顯示錯誤提示
    And 顯示重試按鈕

  @error @p2
  Scenario: 點擊重試重新載入
    Given 資料載入失敗且顯示重試按鈕
    When 使用者點擊重試按鈕
    Then 重新嘗試載入資料
