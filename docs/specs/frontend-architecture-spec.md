# 前端架構規格

> 最近同步：2026-08-31。本文記錄目前已落地的前端架構事實；短期執行討論不放在這裡。
> 現行 Analyze 與 Portfolio 只呈現 deterministic 技術、籌碼、基本面與策略結果，不再提供站內 LLM 分析。本文後段若仍出現 `skip_ai` 或 AI 報告，視為退役歷史設計；對外延伸研究只保留「複製技術摘要」工作流。

## 技術棧

- Runtime/build：React 19、TypeScript 5.9、Vite 8、pnpm 10。
- Routing：React Router 7，路由集中在 `frontend/src/main.tsx`。
- Styling：Tailwind CSS 4，主樣式入口為 `frontend/src/index.css`。
- Auth：`@react-oauth/google` + `frontend/src/stores/auth.tsx`。
- Server state：TanStack Query v5。
- API boundary validation：Zod 4。
- Static checks：`pnpm run build`、`pnpm run lint`。

## Provider 與路由邊界

`frontend/src/main.tsx` 是前端組裝根節點，目前 provider 順序如下：

1. `GoogleOAuthProvider`：提供 Google OAuth client context。
2. `QueryClientProvider`：提供 TanStack Query cache、request state 與 invalidation 能力。
3. `BrowserRouter`：以 `APP_BASE_URL` 作為 basename。
4. `AuthProvider`：管理登入狀態與 token。
5. `ProtectedRoute`：保護 `/analyze`、`/watchlist`、`/portfolio`、`/portfolio/closed`、`/daily-radar`、`/active-etf`。

這個順序的重點是：API page 和 feature hooks 都能讀到 auth context 與 query client，route 保護邏輯仍集中在入口，不分散到各 page。

## App Shell 與響應式導覽

`frontend/src/App.tsx` 是登入後共用的 App Shell，只負責產品框架、導覽、使用者控制、主題切換與 route outlet，不承接任何 domain data flow。

- `1024px` 以上使用 224px 左側欄，主要流程為個股分析、關注列表、持股管理、盤後觀察雷達與主動式 ETF 持股追蹤。
- 桌面 App Shell 使用完整 viewport 寬度，側欄固定貼齊左側；主要內容從側欄後方開始，左對齊並限制最大寬度為 1440px，超寬螢幕的剩餘空間保留在右側。
- `1024px` 以下使用 56px 頂部列與固定五項底部導覽，標籤為分析、關注、持股、雷達、ETF。
- `/portfolio` 與 `/portfolio/closed` 保留既有 URL，但在導覽上屬於同一個持股管理 family；桌面在側欄顯示持有中、已結案子項目，行動版在內容頂部顯示子檢視切換。
- 共用導覽與 icon 定義集中在 `frontend/src/components/app-shell/AppNavigation.tsx`。Route page 不自行建立另一套全域導覽。
- App Shell 提供 `跳至主要內容` 連結。每個登入後 route 由 App Shell 產生唯一的 page-level `h1`；頁面內可見區塊從 `h2` 開始，避免產品名稱與 route 標題互相競爭。
- App Shell 不設定 320px 固定最小寬度。320px viewport 在有傳統垂直捲軸時，可用內容寬度可能小於 320px，外框必須跟隨 `documentElement.clientWidth` 收縮，不能產生水平頁面捲動。

主題 token 集中在 `frontend/src/index.css`，以 OKLCH 定義 canvas、shell、surface、文字、邊界、accent、signal、positive 與 negative 等語意角色。既有 route component 在後續頁面重構前，暫時透過相容色階把舊 `indigo-*` utility 映射到新的墨綠 accent；新 App Shell 與新共用樣式不得再新增 indigo 作為產品語意。

## Authentication、空狀態與 Overlay

登入前與登入後使用同一套產品識別、語意色彩和主題切換規則，但保持不同的資訊密度：

- `/login` 與 `/login/callback` 共用 `frontend/src/components/auth/AuthenticationShell.tsx`。桌面以研究流程與登入操作形成雙欄；窄螢幕只保留產品識別、主題切換與當前登入狀態。
- `AuthenticationShell` 只負責呈現。Google OAuth flow、redirect URI、token 保存、`/auth/me` 驗證與成功後導向仍由既有 auth store 和 route page 管理，不得為了視覺調整改寫登入契約。
- Google OAuth redirect flow 必須在 `/login` 產生密碼學安全的一次性 `state`，點擊登入時寫入 `sessionStorage` 並送往 Google；`/login/callback` 在呼叫 `/auth/google/code` 前必須比對且立即消耗。缺少、不相符或已使用的 state 都只能顯示 recovery path，不得交換 authorization code。
- 尚未有資料時使用 `frontend/src/components/app-shell/WorkspaceEmptyState.tsx`。空狀態必須說明目前狀態、可採取的下一步，並在適用時直接提供主要 action；不得只顯示「沒有資料」。
- Analyze、Watchlist、Closed Portfolio、Daily Radar 與 Active ETF 的空狀態沿用各自 workflow 語義。空狀態文案不得把沒有候選、沒有持股、沒有結案紀錄或沒有持股變化解讀為投資結論。
- Modal 在窄螢幕使用底部 sheet，在桌面置中；drawer 固定從右側進入。兩者使用語意 surface、14px 圓角、低眩光遮罩與一致的 close control。
- Dialog 必須提供 `role="dialog"` 或 `role="alertdialog"`、`aria-modal`、可讀 label，並支援 Escape 關閉。包含長表單或長內容時，scroll 應限制在 overlay 內並使用 `overscroll-contain`。
- 非必要動效保持短促，只用於按壓回饋與資料更新提示。`prefers-reduced-motion` 啟用時必須移除 refresh highlight 與非必要 transition，不得讓動效成為理解狀態的唯一方式。

