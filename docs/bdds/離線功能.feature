# 離線功能 — Gherkin BDD Feature
# 來源文件：
#   - docs/interaction-flows/離線功能.md（步驟 1–6 → Happy Path；異常處理表格 E1–E8 → Error Handling；邊界與限制 → Edge Cases；驗收檢查清單 → Business Rules）
#
# 情境追溯對照：
#   - Happy Path：步驟 1 首次訪問／步驟 2 再次訪問・無新資料／步驟 3 再次訪問・有新資料／步驟 4 離線訪問／步驟 5 瀏覽・切換航線／步驟 6 手動更新
#   - Error Handling：E1 首次訪問即離線／E2 離線切未載入航線／E3 背景比對失敗／E4 部分 trip 檔失敗／E5 空間不足／E6 伺服器移除檔案／E7 離線手動更新／E8 無痕或另一瀏覽器
#   - Edge Cases：邊界與限制（離線可看範圍、連網判斷、資料容量、斷線重連）
#   - Business Rules：驗收檢查清單（比對基準、快取單位、手動更新規則、無回歸）

@offline @frontend @dashboard @p0
Feature: 離線功能
  作為一個公開訪客
  我希望再次開啟頁面時以快取秒開、有網路時只補載變更的資料、沒網路時仍可瀏覽上次載入的資料
  以便降低載入等待、支援離線瀏覽並隨時可手動強制更新

  Background:
    Given 我以公開訪客身分使用星宇機票價格儀表板（無需登入）

  # ============ Happy Path（主流程：Interaction Flow 步驟 1–6）============

  @smoke @happy-path @p0
  Scenario: 首次訪問連網載入全部資料並寫入快取
    Given 我第一次開啟頁面，本地沒有任何快取資料
    And 目前連網正常
    When 我開啟頁面
    Then 頁面顯示全頁載入骨架
    And 系統連網載入 api/index.json 與目前航線的 trip 資料
    And 全部載入成功後本地產生快取並記錄更新時間
    And 頁面繪製趨勢圖並顯示更新時間
    And 「手動更新」按鈕為可用狀態

  @smoke @happy-path @p0
  Scenario: 再次訪問且伺服器無新資料時快取秒開並顯示「已是最新」
    Given 我上次訪問已成功載入資料並產生快取
    And 目前連網正常
    When 我再次開啟頁面
    Then 頁面立即以快取資料繪圖並顯示「上次更新 HH:MM」，無全頁載入等待
    And 系統於背景抓取 api/index.json 並比對 generated_at 與本地記錄
    And 比對結果相同，系統不重新抓取任何 trip 檔
    And 更新時間旁顯示「已是最新」

  @happy-path @p0
  Scenario: 再次訪問且伺服器有新資料時只補載變更部分
    Given 我上次訪問已成功載入資料並產生快取
    And 目前連網正常
    And 伺服器 generated_at 比本地記錄新（例如每週五爬蟲後）
    When 我再次開啟頁面
    Then 頁面先以快取資料立即繪圖，背景開始比對
    And 系統只補載新增或變更的 trip 檔，不重抓未變更者
    And 快取與更新時間同步更新
    And 圖表與摘要更新為最新價格，並顯示「已是最新」

  @smoke @happy-path @p0
  Scenario: 離線訪問顯示快取資料與離線橫幅
    Given 我至少成功載入過一次資料（本地有快取）
    And 目前沒有網路（如飛航模式）
    When 我開啟頁面
    Then 頁面直接以快取資料繪製趨勢圖
    And 頁首顯示離線橫幅「離線模式 · 顯示上次資料（HH:MM）」
    And 趨勢圖、航班切換、日期範圍篩選、hover 與 Summary 三卡皆可正常操作
    And 「手動更新」按鈕停用並顯示「離線中，無法更新」

  @happy-path @p0
  Scenario: 連網時切換到未載入航線會載入該航線並寫入快取
    Given 我已載入東京航線資料
    And 目前連網正常
    When 我切換到大阪航線（尚未載入）
    Then 系統連網載入大阪 trip 資料並寫入快取
    And 圖表與摘要更新為大阪航線資料

  @happy-path @p0
  Scenario: 手動更新且伺服器有新版時補載變更資料
    Given 頁面正在顯示快取資料
    And 目前連網正常
    And 伺服器 generated_at 比本地記錄新
    When 我點擊「手動更新」
    Then 系統強制重新抓取 api/index.json 並比對 generated_at
    And 系統補載新增或變更的 trip 檔並更新快取與更新時間
    And 圖表顯示最新價格並標示「已是最新」

  @happy-path @p0
  Scenario: 手動更新且伺服器無新版時維持資料並顯示「已是最新」
    Given 頁面已顯示「已是最新」狀態
    And 目前連網正常
    When 我點擊「手動更新」
    Then 系統仍強制重新抓取 api/index.json 並比對 generated_at
    And 比對結果相同，資料維持原狀
    And 頁面顯示「已是最新」

  # ============ Error Handling（異常處理：Interaction Flow 異常表格 E1–E8）============

  @error-handling @p0
  Scenario: 首次訪問即離線顯示錯誤卡並可重試（E1）
    Given 我第一次開啟頁面，本地沒有任何快取資料
    And 目前沒有網路
    When 我開啟頁面
    Then 頁面顯示錯誤卡「需要網路才能首次載入資料」
    And 錯誤卡提供「重試」按鈕
    When 我連網後點擊「重試」
    Then 頁面重新執行首次載入流程並正常顯示圖表

  @error-handling @p1
  Scenario: 離線時切換到從未載入的航線顯示提示並停留原航線（E2）
    Given 本地僅快取東京航線資料
    And 目前沒有網路
    When 我點擊「大阪」航線 tab（從未載入）
    Then 該航線 tab 顯示提示「此航線尚未下載，需連網」
    And 頁面停留原航線，不跳出錯誤卡、不白屏

  @error-handling @p0
  Scenario: 背景比對時 index.json 抓取失敗不中斷瀏覽並可重試（E3）
    Given 頁面已以快取資料顯示
    And 背景比對時 api/index.json 抓取失敗（網路抖動）
    When 我開啟頁面觸發背景比對
    Then 瀏覽與圖表操作不受中斷
    And 更新時間旁顯示「更新失敗，稍後自動重試」
    When 我之後按「手動更新」或下次開啟頁面
    Then 系統重新嘗試更新流程

  @error-handling @p1
  Scenario: 增量補載部分 trip 檔失敗時保留舊版並下次重試（E4）
    Given 伺服器有新版 generated_at
    And 背景增量補載時部分 trip 檔載入失敗
    When 系統執行增量更新
    Then 已成功的 trip 檔先更新並顯示
    And 失敗者保留舊版資料
    And 頁面顯示「部分資料更新失敗」
    When 下次自動或手動更新時
    Then 系統重試失敗的 trip 檔

  @error-handling @p1
  Scenario: 瀏覽器儲存空間不足時降級為只保留最新（E5）
    Given 瀏覽器本地儲存空間不足
    When 系統嘗試寫入快取
    Then 頁面提示「空間不足，已保留最新資料」
    And 系統自動改用「只保留最新」策略，頁面仍正常運作

  @error-handling @p1
  Scenario: 伺服器檔案被移除時一併移除本地快取（E6）
    Given 本地快取包含某航線的 trip 資料
    And 伺服器上該 trip 檔已不存在（404）
    When 背景比對或增量更新發現檔案被移除
    Then 本地對應快取一併移除
    And 頁面不顯示錯誤卡，以伺服器資料為準

  @error-handling @p1
  Scenario: 離線時按「手動更新」按鈕停用並提示（E7）
    Given 本地有快取資料
    And 目前沒有網路
    When 我檢視「手動更新」按鈕
    Then 按鈕為停用狀態並顯示「離線中，無法更新」
    When 我恢復連網
    Then 按鈕自動恢復為可用狀態

  @error-handling @p1
  Scenario: 在無痕視窗或另一瀏覽器開啟等同首次訪問（E8）
    Given 我在一般瀏覽器已成功載入並產生快取
    When 我在無痕視窗或另一瀏覽器開啟頁面
    Then 頁面視為首次訪問（快取以瀏覽器為單位）
    And 連網載入一次後該瀏覽器才有快取

  # ============ Edge Cases（邊界情況：Interaction Flow 邊界與限制）============

  @edge-case @p1
  Scenario: 離線時切換到已快取航線仍可完整操作
    Given 本地已快取東京與大阪兩條航線資料
    And 目前沒有網路
    When 我在東京與大阪航線之間切換
    Then 兩條航線圖表皆直接顯示快取資料
    And 航班切換、日期篩選、hover 與 Summary 三卡皆正常操作

  @edge-case @p1
  Scenario: 連網狀態誤判時以實際請求結果為準
    Given 瀏覽器回報連網但實際請求失敗
    When 背景比對或載入資料
    Then 請求失敗即視為離線處理，不因連網誤判而卡住
    And 離線橫幅與對應提示正確顯示

  @edge-case @p1
  Scenario: 斷線重連後不需重新整理頁面即可恢復自動更新
    Given 背景比對或更新流程因斷線而失敗
    When 網路恢復連線
    Then 系統自動恢復更新流程，不需重新整理頁面
    And 頁面資料與更新狀態回到最新

  @edge-case @p2
  Scenario: 全量資料容量在瀏覽器儲存配額內
    Given index 約 9KB 加上 159 個 trip 檔（每檔約 1–2KB）
    When 系統將全量資料寫入快取
    Then 資料量約數百 KB，於正常瀏覽器儲存配額內
    And 仍具備空間不足時的降級機制

  # ============ Business Rules（商業規則：Interaction Flow 驗收檢查清單）============

  @business-rules @p0
  Scenario: 更新比對以 index.json 的 generated_at 為唯一基準
    Given 本地快取記錄某個更新時間
    When 系統於背景比對伺服器資料
    Then 比對基準為 api/index.json 的 generated_at（每週五爬蟲後更新）
    And 比對相同即標示「已是最新」，不重新抓取任何 trip 檔
    And 比對發現伺服器版本較舊時顯示「資料可能過時」警示
    And 比對發現新版時只補載新增或變更的 trip 檔，未變更者以快取為主

  @business-rules @p0
  Scenario: 快取以瀏覽器為單位且離線能力從第二次訪問起生效
    Given 訪客在瀏覽器 A 成功載入過資料
    When 訪客在瀏覽器 B 或無痕視窗開啟頁面
    Then 瀏覽器 B 沒有快取，等同首次訪問且需連網
    And 離線能力從該瀏覽器第二次訪問起生效

  @business-rules @p1
  Scenario: 手動更新於連網時永遠可用且強制重新驗證
    Given 頁面顯示「已是最新」狀態
    And 目前連網正常
    When 我點擊「手動更新」
    Then 系統仍強制重新抓取並比對 index.json，不受自動比對結果影響
    And 離線時手動更新一律停用（E7）

  @business-rules @p1
  Scenario: 離線功能不改變既有儀表板行為
    Given 頁面以快取或連網資料顯示東京趨勢圖
    When 我操作趨勢圖、切換航線、篩選日期或檢視 Summary 三卡
    Then 既有功能行為與連網時一致，趨勢圖繪製、航線切換、篩選與 Summary 三卡無回歸
