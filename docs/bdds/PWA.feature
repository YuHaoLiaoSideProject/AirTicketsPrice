# PWA — Gherkin BDD Feature
# 來源文件：
#   - docs/interaction-flows/PWA.md（情境 P1-A~P2-F → Happy Path；異常表格 E1–E14 → Error Handling；邊界與限制 → Edge Cases；驗收檢查清單 → Business Rules）
#   - docs/tech-decisions/PWA-2026-08-15.md（D1–D8 決策、通知承載格式、manifest 規格）
#
# 情境追溯對照：
#   - Happy Path：P1-A 可安裝（步驟 1–3）／P1-B iOS 加到主畫面（步驟 3b）／P1-C 已安裝模式（步驟 4）／P2-A 開啟票價提醒（步驟 5–6）／P2-B 收到通知與點擊（步驟 9–10）／P2-C 退訂與拒絕（步驟 7–8）／P2-D iOS 訂閱／P2-E 離線與通知並存／P2-F 系統自動觸發
#   - Error Handling：E1 權限封鎖／E2 訂閱失敗／E3 公鑰抓取失敗／E4 權限詢問被忽略／E5 訂閱過期／E6 notify 401／E7 無訂閱者／E8 iOS 未安裝／E9 未快取且離線／E10 分頁已開啟／E11 超過 3 條／E12 首次無基準／E13 滑掉通知／E14 file:// 開啟
#   - Edge Cases：邊界與限制（僅 drop_last、每週頻率、scope 基準、裝置單位、離線並存、iOS 版本、裝置通知設定、多則通知）
#   - Business Rules：驗收檢查清單（manifest 規格、maskable safe zone、HTML meta、安裝按鈕時機、下降比對基準、Worker 端點、通知承載、憑證分層、零回歸、爬蟲不變、規模與成本、E2E、文件）