## Frontend E2E Quality Gate

`frontend/e2e/` 使用 Playwright Chromium 保護已驗收的跨路由使用流程。E2E 是 browser-level contract，不是視覺 snapshot：

- `playwright.config.ts` 自行啟動隔離的 4173 Vite server，不使用開發者正在操作的 5173，也不沿用既有 4173 process。
- 測試以 browser context localStorage 與 route interception 提供固定 auth user、假 token 和 deterministic API fixtures，不依賴個人 Google session、production data 或真實 backend。
- Google OAuth script 在 E2E 中明確阻擋；測試只驗證 login/callback UI、一次性 state 成功／拒絕路徑、protected route 與 recovery path，不嘗試自動化第三方 Google 登入頁。
- 穩定 selector 優先使用 role、accessible name、label 與 route URL。只有語意 selector 無法唯一描述互動時才可增加 test id；不得以 Tailwind class、DOM 深度或像素位置作主要契約。
- 核心保護範圍包括 App Shell 導覽、portfolio 子檢視、theme persistence、Analyze/Watchlist copy-to-AI、Portfolio destructive confirmation、Radar 與 Active ETF drawer keyboard focus，以及 1280px、1024px、375px、320px 的無水平溢出。
- Pull request release gate 必須依序通過 dependency install、Playwright Chromium install、lint、E2E 與 build。

## 目錄責任

| 路徑 | 責任 |
| --- | --- |
| `frontend/src/pages/` | Route-level screen，負責畫面組合、表單狀態、modal 狀態和局部互動流程 |
| `frontend/src/components/` | 跨頁可重用 UI component |
| `frontend/src/components/app-shell/` | 登入後共用導覽、route family 與響應式 App Shell component |
| `frontend/src/components/brand/` | 登入前後共用的產品識別 component |
| `frontend/e2e/` | Playwright browser-level regression tests 與 deterministic API fixtures |
| `frontend/src/stores/` | Client-only app state，目前主要是 auth |
| `frontend/src/lib/config.ts` | 前端環境變數正規化 |
| `frontend/src/lib/apiClient.ts` | HTTP request、token attach、query string、錯誤處理 |
| `frontend/src/lib/*Api.ts` | Domain API client，封裝 endpoint request |
| `frontend/src/lib/*Types.ts` | TypeScript compile-time 型別 |
| `frontend/src/lib/*Schemas.ts` | Zod runtime boundary validation |
| `frontend/src/features/*/` | Feature-level server state hooks、mutation hooks、query keys |

## Server State Policy

前端把「後端資料」與「頁面互動狀態」分開處理。

TanStack Query 管理 server state：

- API 讀取狀態：loading、error、data。
- Cache identity：透過 query key 明確定義資料面。
- Mutations：write action 成功後統一 invalidation。
- Cache update：必要時可用 `queryClient.setQueryData` 更新局部 cache，例如持股即時分析完成後更新 latest history。
- Portfolio 單筆價格刷新回應會重建完整 risk summary；若 cache 內已有其他 `refresh_status = "refreshed"` 持股，下一次單筆刷新必須把那些持股 ID 一併送出重抓，再替換 summary cache，避免先前報價與 portfolio totals 回退到 persisted final price。

頁面本地 state 只保留 UI state：

- Modal 開關與目前選中的 item。
- Form input。
- 展開哪一筆 history。
- 批次分析進度。
- 即時分析 modal 的 loading/error/result。

避免把 API response 複製進 page state 後再手動同步，因為這會造成 list、risk summary、latest history 和 decision context 之間出現 stale UI。

## Portfolio Feature Data Flow

`PortfolioPage` 的核心 read data 已改由 `frontend/src/features/portfolio/queries.ts` 提供：

