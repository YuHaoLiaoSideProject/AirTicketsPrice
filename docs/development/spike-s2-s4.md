# Spike S2/S4 — GitHub Pages 子路徑與安裝性稽核驗證報告（Phase 1 步驟 5）

> **對應**：`docs/tech-decisions/PWA-2026-08-15.md`（Spike S2「GitHub Pages 子路徑下 SW scope 與 manifest start_url」、Spike S4「Lighthouse installable 通過條件」）；`docs/development/PWA.md` §8 步驟 5、§9.3、§9.7
> **驗證時間**：2026-08-15（Phase 1 收尾）
> **驗證方式**：本機 `python -m http.server`（repo 根，`/web/` 子路徑 serve，類比 GitHub Pages `/AirTicketsPrice/web/`）+ Playwright headless chromium + CDP
> **狀態**：✅ **S2 子路徑驗證通過** ｜ ✅ **S4 安裝性稽核通過（CDP 替代）**

---

## 1. 結論

| Spike | 結論 |
|-------|------|
| **S2（子路徑 scope / start_url）** | ✅ **通過**。以 repo 根起 `http.server` 並訪問 `http://localhost:<port>/web/` 子路徑：SW 註冊 scope = `/web/`（子路徑正確）；manifest `start_url`/`scope` 皆 `./`，解析為 `/web/`（安裝後開啟子路徑首頁）；manifest link href 解析為 `/web/manifest.webmanifest`。相對路徑策略（§9.3）成立，正式部署 `/AirTicketsPrice/web/` 同理 |
| **S4（Lighthouse installable）** | ✅ **通過（CDP 替代）**。**Lighthouse 13.x 已從 audit set 移除 PWA 安裝稽核**（`installable-manifest` / `service-worker` / `works-offline` 不存在，`--only-audits` 亦無法執行），故依計畫改以 CDP 驗證：`Page.getAppManifest`（manifest 解析 errors=[]）＋ `Page.getInstallabilityErrors`（**errors 陣列為空 = 可安裝**）。**Lighthouse 列為 MAN 手動驗證項**（MAN-11）；Lighthouse 仍跑 best-practices 確認無回歸（score 1.00、0 失敗 audit） |

**一句話**：子路徑部署下 SW scope、manifest start_url/scope 全部正確解析（相對路徑策略免硬編碼路徑前綴）；安裝性以 CDP installability errors=空 通過，Lighthouse 13 的 PWA 稽核缺位以 MAN-11 手動驗證項承接。

---

## 2. S2 — 子路徑驗證（`/web/` 類比 `/AirTicketsPrice/web/`）

### 2.1 驗證方式

```bash
cd /fork/YuHaoLiaoSideProject/AirTicketsPrice
python -m http.server <port>        # repo 根；訪問 http://localhost:<port>/web/
```

> 註：本機 8000 已被 pi-web 會話的 workflow MCP server 佔用（基礎設施服務，不可停），故使用其他 port 驗證；行為與 port 無關，結論可移植。

### 2.2 實測結果

| 驗證項目 | 預期 | 實測 | 結果 |
|---------|------|------|------|
| SW 註冊 scope | `/web/`（子路徑） | `http://localhost:<port>/web/` | ✅ |
| SW 註冊 active / controller | active 且控制頁面 | active=true、controller 存在 | ✅ |
| manifest `start_url` `./` 解析 | `/web/` | `new URL('./', location.href)` = `/web/` | ✅ |
| manifest `scope` `./` 解析 | `/web/` | `/web/` | ✅ |
| `<link rel="manifest">` href 解析 | `/web/manifest.webmanifest` | 同預期 | ✅ |
| shell cache 含 `pwa.js` | 離線 reload 前提 | `airtickets-shell-v2` 內含 `/web/pwa.js` | ✅ |

→ 以上同時為 `tests/e2e_pwa.py` 內建 check（「SW scope = /web/ 子路徑」「start_url ./ 解析為 /web/」等），可重複驗證。

### 2.3 對實作與文件的影響

- manifest `start_url`/`scope` 維持**相對路徑 `./`**（§2.2 / §9.3），不需硬編碼 `/AirTicketsPrice` 前綴 → 任何子路徑深度皆正確。
- deep-link 拼接以 `registration.scope` 為基準（F-14，`?route=TPE-NRT` → `/web/?route=TPE-NRT`），Phase 2 E2E（notificationclick）再以完整子路徑情境驗證（INT-04 / E2E-31，T12）。

---

## 3. S4 — 安裝性稽核

### 3.1 為什麼用 CDP 替代

Lighthouse 13.x（本機 `npx lighthouse` 13.4.1）已將 PWA 安裝稽核自 audit set **移除**（Lighthouse 12 起改版方向）：

- 預設 categories 僅 `performance / accessibility / best-practices / seo / agentic-browsing`，無 PWA 類別
- `--preset=experimental` 亦無 `installable-manifest` / `service-worker` / `works-offline` / `maskable-icon`
- `--only-audits=installable-manifest,service-worker,works-offline` → 產出空 categories、無對應 audit（audit 已不在套件內）

故依 Tech Decision / 測試計畫的備援路徑，改以 **CDP**（Playwright `context.newCDPSession`）執行安裝性稽核；**Lighthouse 列為 MAN 手動驗證項**（MAN-11 已更新註記）。

### 3.2 CDP 實測結果（`tests/e2e_pwa.py` 內建，persistent context 避免 incognito 假錯誤）

| CDP 指令 | 實測 | 結果 |
|---------|------|------|
| `Page.getAppManifest` → `errors` | `[]`（manifest 解析無錯誤） | ✅ |
| `Page.getAppManifest` → `url` | `…/web/manifest.webmanifest`（子路徑正確） | ✅ |
| `Page.getAppManifest` → `data` | `start_url=./`、`scope=./`、`display=standalone`、icons 192/512/maskable 齊全 | ✅ |
| `Page.getInstallabilityErrors` → `installabilityErrors` | **`[]`（空 = 可安裝）** | ✅ |

> 註：Playwright 一般 context 為 incognito，CDP 會回報 `in-incognito` 假錯誤；稽核改用 `launch_persistent_context`（真實 profile）後 errors 為真正空陣列。

### 3.3 Lighthouse 附帶結果（best-practices 無回歸，BR12）

```bash
npx lighthouse http://localhost:8010/web/ --chrome-path=<playwright chromium> --only-categories=best-practices
```

| 項目 | 結果 |
|------|------|
| best-practices score | **1.00**（0 個失敗 audit） |

---

## 4. 回歸門檻（BR12：既有測試全綠）

| 套件 | 結果 |
|------|------|
| `node --test tests/unit/*.test.js` | 85/85 ✅ |
| `python tests/e2e_smoke.py` | 69/69 ✅ |
| `python tests/e2e_offline.py` | 105/105 ✅ |
| `python tests/e2e_pwa.py`（新增 Phase 1 安裝 E2E + S2/S4 check） | 50/50 ✅ |

---

## 5. 風險與後續

| 項目 | 說明 |
|------|------|
| Lighthouse 版本變動 | 未來 Lighthouse 若恢復 PWA 稽核（或改用 plugin），可於 MAN-11 手動驗證時補跑；自動化以 CDP installability 為準（不綁工具版本，D8） |
| `in-incognito` 干擾 | CDP 稽核需以非 incognito profile 執行（`launch_persistent_context`），已內建於 `tests/e2e_pwa.py` |
| 真實安裝流程 | CDP 驗證的是「瀏覽器判定可安裝」；真實主畫面安裝 / iOS 加到主畫面仍屬 MAN-01 / MAN-02 手動驗證 |