@pwa @frontend @p0
Feature: PWA 可安裝與推播通知
  作為一個公開訪客（少數親友，免登入）
  我希望把票價趨勢頁安裝成主畫面 App，並在每週五票價下降時收到推播通知、點通知直接看到該航線
  以便不用天天開頁也能掌握降價時機，並獲得 App 般的使用體驗

  Background:
    Given 我以公開訪客身分使用星宇機票價格趨勢頁（無需登入）

  # ============ Happy Path（主流程：Interaction Flow 情境 P1-A ~ P2-F）============

  @smoke @happy-path @p0
  Scenario: 符合安裝條件的瀏覽器顯示「安裝 App」按鈕（P1-A）
    Given 我以連網、HTTPS 開啟頁面
    And SW 已註冊且 manifest 生效
    When 瀏覽器具備安裝資格（Chrome/Edge 桌面或 Android）
    Then 頁面顯示「安裝 App」按鈕
    And 系統暫存安裝提示，點擊按鈕時才呼叫原生安裝流程

  @smoke @happy-path @p0
  Scenario: 接受安裝後主畫面出現 App 圖示並以 standalone 開啟（P1-A）
    Given 頁面顯示「安裝 App」按鈕
    When 我點擊「安裝 App」
    Then 瀏覽器顯示原生安裝確認框
    When 我接受安裝
    Then 安裝完成，主畫面出現「票價趨勢」App 圖示
    And 之後從主畫面開啟即以 standalone 模式啟動（無瀏覽器工具列）

  @happy-path @p0
  Scenario: 取消安裝確認後按鈕保留可再次觸發（P1-A）
    Given 瀏覽器顯示原生安裝確認框
    When 我取消安裝
    Then 安裝未完成，主畫面不出現圖示
    And 「安裝 App」按鈕仍顯示，可再次點擊觸發安裝

  @happy-path @p1
  Scenario: iOS Safari 依提示「加到主畫面」後以 standalone 開啟（P1-B）
    Given 我使用 iOS Safari 開啟頁面（尚未安裝）
    When 我開啟頁面
    Then 頁面顯示「加到主畫面」逐步提示（分享 → 加到主畫面，3 步驟），不顯示「安裝 App」按鈕
    When 我依提示將頁面加到主畫面
    Then 主畫面圖示使用 apple-touch-icon（180）
    And 之後以 standalone 模式開啟

  @smoke @happy-path @p0 @regression
  Scenario: 已安裝模式隱藏安裝入口且離線能力照常（P1-C）
    Given 我已安裝 App（display-mode: standalone）
    When 我從主畫面圖示開啟頁面
    Then 頁面以 standalone 模式顯示（無瀏覽器工具列）
    And 「安裝 App」按鈕與「加到主畫面」提示皆隱藏
    And 既有離線快取與 SW 行為照常運作

  @smoke @happy-path @p0
  Scenario Outline: 頁面依權限與訂閱狀態顯示對應的提醒入口（P2-A）
    Given 我的通知權限狀態為 <permission>
    And 訂閱狀態為 <subscription>
    When 我開啟頁面
    Then 頁面顯示「<expected_ui>」
    And 頁面不自動彈出權限詢問

    Examples:
      | permission          | subscription | expected_ui                                          |
      | 未決定（default）   | 無訂閱        | 開啟票價提醒                                          |
      | 已允許（granted）   | 無訂閱        | 開啟票價提醒                                          |
      | 已允許（granted）   | 已訂閱        | 關閉票價提醒                                          |
      | 已封鎖（denied）    | —            | 拒絕引導：「通知已封鎖，請到瀏覽器網站設定中允許通知」 |

  @smoke @happy-path @p0
  Scenario: 點「開啟票價提醒」於 user gesture 下觸發權限詢問（P2-A）
    Given 頁面顯示「開啟票價提醒」按鈕
    And 通知權限狀態為未決定或已允許
    When 我點擊「開啟票價提醒」按鈕
    Then 系統立即觸發瀏覽器通知權限詢問
    And 詢問僅在此使用者點擊時發生（user gesture）

  @smoke @happy-path @p0
  Scenario: 同意權限後訂閱成功狀態變「已訂閱」（P2-A）
    Given 瀏覽器顯示通知權限詢問
    When 我同意通知權限
    Then 系統以公開的 VAPID 公鑰建立訂閱（僅用於顯示使用者可見的通知）
    And 系統將訂閱寫入推播服務（POST /subscribe）
    And 狀態變為「已訂閱」，按鈕變「關閉票價提醒」

  @smoke @happy-path @p0
  Scenario: 每週五票價下降時收到單則摘要通知（P2-B）
    Given 我已訂閱票價提醒
    And 裝置在線可接收推播
    When 每週五爬蟲完成且任一航班票價較上次下降
    Then 我收到單則摘要通知，標題為「✈️ 票價下降了！」
    And 通知內文列出下降航班、期間與新舊價格
    And 多個航班下降時合併為單則通知，不逐航班連發

  @smoke @happy-path @p0
  Scenario: 點擊通知開啟對應航線頁面（P2-B）
    Given 通知中心顯示票價摘要通知
    When 我點擊該通知
    Then 系統開啟新分頁載入頁面並帶入對應航線（/web/?route=TPE-NRT）
    And 頁面顯示該航線的趨勢圖與最新價格

  @happy-path @p0
  Scenario: 關閉票價提醒完成退訂且不再收到通知（P2-C）
    Given 我的訂閱狀態為「已訂閱」
    When 我點擊「關閉票價提醒」
    Then 系統移除本機的推播訂閱
    And 系統同步刪除推播服務上的訂閱記錄（KV）
    And 狀態回「未訂閱」，按鈕變「開啟票價提醒」
    And 之後票價下降時我不再收到通知

  @happy-path @p1
  Scenario: iOS 已加到主畫面的 PWA 可正常訂閱（P2-D）
    Given 我使用 iOS 16.4+ 的 Safari
    And 我已將頁面加到主畫面（standalone 模式）
    When 我點擊「開啟票價提醒」
    Then 系統執行權限詢問並建立訂閱（與一般流程相同）
    And 訂閱成功後狀態「已訂閱」，每週五可收到通知

  @happy-path @p1 @regression
  Scenario: 離線時點擊通知仍可開啟頁面看快取資料（P2-E）
    Given 我已訂閱且已快取東京航線資料
    And 目前沒有網路
    When 我點擊票價下降通知（route=TPE-NRT）
    Then 頁面以快取資料顯示東京航線趨勢圖
    And 頁首顯示離線橫幅
    And 我的訂閱狀態維持「已訂閱」，不因離線失效

  @smoke @happy-path @p0
  Scenario: 系統每週五自動偵測下降並廣播通知，使用者零操作（P2-F）
    Given 已達每週五爬蟲排程時間
    And 系統比對上次資料發現任一航班票價下降
    When 爬蟲完成後系統自動呼叫推播服務（附 PUSH_API_TOKEN）
    Then 推播服務對全部訂閱者廣播 Web Push 通知
    And 我無需任何操作即可收到通知

  # ============ Error Handling（異常處理：Interaction Flow 異常表格 E1–E14）============

  @error-handling @p0
  Scenario: 權限被封鎖時顯示拒絕引導且不重複詢問（E1）
    Given 通知權限狀態為「已封鎖」（先前拒絕或瀏覽器設定封鎖）
    When 我開啟頁面
    Then 頁面顯示拒絕引導文案「通知已封鎖，請到瀏覽器網站設定中允許通知」
    And 系統不嘗試再次彈出權限詢問
    When 我到瀏覽器設定允許通知後回到頁面重新點「開啟票價提醒」
    Then 系統重新執行訂閱流程

  @error-handling @p0
  Scenario: 訂閱失敗顯示可重試提示且不影響頁面瀏覽（E2）
    Given 我點「開啟票價提醒」並同意權限
    And 推播服務暫時不可用（或網路不穩）
    When 系統嘗試建立訂閱
    Then 狀態顯示「訂閱失敗，請稍後重試」
    And 按鈕可再次點擊重試
    And 頁面瀏覽與圖表功能完全不受影響
    When 我再次點擊「開啟票價提醒」
    Then 系統重新嘗試訂閱流程

  @error-handling @p1
  Scenario: 訂閱時瀏覽器連不上推播服務顯示可操作提示（E2-AbortError）
    Given 我點「開啟票價提醒」並同意權限
    And 瀏覽器無法連上自家推播服務（FCM／Mozilla push，常見於 VPN 出口或公司防火牆）
    When 系統嘗試建立訂閱並收到 AbortError
    Then 狀態顯示「通知服務連線失敗，請確認網路後重試」
    And 提示包含「VPN／公司網路／擋廣告」可操作說明
    And 按鈕可再次點擊重試
    And 頁面瀏覽與圖表功能完全不受影響

  @error-handling @p1
  Scenario: macOS Safari 未加到 Dock 就訂閱時提示安裝（E8b）
    Given 我用 macOS Safari 開啟頁面且未加到 Dock（非 standalone）
    When 我點「開啟票價提醒」
    Then 顯示「需加到 Dock（程式塢）後才收得到通知」
    And 系統不彈出權限詢問
    When 我把網站加到 Dock 後再點「開啟票價提醒」
    Then 系統正常執行訂閱流程

  @error-handling @p0
  Scenario: 取得 VAPID 公鑰失敗時停用訂閱按鈕（E3）
    Given 推播服務（Worker）未部署或暫時故障
    When 頁面嘗試取得 VAPID 公鑰失敗
    Then 「開啟票價提醒」按鈕停用
    And 頁面提示「提醒功能暫時不可用」
    And 其餘功能（圖表、航線、離線）完全正常
    When 下次頁面載入時推播服務已恢復
    Then 按鈕自動恢復可用

  @error-handling @p1
  Scenario: 權限詢問被忽略時維持未訂閱且無錯誤提示（E4）
    Given 我點「開啟票價提醒」後關閉權限詢問框
    Then 訂閱狀態維持「未訂閱」
    And 頁面不顯示任何錯誤提示
    When 我再次點「開啟票價提醒」
    Then 系統重新彈出權限詢問

  @error-handling @p1
  Scenario: 訂閱過期（push service 回 404/410）時自動清理並可重新訂閱（E5）
    Given 我的訂閱已失效（如瀏覽器資料被清除）
    When 系統廣播通知時推播服務回傳 404/410
    Then 推播服務自動刪除該失效訂閱記錄
    And 我下次開啟頁面時狀態顯示「未訂閱」
    When 我點「開啟票價提醒」
    Then 系統建立新訂閱並恢復通知

  @error-handling @p1
  Scenario: 通知發送授權失敗（401）不影響使用者且資料照常提交（E6）
    Given 爬蟲端呼叫 /notify 時 PUSH_API_TOKEN 失效
    When 系統嘗試發送通知
    Then 推播服務拒絕（401），通知不發送
    And 使用者端無任何影響與錯誤
    And GitHub Actions 該步驟標記失敗，但爬蟲資料已提交
    When 維護者輪換 PUSH_API_TOKEN 後
    Then 下週爬蟲恢復正常發送

  @error-handling @p2
  Scenario: 推播服務沒有訂閱者時空廣播回成功（E7）
    Given 推播服務沒有任何訂閱者
    When 爬蟲端呼叫 /notify
    Then 推播服務回應成功（空廣播）
    And 不發送任何通知、不出現錯誤

  @error-handling @p1
  Scenario: iOS 未加到主畫面時提示且不發無效權限請求（E8）
    Given 我使用 iOS Safari 且尚未加到主畫面
    When 我點擊「開啟票價提醒」
    Then 頁面顯示「需加到主畫面後才收得到通知」提示
    And 系統不發出權限請求
    When 我依提示加到主畫面後再點「開啟票價提醒」
    Then 系統執行正常訂閱流程

  @error-handling @p1
  Scenario: 離線點通知且目標航線未快取時顯示提示並停留原航線（E9）
    Given 我點擊通知開啟目標航線
    And 目前沒有網路
    And 該航線從未載入過（無快取）
    Then 頁面顯示「此航線尚未下載，需連網」提示
    And 頁面停留原航線，不白屏、不跳出錯誤卡

  @error-handling @p1
  Scenario: 通知對應分頁已開啟時聚焦既有分頁並切換航線（E10）
    Given 我已有開啟票價頁的分頁但顯示其他航線
    When 我點擊通知（route=TPE-NRT）
    Then 系統聚焦既有分頁並切換到東京航線
    And 不重開新分頁

  @error-handling @p0
  Scenario: 下降航班超過 3 條時只發下降幅度最大的 3 條（E11）
    Given 每週五爬蟲完成且下降航班超過 3 條
    When 系統組合通知內容
    Then 依下降幅度排序取前 3 條合併為單則摘要
    And 其餘下降航班不發送通知

  @error-handling @p1
  Scenario: 首次爬蟲無基準資料時跳過通知僅建立基準（E12）
    Given 系統首次執行爬蟲（無上次基準資料）
    When 爬蟲完成後進行下降偵測
    Then 系統跳過通知發送
    And 以本次資料建立基準
    And 下週起正常觸發下降通知

  @error-handling @p1
  Scenario: 滑掉通知時無任何後續動作（E13）
    Given 通知中心顯示票價摘要通知
    When 我滑掉或關閉該通知
    Then 通知關閉，系統不開啟頁面、不執行任何動作

  @error-handling @p2
  Scenario: file:// 本機開啟時無 SW 與推播，降級為一般頁面（E14）
    Given 我以 file:// 直接開啟本機 HTML（無 HTTPS）
    When 我開啟頁面
    Then SW 不註冊，無安裝資格、無推播能力
    And 頁面降級為純記憶體快取瀏覽（既有行為）
    When 我改以 http://localhost 或正式網址開啟
    Then SW 註冊與 PWA 能力恢復

  # ============ Edge Cases（邊界情況：Interaction Flow 邊界與限制）============

  @edge-case @p0
  Scenario: 非 drop_last 條件一律不觸發通知
    Given 系統每週爬蟲完成
    And 票價持平、上漲，或僅低於平均／創近期新低／有週摘要素材
    When 系統進行下降偵測
    Then 系統不發送任何通知
    And 本次僅「較上次抓取下降（drop_last）」為唯一觸發條件

  @edge-case @p1
  Scenario: 通知頻率與爬蟲同頻，維持每週一次
    Given 本週五爬蟲後已發送過通知
    When 同週內系統再次被觸發檢查
    Then 不會再發送額外通知
    And 通知頻率維持每週五一次

  @edge-case @p1
  Scenario: 子路徑部署下通知 deep-link 以 SW scope 為基準拼接
    Given 頁面部署於 GitHub Pages 子路徑（如 /AirTicketsPrice/web/）
    When 我點擊通知
    Then 開啟路徑以 SW scope 為基準拼接（/AirTicketsPrice/web/?route=TPE-NRT）
    And 航線參數正確套用

  @edge-case @p1
  Scenario: 訂閱以瀏覽器/裝置為單位，無跨裝置同步
    Given 我在瀏覽器 A 已訂閱票價提醒
    When 我在瀏覽器 B 或另一裝置開啟頁面
    Then 瀏覽器 B 顯示「未訂閱」
    And 瀏覽器 A 的訂閱不受影響

  @edge-case @p1
  Scenario: 訂閱狀態與離線快取彼此獨立，離線不失效
    Given 我已訂閱票價提醒且快取東京航線
    And 目前沒有網路
    When 我開啟頁面
    Then 頁面以快取資料顯示趨勢圖與離線橫幅
    And 訂閱狀態仍正確顯示「已訂閱」

  @edge-case @p2
  Scenario: iOS 16.4 以下版本收不到推播且無其他替代方案
    Given 我使用 iOS 16.4 以下版本的 Safari
    When 我嘗試訂閱票價提醒
    Then 頁面誠實提示 iOS 推播限制（需加到主畫面且 16.4+）
    And 系統不提供 email 等其他替代通知方案

  @edge-case @p2
  Scenario: 裝置通知設定（勿擾/靜音）影響通知可達性
    Given 我已訂閱且票價下降
    And 裝置開啟勿擾模式或靜音
    When 系統廣播通知
    Then 通知可達性依裝置設定而定（可能不顯示）
    And 此為裝置層級行為，頁面與訂閱狀態不受影響

  @edge-case @p2
  Scenario: 同時有多則通知時點擊一則只開啟該則航線
    Given 通知中心同時顯示多則票價通知
    When 我點擊其中一則（route=TPE-NRT）
    Then 只開啟該則對應的東京航線頁面
    And 其他通知不產生任何連動動作

  # ============ Business Rules（商業規則：Interaction Flow 驗收檢查清單）============

  @business-rules @p0
  Scenario: manifest 欄位齊全且 Lighthouse 安裝與離線稽核通過
    Given web/manifest.webmanifest 已部署
    When 瀏覽器解析 manifest
    Then 具備 name / short_name / start_url（./）/ scope（./）/ display（standalone）/ icons（192、512、512-maskable）/ theme_color / background_color / lang（zh-Hant）
    And Lighthouse「Installable」稽核通過
    And Lighthouse「離線 reload」稽核通過（既有離線不回歸）

  @business-rules @p1
  Scenario: maskable 圖示主體落在 80% safe zone 內
    Given 系統提供 icon-512-maskable.png
    When 瀏覽器以 maskable 用途裁切渲染
    Then 圖示主體落在 80% safe zone 內，不因裁切而缺角

  @business-rules @p0
  Scenario: index.html 具備 PWA 所需連結與 iOS meta
    Given index.html 已部署
    When 我檢視頁面 head
    Then 具備 rel="manifest" 連結
    And 具備 apple-touch-icon（180）
    And 具備 theme-color 與 mobile-web-app-capable meta
    And 具備 apple-mobile-web-app-capable 與 apple-mobile-web-app-status-bar-style meta

  @business-rules @p0
  Scenario: 「安裝 App」按鈕只在瀏覽器觸發安裝事件後出現
    Given 瀏覽器具備安裝條件但尚未觸發安裝事件（beforeinstallprompt）
    When 我開啟頁面
    Then 不顯示「安裝 App」按鈕
    When 瀏覽器觸發安裝事件後
    Then 按鈕出現，且已安裝（standalone）模式下不顯示

  @business-rules @p1
  Scenario: iOS 依 UA 顯示「加到主畫面」提示且圖示為 apple-touch-icon
    Given 我使用 iOS Safari
    When 我開啟頁面
    Then 依 UA 判斷顯示「加到主畫面」逐步提示
    And 不顯示「安裝 App」按鈕
    And 加到主畫面後圖示使用 apple-touch-icon（180）

  @business-rules @p0
  Scenario: 下降比對以最近一次抓取資料為基準（drop_last）
    Given 系統已有最近一次抓取的資料（上週 data/*.json 原始檔；notify 執行前 api/latest.json 已被本次覆寫，不作基準）
    When 每週五爬蟲完成後進行比對
    Then 僅當任一航班票價較該基準下降時才觸發通知
    And 比較基準為「較上次抓取」，非絕對價格或其他指標

  @business-rules @p1
  Scenario: GET /vapid-public-key 回傳前端訂閱所需公鑰
    Given 推播服務（Worker）已部署
    When 前端請求 /vapid-public-key
    Then 系統回傳 VAPID 公鑰供訂閱使用

  @business-rules @p1
  Scenario: POST /subscribe 驗證後將訂閱寫入 KV
    Given 前端已取得有效訂閱
    When 前端呼叫 POST /subscribe
    Then 系統驗證訂閱資料有效後寫入 KV
    And 無效資料被拒絕，不寫入

  @business-rules @p0
  Scenario: POST /notify 驗證 token 後對全部訂閱者廣播並清理失效訂閱
    Given 爬蟲端以 Bearer PUSH_API_TOKEN 呼叫 /notify
    When 系統執行廣播
    Then 以 VAPID 私鑰對全部有效訂閱者發送 Web Push
    And 遇到 404/410 的失效訂閱自動清理
    And 未附正確 token 的請求被拒絕（401）

  @business-rules @p0
  Scenario Outline: 通知承載符合單則摘要格式（title/body/data.url）
    Given 系統偵測到下降航班 <route> <name>
    When 系統廣播摘要通知
    Then 標題為「✈️ 票價下降了！」
    And 內文為「<route> <name> <outbound>–<return> 降至 NT$<new_price>（原 NT$<old_price>）」
    And 點擊資料帶入 /web/?route=<route>

    Examples:
      | route   | name | outbound | return | new_price | old_price |
      | TPE-NRT | 東京 | 8/22     | 8/30   | 24,120    | 26,008    |
      | TPE-KIX | 大阪 | 8/23     | 8/31   | 11,500    | 12,900    |

  @business-rules @p1
  Scenario: 憑證分層：公鑰公開、私鑰與訂閱名單只在推播服務端
    Given VAPID 公私鑰對已建立
    When 前端取得公鑰進行訂閱
    Then 公鑰可公開取得（前端使用）
    And 私鑰與訂閱名單只存在於推播服務端（secret / KV）
    And 爬蟲到推播服務以 PUSH_API_TOKEN 保護，防止陌生人灌爆 KV

  @business-rules @p0 @regression
  Scenario: 既有測試與 Lighthouse 全量零回歸
    Given Phase 1 或 Phase 2 改動已上線
    When 執行全量回歸
    Then 單元測試（node --test tests/unit/）全綠
    And e2e_smoke（69 checks）全綠
    And e2e_offline（105 checks）全綠
    And Lighthouse 複測無 best-practices 回歸

  @business-rules @p1 @regression
  Scenario: 既有爬蟲、data/ 與 api/ 維持原樣，僅追加通知呼叫
    Given 既有每週五爬蟲流程已上線
    When Phase 2 上線後執行爬蟲
    Then data/ 與 api/ 產出流程不變
    And 既有 GitHub Actions 爬蟲步驟維持原樣
    And 僅在爬蟲完成後追加呼叫推播服務（/notify）

  @business-rules @p1
  Scenario: 訂閱與通知流程以 Playwright mock push service 端到端驗證
    Given 測試環境以 mock push service 執行
    When 執行 PWA E2E 測試
    Then 訂閱流程通過
    And push 事件（收到通知）通過
    And notificationclick deep-link 開啟航線通過

  @business-rules @p2
  Scenario: 公開免登入、少數親友規模且維持 $0 成本
    Given 使用者為公開訪客
    When 任何人開啟頁面
    Then 免登入即可瀏覽、安裝與訂閱
    And 無訂閱者管理後台（訂閱由前端按鈕管理）
    And 基礎設施維持 $0（GitHub Pages + Cloudflare Workers 免費 tier）

  @business-rules @p2
  Scenario: README 與文件完整說明安裝、推播與 iOS 限制
    Given 交付文件已完成
    When 我檢視 README 與 docs/
    Then 包含「安裝 App」操作說明
    And 包含推播訂閱說明、PUSH_API_TOKEN secret 設定與 Worker 部署步驟
    And 明列 iOS 限制（需加到主畫面且 16.4+ 才收得到）