- `usePortfolioItemsQuery()` -> `GET /portfolio`
- `usePortfolioRiskSummaryQuery()` -> 首次與 stale refetch 使用 `POST /portfolio/risk-summary/refresh-prices`（`portfolio_ids: null`）
- `useLatestPortfolioHistoryQuery()` -> `GET /portfolio/latest-history`
- `useDecisionContextStatusQuery()` -> `GET /portfolio/decision-context-status`
- `useLifecyclePlanQuery(id)` -> `GET /portfolio/{id}/lifecycle-plan`

Portfolio risk-summary response 已導入 Zod parser，`PortfolioPage` 直接消費 parsed response。頁面首次載入不得先顯示 `GET /portfolio/risk-summary` 的 persisted final price 再等待使用者手動刷新；query 必須直接走純價格刷新 endpoint，且不觸發 AI 分析。Phase 1C `phase1_current_day_lists` 在 Portfolio UI 只顯示目前 active holdings 對應的 AVWAP 觀察：

- `holding_management_candidates`
- `holding_risk_alerts`

Portfolio UI 標題為 `持股 AVWAP 觀察`，不得在持股頁顯示 watchlist / Daily Radar 的非持股候選。每張觀察卡的「最新價格」、狀態與「與觀察線距離」必須使用同一份 Portfolio refresh price；同時顯示價格資料日、AVWAP 資料日與 AVWAP 線價格，並明確說明 AVWAP 是技術觀察線，不等同持有成本或正式防守價。內部 anchor type / matched rule 只保留在 response trace，不得直接作為主要 UI 文案。`breakout_confirmation_candidates`、`pullback_observation_candidates` 與 `overheated_do_not_chase_candidates` 可因 API 相容性保留在 parsed response shape，但不作為 Portfolio UI 顯示來源；非持股 AVWAP 候選應回 Daily Radar 或關注清單語境呈現，不得寫入 portfolio，也不得把空陣列文案寫成交易建議或推薦結論。Phase 1 AVWAP snapshot 過期時 backend 會回 `missing_reason = "phase1_snapshot_stale"`，前端應以資料不足/風險 caveat 呈現，不把舊 snapshot 當今日觀察依據。

`GET /portfolio/risk-summary` parser 也接受每個 `position_risks[]` 的 `weekly_major_holders` 與 `chip_stability_context`。Portfolio UI 可顯示籌碼穩定性摘要，但它只作 active holding 的週頻 TDCC 補充資訊：千張大戶增加代表籌碼穩定性提升，連續增加代表籌碼愈加穩定，下降代表籌碼穩定性轉弱或集中度下降但不能單獨判定看空。前端不得用這個欄位重新排序持股、改 risk state、改 risk score 或生成加減碼文案。

Query key 由 `frontend/src/features/portfolio/queryKeys.ts` 集中定義：

- `portfolioKeys.items()`
- `portfolioKeys.riskSummary()`
- `portfolioKeys.latestHistory()`
- `portfolioKeys.decisionContext()`
- `portfolioKeys.history(id)`
- `portfolioKeys.lifecyclePlan(id)`

這些 key 是 cache topology 的正式邊界。新增 portfolio read surface 時，先補 query key，再補 query hook，最後才接 page。

## Portfolio Workspace Presentation

`/portfolio` 與 `/portfolio/closed` 是同一個持股管理 workflow 的兩個狀態。App Shell 對兩個 route 使用同一個 page-level `h1`，並以 `持有中`、`已結案` 子導覽切換；頁面內再以 section heading 說明目前狀態。

