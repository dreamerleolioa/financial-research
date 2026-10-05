> 2026-10-02 功能範圍：產品保留個股分析、Daily Radar 與主動式 ETF。關注列表、個人持股、持股診斷與復盤已下線；舊路由導向 `/analyze`，舊 API 回傳 404。歷史資料模型、資料與 Alembic migration 保留，未執行資料刪除。AVWAP／籌碼背景採雷達標的，基本面保留 prepared universe／final raw pool，managed raw data 僅更新近期 general 分析標的。

# 前端架構規格

> 最近同步：2026-10-02。本文記錄目前已落地的前端架構事實；短期執行討論不放在這裡。
> 現行 Analyze 只呈現 deterministic 技術、籌碼、基本面與策略結果，不再提供站內 LLM 分析。本文後段若仍出現 `skip_ai` 或 AI 報告，視為退役歷史設計；對外延伸研究只保留「複製技術摘要」工作流。

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
5. `ProtectedRoute`：保護 `/analyze`、`/daily-radar`、`/active-etf`。

這個順序的重點是：API page 和 feature hooks 都能讀到 auth context 與 query client，route 保護邏輯仍集中在入口，不分散到各 page。

## App Shell 與響應式導覽

`frontend/src/App.tsx` 是登入後共用的 App Shell，只負責產品框架、導覽、使用者控制、主題切換與 route outlet，不承接任何 domain data flow。

- `1024px` 以上使用 224px 左側欄，主要流程為個股分析、盤後觀察雷達與主動式 ETF 持股追蹤。
- 桌面 App Shell 使用完整 viewport 寬度，側欄固定貼齊左側；主要內容從側欄後方開始，左對齊並限制最大寬度為 1440px，超寬螢幕的剩餘空間保留在右側。
- `1024px` 以下使用 56px 頂部列與固定三項底部導覽，標籤為分析、雷達、ETF。
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
- Analyze、Daily Radar 與 Active ETF 的空狀態沿用各自 workflow 語義。空狀態文案不得把沒有候選或沒有 ETF 持股變化解讀為投資結論。
- Modal 在窄螢幕使用底部 sheet，在桌面置中；drawer 固定從右側進入。兩者使用語意 surface、14px 圓角、低眩光遮罩與一致的 close control。
- Dialog 必須提供 `role="dialog"` 或 `role="alertdialog"`、`aria-modal`、可讀 label，並支援 Escape 關閉。包含長表單或長內容時，scroll 應限制在 overlay 內並使用 `overscroll-contain`。
- 非必要動效保持短促，只用於按壓回饋與資料更新提示。`prefers-reduced-motion` 啟用時必須移除 refresh highlight 與非必要 transition，不得讓動效成為理解狀態的唯一方式。

## Frontend E2E Quality Gate

`frontend/e2e/` 使用 Playwright Chromium 保護已驗收的跨路由使用流程。E2E 是 browser-level contract，不是視覺 snapshot：

- `playwright.config.ts` 自行啟動隔離的 4173 Vite server，不使用開發者正在操作的 5173，也不沿用既有 4173 process。
- 測試以 browser context localStorage 與 route interception 提供固定 auth user、假 token 和 deterministic API fixtures，不依賴個人 Google session、production data 或真實 backend。
- Google OAuth script 在 E2E 中明確阻擋；測試只驗證 login/callback UI、一次性 state 成功／拒絕路徑、protected route 與 recovery path，不嘗試自動化第三方 Google 登入頁。
- 穩定 selector 優先使用 role、accessible name、label 與 route URL。只有語意 selector 無法唯一描述互動時才可增加 test id；不得以 Tailwind class、DOM 深度或像素位置作主要契約。
- 核心保護範圍包括 App Shell 導覽、theme persistence、Analyze copy-to-AI、Radar 與 Active ETF drawer keyboard focus，以及 1280px、1024px、375px、320px 的無水平溢出。
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
- Cache update：必要時可用 `queryClient.setQueryData` 更新局部 cache，並維持同一 query key 的資料一致性。

頁面本地 state 只保留 UI state：

- Modal 開關與目前選中的 item。
- Form input。
- 展開哪一筆 history。
- 批次分析進度。
- 即時分析 modal 的 loading/error/result。

避免把 API response 複製進 page state 後再手動同步，因為這會造成 列表、摘要和明細 之間出現 stale UI。

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


