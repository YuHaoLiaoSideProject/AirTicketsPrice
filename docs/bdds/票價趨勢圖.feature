# 票價趨勢圖 — Gherkin BDD Feature
# 來源文件：
#   - docs/interaction-flows/票價趨勢圖.md（步驟 1–6 → Happy Path；異常處理表格 → Error Handling；邊界與限制 → Edge Cases；驗收清單 → Business Rules）
#   - docs/uiux/001-price-trend-design.md（狀態矩陣、無障礙 WCAG、RWD 行為）

@price-trend @frontend @dashboard @p0
Feature: 票價趨勢圖
  作為一個公開訪客
  我希望一眼看出星宇航空各航線未來 40 週中「哪週出發最便宜」
  以便快速決定最划算的出發週次、避開旺季高峰並縮短比價時間

  Background:
    Given 我以公開訪客身分使用票價趨勢圖（無需登入）

  # ============ Happy Path（主流程）============

  @smoke @happy-path @p0
  Scenario: 開啟頁面顯示頁首與預設航線 tab
    Given 頁面尚未載入任何資料，僅顯示標題與載入骨架
    When 我開啟票價趨勢圖頁面
    Then 頁面顯示標題「星宇航空票價趨勢」
    And 頁面頂部顯示資料更新時間（來自 index.json 的 generated_at）
    And 頁面顯示航線 tab「東京 TPE-NRT」與「大阪 TPE-KIX」
    And 預設選中第一條航線「東京 TPE-NRT」

  @smoke @happy-path @p0
  Scenario: 檢視東京 40 週趨勢圖（折線與平均線）
    Given 頁面已載入東京航線全部 trip 資料
    When 我檢視預設航線的圖表
    Then 首次載入於 3 秒內出現第一張圖表
    And 圖表顯示 40 個出發週的「每週最低價」折線，Y 軸單位為 TWD
    And 圖表顯示全域平均價虛線

  @happy-path @p0
  Scenario: 趨勢圖標記與圖例一目了然
    Given 圖表已顯示東京 40 週趨勢圖
    When 我檢視圖表上的標記與圖例
    Then 最低價週有明顯標記並顯示金額與日期
    And 旺季區塊（過年、櫻花季）有底色與名稱文字標籤
    And 圖例包含「每週最低價 / 平均價 / 旺季」，無需說明書即可理解

  @smoke @happy-path @p0
  Scenario: hover 查看單週價格細節
    Given 圖表已顯示東京 40 週趨勢圖
    When 我將滑鼠移到曲線上某一週的資料點
    Then 顯示 tooltip 包含出發日期、回程日期與價格（TWD）
    And tooltip 以文字數字呈現與平均價的差幅（比平均便宜或貴 N%）
    And tooltip 顯示該週最低價的航班號
    When 我將滑鼠移開該資料點
    Then tooltip 消失

  @happy-path @p0
  Scenario: 切換航班顯示模式
    Given 圖表顯示東京每週最低價主線
    When 我在航班下拉選擇「JX 800」
    Then 圖表只顯示 JX 800 的 40 週價格線
    And 缺資料週於曲線上顯示斷點
    And 工具列標註目前模式為「JX 800」
    When 我在航班下拉選擇「全部（每週最低價）」
    Then 圖表回到每週最低價主線

  @happy-path @p0
  Scenario Outline: 篩選日期範圍
    Given 圖表顯示東京全部 40 週
    When 我在日期範圍選擇「<範圍>」
    Then X 軸縮放到所選範圍
    And 最低價標記依可見範圍重新計算
    And 平均線維持全域基準不變
    And 旺季區塊保留於圖上
    And 標題包含「顯示 <範圍>（共 N 週）」（完整標題含航線名與航班模式前綴，見開發規格 §2.6）
    Examples:
      | 範圍     |
      | 3 個月   |
      | 6 個月   |
      | 12 個月  |
      | 全部     |

  @happy-path @p0
  Scenario: 切換航線並保留設定
    Given 圖表顯示東京趨勢圖，且航班模式為「JX 800」、日期範圍為「3 個月」
    When 我點擊「大阪 KIX」航線 tab
    Then 圖表切換為大阪 TPE-KIX 的 40 週趨勢圖
    And 標題與圖例更新為大阪資料
    And 日期範圍仍為「3 個月」
    And 航班模式回退為「全部（每週最低價）」，因「JX 800」僅存在於東京航線（大阪航班為 JX 820 / JX 822，工具列航班選項依大阪航班重建）

  # ============ Error Handling（異常處理）============

  @error-handling @p0
  Scenario: index.json 載入失敗顯示全頁錯誤卡並可重試
    Given 網路斷線或 GitHub Pages 異常，api/index.json 無法載入
    When 我開啟票價趨勢圖頁面
    Then 頁面顯示全頁錯誤卡「資料載入失敗」
    And 錯誤卡提供「重試」按鈕
    When 我點擊「重試」且網路恢復
    Then 頁面重新載入 index.json 並恢復顯示圖表
    And 連續失敗時錯誤卡顯示錯誤代碼

  @error-handling @p0
  Scenario: 某週 trip 檔缺失顯示斷點與「本週無資料」
    Given 東京航線第 20 週的 trip 檔不存在（該週未抓到資料）
    When 圖表繪製東京 40 週趨勢圖
    Then 曲線在第 20 週位置顯示斷點
    When 我 hover 第 20 週的斷點
    Then tooltip 顯示「本週無資料」

  @error-handling @p0
  Scenario: 售罄週標示
    Given 東京航線第 30 週全部航班售罄
    When 圖表繪製東京 40 週趨勢圖
    Then 第 30 週 X 軸下方標示「售罄」灰字
    And 曲線在第 30 週位置斷開

  @error-handling @p1
  Scenario: 資料過舊顯示黃色警示
    Given 資料上次更新時間距今超過 14 天
    When 我開啟票價趨勢圖頁面
    Then 頁面頂部顯示黃色警示「資料可能過時，上次更新：YYYY-MM-DD」
    And 圖表仍正常顯示

  @error-handling @p1
  Scenario: 無任何 trip 資料顯示空狀態
    Given 專案尚無任何 trip 資料（全新專案）
    When 我開啟票價趨勢圖頁面
    Then 頁面顯示空狀態插圖與訊息「尚無價格資料，每週五更新」

  @error-handling @p1
  Scenario: CORS 或網域來源問題
    Given 頁面從非允許的網域來源存取資料
    When 我開啟票價趨勢圖頁面
    Then 頁面顯示錯誤卡並提示改用 GitHub Pages 網址開啟
    And 錯誤卡提供「重試」按鈕

  # ============ Edge Cases（邊界情況）============

  @edge-case @p1
  Scenario: 載入中顯示骨架且工具列停用
    Given 頁面正在載入該航線的 trip 資料
    When 我開啟票價趨勢圖頁面
    Then 圖表區顯示載入骨架與 spinner
    And 頂部顯示載入進度
    And 工具列按鈕與下拉為停用狀態

  @edge-case @p1
  Scenario: 手機窄螢幕（375px）下圖表可讀且 tooltip 不溢出
    Given 我使用 375px 寬度的手機瀏覽器
    When 我開啟票價趨勢圖頁面並 hover 任一個資料點
    Then 圖表以最小 640px 寬度橫向捲動呈現
    And tooltip 以固定定位顯示且不溢出螢幕
    And 航線 tab 與工具列單欄垂直堆疊
    And 所有控制元件觸控目標至少 44px

  @edge-case @p1
  Scenario: 切換航線時已載入資料不重複下載
    Given 我已載入東京航線全部 trip 資料（記憶體快取生效）
    When 我切換到大阪航線再切回東京航線
    Then 東京圖表立即顯示，不重新 fetch 已載入的 trip 檔

  @edge-case @p1
  Scenario: 鍵盤 focus 讀取資料點
    Given 圖表已顯示東京 40 週趨勢圖
    When 我使用 Tab 鍵聚焦到某一週的資料點
    Then 資料點視覺放大並顯示與 hover 相同的 tooltip

  @edge-case @p2
  Scenario: 控制元件具備無障礙語意
    Given 票價趨勢圖頁面已載入東京航線
    When 我使用輔助技術檢視頁面控制元件
    Then 航線 tab 具備 aria-selected 狀態
    And 日期範圍按鈕具備 aria-pressed 狀態
    And 圖表具備 role="img" 與描述性 aria-label

  @edge-case @p2
  Scenario: 使用者偏好減少動畫時停用動畫
    Given 我開啟系統的「減少動畫」偏好（prefers-reduced-motion）
    When 我開啟票價趨勢圖頁面並 hover 資料點
    Then 不播放 tooltip 淡入動畫與載入骨架動畫

  # ============ Business Rules（商業規則）============

  @business-rules @p0
  Scenario: 每週價格取該週所有航班最低價
    Given 某一週有多個航班（東京 3 班）各有不同價格
    When 圖表繪製該週的價格點
    Then 該週價格點為所有航班中的最低價格
    And Summary 卡的「最便宜週」為全域最低價的週次並顯示金額

  @business-rules @p0
  Scenario: 平均線以全域 40 週計算且不隨篩選漂移
    Given 東京航線有 40 週每週最低價資料
    When 我篩選日期範圍為「3 個月」
    Then 平均線維持全部 40 週每週最低價的平均
    And hover 任一點的差幅計算仍以全域平均為基準

  @business-rules @p1
  Scenario: Summary 三卡隨範圍與航線更新
    Given 圖表顯示東京趨勢圖且日期範圍為「3 個月」
    When 我切換日期範圍為「全部」或切換航線到大阪
    Then Summary 卡（最便宜週 / 平均價 / 旺季高峰）即時更新
    And 旺季高峰對應的旺季區塊名稱正確

  @business-rules @p1
  Scenario: 價格定義與時間範圍限制
    Given 資料模型僅支援經濟艙、星宇直營航班
    When 我檢視圖表任一週價格
    Then 價格為去程週六出發、回程下週日的來回總價（TWD）
    And 圖表僅顯示未來 40 週範圍，超出範圍無資料
    And 不支援艙等、人數、單程與其他航空公司篩選