- Active risk strip：`PortfolioRiskSummaryPanel` 預設以固定 KPI 順序顯示帳戶權益（尚未記錄現金時明示回退為持股市值）、未實現損益、防守線前可能回吐與整體防守狀態。展開區可保存目前使用者的非負現金餘額，並顯示有明確 fundamental 分類來源的產業集中度及至少 20 筆重疊日報酬的持股相關性；產業 coverage 不足時不得猜測分類或顯示確定等級，必須顯示已分類／eligible 持股筆數與部分樣本警語。相關性 partial 時仍顯示可計算 pairs 與 coverage，但加權平均明示只代表可計算組合；所有相關性固定標示為歷史描述、非未來預測。防守判讀、data-quality caveat 與持股 AVWAP 觀察收在同一個可展開細節區。
- Active position rows：寬螢幕（`xl` 以上）欄位順序固定為持股、目前狀態、未實現損益、防守緩衝、價格／AI 紀錄、操作；側欄存在時，1024px 內容區不足以容納六欄，必須維持兩欄資料卡。未實現損益百分比、金額、現價與防守緩衝必須使用同一份 `position_risks[].current_price`，不得再混用 latest AI history 的舊 close。持股成本與計畫防守價是已保存、直接參與計算的基準值，畫面必須依保存精度原值呈現，不得套用市場報價 tick rounding，避免顯示值與損益／緩衝百分比互相矛盾。防守緩衝表示現價距計畫 `defense_reference.price` 尚有多少百分比，明細標示為「計畫防守價」；目前狀態 badge 只讀當次 risk-summary 的 `risk_state`，其中只有 `current_price <= defense_reference.price` 才顯示「觸及防守」。latest AI history 必須另列日期與分析狀態，不得覆蓋目前防守狀態；舊快照中的「防守條件觸發／防守條件已觸發」需在顯示邊界正規化為「風險檢查已觸發」。行動版與中等寬度使用兩欄資料卡，但維持相同閱讀順序，不以橫向表格呈現。
- Active price action：頁面首次載入與 stale refetch 會自動以 `portfolio_ids: null` 更新全部價格；每筆持股仍顯示次要 `更新價格` 與主要 `AI 分析`，頁首另提供 `更新全部價格` 與 `一鍵全部分析`。價格刷新只呼叫 `POST /portfolio/risk-summary/refresh-prices`，不得自動觸發 `/analyze/position`。單列刷新時需把目前 summary cache 中已成功刷新過的持股 id 一併送出；只有這些 symbols 本次都成功時，才以 `queryClient.setQueryData(portfolioKeys.riskSummary(), response)` 原子替換 cache，否則保留前一次 summary，避免早先更新的即時價與總計倒退。超過 request 500-id 上限時改送 `portfolio_ids: null` 刷新全部。歷史紀錄、新增批次、結案、編輯、補填操作計畫與刪除仍收進情境操作選單。
- Portfolio mutation ordering：手動價格刷新與所有持股 writes（包含 Analyze 頁新增持股）共用同一 TanStack Query mutation scope 以序列化同分頁操作；首次／stale query refresh 會記錄 request-start portfolio revision，若 request 期間 revision 改變便拒絕套用 response。每次 write 開始同步更新 localStorage revision、清除價格 overlay，成功後 invalidation 所有 portfolio read keys。若 write 失敗，也必須 invalidation portfolio read keys，因 overlay timer 已於 request 開始時移除，且網路錯誤可能無法確定 server 是否完成寫入；不得讓舊的 request-scoped 即時價無限期停留在 risk-summary cache。價格 request 完成時若 revision 已改變（包含其他分頁寫入或登入身分切換），不得套用晚到的完整 summary response，需 invalidation 並提示使用者重新刷新；套用 cache 前必須以 request-start revision 再檢查一次。成功的完整 manual refresh summary 另存最長 10 分鐘的 session overlay；後續 risk-summary refetch 若完整成功，必須清除 overlay 並採用新 response；只有 partial／failed refetch 且 revision 與持股結構 fingerprint 都未改變時才保留 overlay，避免失敗 symbol 把先前成功報價與總計倒回 persisted price，也不遮蔽其他裝置完成的新增、結案或成本／防守資料變更。Overlay 到期 timer 或其他分頁的 storage revision event 必須清除 overlay 並 invalidation risk-summary。
- Auth cache isolation：登入、登出、token 驗證失敗或其他分頁的 `auth_token` storage event 都必須先 cancel／clear 共用 QueryClient 與價格 overlay。其他分頁換帳號時，本分頁需用新 token 重新執行 `/auth/me`，並以 verification sequence 丟棄晚到的舊身分 response；不得保留前一位使用者的 user state 或 portfolio query cache。
- Active freshness：持股列分開顯示 `price_context` 的價格時間／盤中狀態與 latest history 的 AI 分析日期。價格刷新 partial failure 時保留後端 fallback 值並明示失敗，不把舊價偽裝成剛更新成功。
- Caveat hierarchy：缺少 plan、風險資料注意與資料不足仍需在持股列可見，但只作次要狀態，不得以大型警告卡壓過部位狀態與防守距離。
- Closed position groups：已結案頁只以完整交易生命週期為清單單位，期間由最終出清日決定；同一交易的所有處分批次完整保留，依序命名為 `第 N 次減碼` 與 `最終出清`，不得顯示 portfolio row id 或 group UUID 作為主要識別。卡片固定先顯示標的與日期範圍、`完整交易`、完整損益、操作流程品質與一個關鍵回饋；沒有 saved v5 review 時明示尚未產生復盤。主要動作只有 `查看完整復盤`，個別批次動作為 `查看這次處分`。
- Closed review workspace：完整交易復盤使用單一 fixed viewport dialog，不以多個 modal 分割結果、整體檢討、事件時間線與單次處分。Dialog header 固定顯示標的、日期範圍、目前筆數及上一筆／下一筆操作，只有內容區可垂直捲動；背景頁面鎖定且不可互動，Esc／關閉按鈕會關閉 dialog 並把焦點還給目前交易的觸發按鈕，Tab 焦點不得離開 dialog。閱讀順序固定為交易結果與操作流程、四面向品質、保持／改善／下次規則、生命週期指標、事件證據、個別處分與技術細節。`outcome` 與 `process_quality` 必須並列，獲利或虧損不得取代操作品質；四面向使用類別狀態，不呈現 0–100 權威分數。個人 setup 統計必須同時顯示 reviewed / eligible coverage，且不論樣本數都永久標示為描述性、非因果、不能代表未來績效。review version、group id、raw trace 與複製 evidence 收進技術細節。
- Lifecycle compatibility：Lifecycle Review v1 / v2 / v3 / v4 可讀，POST 建立 v5；舊 v3 的 `unclassified` fallback 仍不得顯示成決策脈絡不足。`insufficient`、`retrospective_only`、event ledger gap 與 market evidence gap 必須分開提示。市場缺口文案要指出事件類型、日期與缺少指標，並明示這不代表使用者沒有記錄操作原因；只有事件、費稅或部位調整等交易事實缺漏才可標成紀錄品質不足。Backend 新產生的 notes / event evidence 必須使用中文事件與市場狀態；Frontend 對舊 review 內的英文診斷、event type、market regime 與 provenance 提供相容轉譯，未知 enum 只顯示中性 fallback，不得把 snake_case 放進主要 UI。Single Trade Review 與 Lifecycle Review 的未知較新版本都只讀不降級；事後補填或進場後已修改的 plan 只作回顧脈絡，不得呈現為歷史違規或客觀分數。持有中結案 modal 必須明確送出結案原因、計畫遵循與信心水準；使用者選擇未記錄時需保存明確的 `not_recorded`，不得由前端推論。