## Daily Radar Surface

最新結果由 `features/daily-radar/queries.ts` 的 TanStack Query hook 管理，快取 60 秒。切頁返回先呈現快取，過期時背景更新；手動更新失敗保留上次結果並明示錯誤。沒有公開結果的 404 視為空結果。讀取支援 AbortSignal，登入身分切換仍取消並清除全部 query cache。

`DailyRadarPage` 是每日觀察清單，不是交易指令頁。列表使用後端已排序的 candidates；前端不得因試驗版 AVWAP trace 重新排序、重新分類或調整風險標籤。

- Run status：掃描日、執行狀態、候選數與資料新鮮度使用同一個狀態區呈現。各資料源日期預設收合，只有使用者展開時才顯示完整清單；資料落後時仍須顯示明確警示。
- Bucket filters：候選分類篩選在捲動時保持可用，窄螢幕改為區塊內水平捲動，不得造成整頁水平溢出。
- Candidate list：以固定比較欄顯示 symbol/name、repeat status、bucket、風險標籤與操作。列表不重複顯示分類研究論述，也不顯示 internal score、bucket score、rule code 或 raw backend identifier；單股完整分析 link 移至 detail drawer 頂部。
- Detail drawer：顯示觀察理由、隔日觀察點、失效條件、背景脈絡、`input_snapshot.phase1_avwap_context` 的試驗版 AVWAP 脈絡、可讀規則細節與資料日期。技術細節不得顯示分類分數、規則代碼或完整 raw input snapshot。
- 試驗版 AVWAP trace：只在 detail drawer 顯示 anchors、距離、資料日期、dataset、adjustment mode 與 missing snapshot 狀態；不得改 Daily Radar scoring/ranking/bucket/matched rules。

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

- `frontend/src/lib/analysisSchemas.ts`
  - 驗證 `POST /analyze`
  - 目標：分析結果頂層 contract、analysis detail、news display、action plan、errors、`technical_profile`、Phase 1 `phase1_observation` trace、`chip_stability_context`
- `frontend/src/lib/activeEtfSchemas.ts`
  - 驗證 `GET /active-etf-holdings/daily`
  - 目標：coverage、fund status、MoneyDJ 來源證據、summary 計數、變化只能引用可比較基金，以及可安全開啟的 HTTP(S) 來源網址；舊 verification contract 通過驗證後正規化成 MoneyDJ-only 顯示模型

Schema 採用「核心欄位必須符合、額外欄位 passthrough」策略。這能攔下破壞性 contract drift，同時允許後端新增 metadata。

## Display Metadata

股票名稱屬於 display metadata，不在前端自行查資料源。後端會在 Analyze、Daily Radar response 中提供 `symbol_name` 或 `name`；前端顯示時採用「名稱優先、代碼保留」：

- 有名稱：顯示 `台積電 2330.TW` 或主行 `台積電`、次行 `2330.TW`。
- 無名稱：fallback 為原本的 `2330.TW`。

這個欄位不得參與策略、排序、風險計算或 cache key 判斷。

## API Client Layer

`frontend/src/lib/apiClient.ts` 集中 URL、token 與一般 JSON request。一般 domain API client 應透過 `requestJson`；公開 Daily Radar client 以 `apiUrl` 組 URL 並保留專屬錯誤分類與 AbortSignal：

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

## 功能退役邊界

關注列表、個人持股與未接入頁面的歷史趨勢元件已刪除。個股分析、Daily Radar 與主動式 ETF 是現行入口；歷史資料表與後端分析所需的昨日 context 保留。API 客戶端不再保留 `historyApi.ts`，前端也不再保留 `ConfidenceChart` 或持股專用 async-map helpers。

## 驗證命令

```bash
cd frontend
pnpm run build
pnpm run lint
```

## Daily Radar 模組邊界

- `pages/DailyRadarPage.tsx`：查詢狀態與頁面組合。
- `features/daily-radar/queries.ts`：TanStack Query key、快取與取消請求。
- `features/daily-radar/presentation.ts`：顯示標籤、格式與排序。
- `components/daily-radar/RunSummary.tsx`：批次摘要及候選篩選。
- `components/daily-radar/CandidateList.tsx`、`CandidateDetailDrawer.tsx`：清單、研究資訊、明細及焦點管理。
- `components/daily-radar/QueryStates.tsx`：載入、錯誤與空資料狀態。