## Analyze Technical Indicator Surface

`AnalyzePage` 與 Watchlist quick lookup 共用 `frontend/src/components/TechnicalIndicatorsPanel.tsx` 顯示技術指標。`POST /analyze` response 經 `frontend/src/lib/analysisSchemas.ts` 驗證後可包含 `technical_profile` 與 legacy `technical_indicators`：

- Analyze 研究入口需把 `skip_ai: true` 的快速資料與完整 AI 分析呈現為兩個明確選項。快速資料是 deterministic 的技術與風險資料讀取；完整分析才包含 AI 報告與近期新聞。
- 尚未送出分析時，頁面只顯示研究入口與單一研究流程說明，不預先渲染空的風險、報告與新聞卡片。這能讓第一個 viewport 聚焦在選擇標的與研究深度。
- 每次送出快速或完整分析前都必須清除前一次結果，並沿用 AbortController 中止舊 request，避免切換標的或研究深度時留下 stale result。
- 快速資料完成後可在結果區直接補做完整分析，但不得自動觸發 AI。完整報告與近期新聞只在完整分析成功後顯示。
- `technical_profile` 存在時，面板先顯示完整指標值，再於下方提供預設收合的技術分層摘要；展開後顯示技術分、主要判斷、風險與過熱濾網、輔助證據與 data-quality caveat。
- 完整指標值需在現價旁顯示 snapshot 的今日開盤／最高／最低價；`buildTechnicalIndicatorsCopyText()` 使用相同的「今日開／高／低」標籤、順序與價格格式輸出，欄位缺漏時顯示 `—`，不得由前端自行推算。
- snapshot `price_limit_status` 為 `limit_up` 或 `limit_down` 時，完整指標值需在現價旁以條件式標籤顯示「漲停」或「跌停」，並同步附註於 copy-to-AI 的現價；`normal` 與 `unknown` 不顯示標籤。若後端提供 `market_current_price_source = "twse_mis"`，現價欄優先顯示 `market_current_price` 並明示「TWSE MIS 即時」；canonical `snapshot.current_price` 與 technical profile 仍代表原分析 snapshot，不得暗示技術指標已用 MIS 價重算。漲跌停狀態與上下限價格由後端官方市場資料判斷，前端不得以昨收或固定百分比自行推算。
- 完整指標值需在成交量附近顯示後端 `technical_indicators.avg_volume_20` / `avg_volume_60`，以「20／60 日均成交量」合併呈現並同步輸出到 copy-to-AI；盤中排除未完成當日、收盤包含當日的計算口徑由後端負責，前端不得從 snapshot 自行重算；兩欄位只供顯示與複製，不改變 `technical_profile` 評分。
- 缺少 `technical_profile` 時，面板 fallback 為 legacy raw 技術指標值，不顯示分層結論。
- 缺少 raw `technical_indicators` 時，面板保留分層摘要可見性，並在完整指標值區顯示資料不足提示。
- 分層 signal row 只顯示中文狀態與 impact，不顯示 backend reason 原文；完整推理仍保留在 API trace，不作預設 UI 噪音。
- Analyze、Watchlist、Daily Radar 與 copy-to-AI 的資料缺口文案統一經 `frontend/src/lib/presentationLabels.ts` 轉換；未知 `missing_reason` 只顯示中性的資料不足說明，不得把 snake_case 代碼直接顯示在主要 UI。Analyze `errors[]` 仍保留 code 供 trace，但錯誤橫幅只顯示穩定的使用者文案，不顯示 `[ERROR_CODE]`、provider 名稱或 exception message。AVWAP 的 dataset 與 adjustment mode 也需轉成可讀名稱。
- `technical_profile.data_quality.is_final === false` 或 response `is_final === false` 時，前端需顯示盤中 caveat，不能當成完整收盤判斷。
- `technical_profile.data_quality.ohlcv_aligned === false` 時，支撐壓力相關分層需顯示 caveat；前端不得自行補 high/low 或推算支撐壓力分數。
- 前端只顯示 data-quality caveat，不直接顯示 backend `technical_profile.caveats` 的內部分層規則提醒；這些 rule trace 留在 API/debug contract。
- `chip_stability_context` 是 companion evidence，不屬於技術分層面板的 scoring bucket；若頁面呈現，應使用籌碼穩定性語言，且不得改技術分或排序。
- Watchlist quick lookup 的內容順序固定為完整指標值、試驗版 AVWAP 觀察、技術分層摘要；分層摘要需放在 AVWAP 區塊下方，避免搶在 AVWAP context 前面。

`frontend/src/lib/technicalIndicators.ts` 的 `buildTechnicalIndicatorsCopyText()` 是 copy-to-AI 專用 raw/context formatter。它必須維持中立資料包：股票、資料狀態、價格成交量、raw 技術指標、AVWAP context 與千張大戶資料。它不得輸出 `technical_profile` 的 Primary/Risk/Secondary/Display-only 分段、bucket impact、score summary、cap 後分數或任何內部 scoring 權重；此契約由 `backend/tests/test_technical_indicator_copy_contract.py` 以 source guard 保護。

Portfolio 的「整理全部技術資料」沿用 `POST /analyze` 並固定帶 `skip_ai: true`，以前端最多 3 路並行取得目前 active holdings 的 deterministic 技術快照；不得呼叫 `/analyze/position`、執行 LLM 或寫入持股分析歷史。只有 snapshot 股票代碼與持股吻合且包含 `technical_indicators` 的 response 可計為成功；HTTP 200 內含結構化錯誤但沒有可用技術 payload 時仍需列為失敗。部分標的失敗時仍可複製成功結果，失敗代碼只留在頁面狀態，不加入複製內容。跨分頁 portfolio mutation revision 事件必須 invalidation 全部 portfolio read keys；items request 本身需拒絕套用跨 revision response，並記錄最近一次成功 items response 所屬 revision。items refetch 期間或目前 revision 尚無成功 items response 時，不得開始技術批次或複製舊結果。批次簽章必須包含成本、進場日與股數，且批次開始與複製前都需比對 revision；持股內容變更使執行中批次失效時需停止排入後續標的，並維持獨立 in-flight lock，直到既有 request 全部 settle 後才能重新整理，避免新舊批次突破 3 路並行上限。`frontend/src/features/portfolio/technicalExport.ts` 只能接受 allowlist 後的成本、進場日、股數與中立技術欄位；持股成本需保留 portfolio storage 的最多兩位小數，不得套用市場報價 tick rounding。持股事實需放在各檔「技術指標摘要」標題內，多檔摘要之間以獨立一行 `---` 分隔。輸出不得加入批次標頭、序號、權重、損益率、防守參考、internal technical score、bucket、權重、分析詳情或系統交易建議。

## Portfolio Mutations

Portfolio write action 集中在 `frontend/src/features/portfolio/mutations.ts`：

- `useUpdatePortfolioItemMutation()`
- `useBackfillLifecyclePlanMutation()`
- `useAddPortfolioEntryMutation()`
- `useClosePortfolioItemMutation()`
- `useDeletePortfolioItemMutation()`

Mutation 成功後統一 invalidation：

- portfolio list
- risk summary
- latest history
- decision context
- item-specific history
- item-specific lifecycle plan

Delete mutation 會移除 item-specific query cache，再 invalidation aggregate read data。Page callback 只清理局部 UI state，不再手動 patch server state。

## Watchlist Surface

`/watchlist` 是目前登入使用者的個人關注列表，產品語義是「有興趣但尚未進入持股的觀察標的」。它與 `/portfolio` 的 active/closed position lifecycle 分離，不代表進場、部位、加碼或交易紀錄。

前端 watchlist public surface：

- route：`frontend/src/main.tsx` 以 `ProtectedRoute` 保護 `/watchlist`。
- page：`frontend/src/pages/WatchlistPage.tsx` 負責列表、刪除、備註編輯、拖拉排序預覽，以及列表內技術指標快查。
- API client：`frontend/src/lib/watchlistApi.ts` 透過 `requestJson` 呼叫 authenticated `/watchlist` endpoints，包含 `PUT /watchlist/reorder` 的完整清單排序更新。
- List density：預設列只顯示排序控制、標的、加入時間、觀察條件與主要操作。備註 textarea 只在使用者點擊「編輯備註」後展開，取消時還原 server value，儲存成功後收合；同一時間只開啟一筆備註編輯。
- Quick technical lookup：Watchlist 內的技術快查呼叫 `POST /analyze` 並帶 `skip_ai: true`，取得 deterministic `technical_profile`、legacy raw 技術指標與 snapshot，不執行完整 AI 分析；面板與 Analyze 共用 `TechnicalIndicatorsPanel`，支援複製完整 raw/context 指標摘要供外部 AI agent 深度分析。頁面可單筆查詢，也可一鍵批次補查尚未載入的關注標的；所有標的已載入後，批次按鈕改為重新快查全部。
- 試驗版 AVWAP trace：Watchlist quick lookup 會讀取 `AnalyzeResponse.phase1_observation`，並在完整技術指標值下方、技術分層摘要上方顯示可用 AVWAP anchors 或 missing snapshot 狀態。Analyze / Watchlist / copy-to-AI 顯示「現價距離 AVWAP」時必須使用 `current_distance_to_avwap_pct`；`distance_to_avwap_pct` 是 snapshot 資料日 `snapshot_close` 距離，只能作資料日 trace。這是 read-only trace，不新增 watchlist indicator endpoint，不寫入 portfolio，也不改 Daily Radar scoring/ranking。
- 籌碼穩定性補充：Watchlist quick lookup / Analyze response 可接收 `chip_stability_context`，但它不是技術指標分數的一部分。複製完整指標摘要時，若 response 有此欄位，`buildTechnicalIndicatorsCopyText()` 只輸出 `千張大戶持股比例`、`較上週變化`，以及最多 5 週歷史資料；不輸出 companion 標題、長 caveat 或 score/ranking 說明文字。
- Cross-page write：`AnalyzePage` 與 `DailyRadarPage` 可以新增關注項目；此 mutation 只保存 observation item，不影響 Daily Radar scoring/ranking，也不寫入 portfolio。

股票名稱仍遵守 display metadata 規則：watchlist response 的 `name` 只供顯示，前端不自行查資料源，也不得用於策略、排序、風險計算或 cache key 判斷。

## Daily Radar Surface

`DailyRadarPage` 是每日觀察清單，不是交易指令頁。列表使用後端已排序的 candidates；前端不得因試驗版 AVWAP trace 重新排序、重新分類或調整風險標籤。

- Run status：掃描日、執行狀態、候選數與資料新鮮度使用同一個狀態區呈現。各資料源日期預設收合，只有使用者展開時才顯示完整清單；資料落後時仍須顯示明確警示。
- Bucket filters：候選分類篩選在捲動時保持可用，窄螢幕改為區塊內水平捲動，不得造成整頁水平溢出。
- Candidate list：以固定比較欄顯示 symbol/name、repeat status、bucket、風險標籤與操作。列表不重複顯示分類研究論述，也不顯示 internal score、bucket score、rule code 或 raw backend identifier；加入關注仍保留在列表，單股完整分析 link 移至 detail drawer 頂部。
- Detail drawer：顯示觀察理由、隔日觀察點、失效條件、背景脈絡、`input_snapshot.phase1_avwap_context` 的試驗版 AVWAP 脈絡、可讀規則細節與資料日期。技術細節不得顯示分類分數、規則代碼或完整 raw input snapshot。
- 試驗版 AVWAP trace：只在 detail drawer 顯示 anchors、距離、資料日期、dataset、adjustment mode 與 missing snapshot 狀態；不得寫入 watchlist/portfolio，也不得改 Daily Radar scoring/ranking/bucket/matched rules。

## Active ETF Surface

`ActiveEtfPage` 是各家主動式 ETF 每日公開持股差異的獨立觀察面，不是 Daily Radar 的候選來源，也不改動任何 deterministic scoring、ranking 或風險標籤。

- Route 與資料：`/active-etf` 透過 `useActiveEtfDailyQuery(dataDate)` 呼叫 authenticated `GET /active-etf-holdings/daily`。Query key 必須包含資料日，切換日期不可覆蓋其他日期 cache。
- Coverage first：頁面先顯示選定資料日、預期基金數、MoneyDJ 已有資料數、可比較數與來源未提供數。有目前與前期 MoneyDJ 快照就發布變化；缺少當日快照的基金標示為「來源尚未提供」並列出最新資料日，不得用最近日期混入當日比較。
- Fund changes：桌面寬螢幕使用基金索引加密集比較表；1024px 以下使用基金 select 與卡片，避免在中等寬度壓縮欄位。搜尋與 action filter 只改 client view，不改 server response 或排序語意。
- Consensus：每個有持股變化的標的都呈現描述性彙總；達兩檔以上且方向一致時才加上多基金共識標記。提供「全部／增加／減少」client-side 快速篩選，其中增加與減少只納入對應的單一方向，排除 `mixed` 方向分歧。方向分歧必須明示，不得把跨基金同向變化描述成推薦或預測。
- Fund evidence：基金索引分別標示「已更新・N 筆變化／已更新・無持股變化／已更新・等待前期資料／來源尚未提供」。`ready + change_count=0` 代表來源資料日已更新但股數沒有差異，不得呈現成來源未更新；缺少指定資料日快照時才顯示來源未提供與最新資料日。選定基金後把 `fund.sources` 明確標為本期 MoneyDJ 來源，列出資料日、短 hash 與原始公開頁連結；舊 API 的官方來源、驗證與衝突欄位在 boundary 正規化後不得顯示。
- Detail drawer：由 MoneyDJ 快照的變化列開啟，顯示前後股數、權重、共同規模比例校正後的相對變化、資料日與擷取時間。來源證據須依本期與前期分區，各區只顯示該資料日的 MoneyDJ 來源；若舊 API 尚未提供 `evidence_periods[]`，drawer 不得把目前期 `fund.sources` 冒充整段比較證據。`likely_fund_scale_change` 為真時明示可能包含基金申贖造成的等比例調整；drawer 支援焦點圈限、Escape 關閉與觸發按鈕焦點還原。

## API Boundary Validation

TypeScript 只能保證前端程式碼的靜態型別，不能保證後端 runtime response 一定符合 contract。因此前端在高風險 API boundary 加 Zod：

- `frontend/src/lib/portfolioSchemas.ts`
  - 驗證 `GET /portfolio/risk-summary`
  - 目標：風險摘要、position risk、risk budget、data quality、`weekly_major_holders` 與 `chip_stability_context` 的核心欄位
- `frontend/src/lib/analysisSchemas.ts`
  - 驗證 `POST /analyze`
  - 目標：分析結果頂層 contract、analysis detail、news display、action plan、errors、`technical_profile`、Phase 1 `phase1_observation` trace、`chip_stability_context`
- `frontend/src/lib/activeEtfSchemas.ts`
  - 驗證 `GET /active-etf-holdings/daily`
  - 目標：coverage、fund status、MoneyDJ 來源證據、summary 計數、變化只能引用可比較基金，以及可安全開啟的 HTTP(S) 來源網址；舊 verification contract 通過驗證後正規化成 MoneyDJ-only 顯示模型

Schema 採用「核心欄位必須符合、額外欄位 passthrough」策略。這能攔下破壞性 contract drift，同時允許後端新增 metadata。

## Display Metadata

股票名稱屬於 display metadata，不在前端自行查資料源。後端會在 Analyze、Portfolio、Daily Radar response 中提供 `symbol_name` 或 `name`；前端顯示時採用「名稱優先、代碼保留」：

- 有名稱：顯示 `台積電 2330.TW` 或主行 `台積電`、次行 `2330.TW`。
- 無名稱：fallback 為原本的 `2330.TW`。

這個欄位不得參與策略、排序、風險計算或 cache key 判斷。

## API Client Layer

`frontend/src/lib/apiClient.ts` 是唯一應該直接組 HTTP request 的位置。Domain API client 應透過 `requestJson`：

- 自動加上 auth token。
- 統一處理 query string。
- 統一轉換 backend error。
- 回傳 `unknown` 給 Zod parser，或在尚未導入 schema 的 endpoint 回傳 typed response。

新增 API 時優先順序：

1. 在 `*Types.ts` 補 TypeScript type。
2. 在 `*Api.ts` 補 request function。
3. 高風險 response 在 `*Schemas.ts` 補 Zod parser。
4. Read data 用 feature query hook，不直接在 page `useEffect` 內呼叫。
5. Write action 用 feature mutation hook，不直接在 modal 內呼叫 raw API function。

## Page Responsibility

Page 可以做：

- 組裝區塊、modal、table、card。
- 管理使用者輸入與 validation message。
- 管理純 UI state，例如 expanded row、selected item、batch progress。
- 呼叫 query hook 和 mutation hook。

Page 不應做：

- 重複保存 server response 的副本。
- 在多個 callback 手動 refetch 同一批 aggregate data。
- 自己拼 API base URL 或 token。
- 在 component 內分散定義後端 contract。

## 已知後續改善

- `PortfolioPage` 仍可再拆成更小的 component，例如 risk panel、position card、modal group。
- Portfolio history 展開目前仍是 local async state；若歷史列表會被更多流程共用，可改成 `usePortfolioHistoryQuery(id, enabled)`。
- `POST /analyze/position` 尚未導入 Zod parser；目前本輪只補 `POST /analyze` 與 `GET /portfolio/risk-summary`。
- 完整介面視覺驗收後，補上登入狀態、App Shell、核心 route 與 1280px、375px、320px viewport 的 E2E regression coverage。

## 驗證命令

```bash
cd frontend
pnpm run build
pnpm run lint
```
