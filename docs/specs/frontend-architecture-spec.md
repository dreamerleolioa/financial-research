# 前端架構規格

> 最近同步：2026-06-16。本文記錄目前已落地的前端架構事實；短期執行討論不放在這裡。

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
5. `ProtectedRoute`：保護 `/analyze`、`/watchlist`、`/portfolio`、`/portfolio/closed`、`/daily-radar`。

這個順序的重點是：API page 和 feature hooks 都能讀到 auth context 與 query client，route 保護邏輯仍集中在入口，不分散到各 page。

## 目錄責任

| 路徑 | 責任 |
| --- | --- |
| `frontend/src/pages/` | Route-level screen，負責畫面組合、表單狀態、modal 狀態和局部互動流程 |
| `frontend/src/components/` | 跨頁可重用 UI component |
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
- `usePortfolioRiskSummaryQuery()` -> `GET /portfolio/risk-summary`
- `useLatestPortfolioHistoryQuery()` -> `GET /portfolio/latest-history`
- `useDecisionContextStatusQuery()` -> `GET /portfolio/decision-context-status`
- `useLifecyclePlanQuery(id)` -> `GET /portfolio/{id}/lifecycle-plan`

`GET /portfolio/risk-summary` 已導入 Zod parser，`PortfolioPage` 直接消費 parsed response。Phase 1C `phase1_current_day_lists` 在 Portfolio UI 分成兩個區塊顯示：

- `holding_management_candidates`
- `holding_risk_alerts`
- `breakout_confirmation_candidates`
- `pullback_observation_candidates`
- `overheated_do_not_chase_candidates`

Holding lists 顯示在 `試驗版今日觀察清單` 的持股區塊內，非持股 lists 顯示在獨立 `非持股觀察清單` 區塊。非持股清單只讀 watchlist / Daily Radar managed-universe projection，不得混入持股管理清單，不得寫入 portfolio，也不得把空陣列文案寫成交易建議或推薦結論。Phase 1 AVWAP snapshot 過期時 backend 會回 `missing_reason = "phase1_snapshot_stale"`，前端應以資料不足/風險 caveat 呈現，不把舊 snapshot 當今日觀察依據。

Query key 由 `frontend/src/features/portfolio/queryKeys.ts` 集中定義：

- `portfolioKeys.items()`
- `portfolioKeys.riskSummary()`
- `portfolioKeys.latestHistory()`
- `portfolioKeys.decisionContext()`
- `portfolioKeys.history(id)`
- `portfolioKeys.lifecyclePlan(id)`

這些 key 是 cache topology 的正式邊界。新增 portfolio read surface 時，先補 query key，再補 query hook，最後才接 page。

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
- page：`frontend/src/pages/WatchlistPage.tsx` 負責列表、刪除、備註編輯、拖拉排序預覽，以及列表內 raw 技術指標快查。
- API client：`frontend/src/lib/watchlistApi.ts` 透過 `requestJson` 呼叫 authenticated `/watchlist` endpoints，包含 `PUT /watchlist/reorder` 的完整清單排序更新。
- Quick technical lookup：Watchlist 內的技術快查呼叫 `POST /analyze` 並帶 `skip_ai: true`，只取得 deterministic 技術指標與 snapshot；面板支援複製完整指標摘要供外部 AI agent 深度分析。頁面可單筆查詢，也可一鍵批次補查尚未載入的關注標的；所有標的已載入後，批次按鈕改為重新快查全部。
- 試驗版 AVWAP trace：Watchlist quick lookup 會讀取 `AnalyzeResponse.phase1_observation` 並在技術快查 panel 內顯示可用 AVWAP anchors 或 missing snapshot 狀態。這是 read-only trace，不新增 watchlist indicator endpoint，不寫入 portfolio，也不改 Daily Radar scoring/ranking。
- Cross-page write：`AnalyzePage` 與 `DailyRadarPage` 可以新增關注項目；此 mutation 只保存 observation item，不影響 Daily Radar scoring/ranking，也不寫入 portfolio。

股票名稱仍遵守 display metadata 規則：watchlist response 的 `name` 只供顯示，前端不自行查資料源，也不得用於策略、排序、風險計算或 cache key 判斷。

## Daily Radar Surface

`DailyRadarPage` 是每日觀察清單，不是交易指令頁。列表使用後端已排序的 candidates；前端不得因試驗版 AVWAP trace 重新排序、重新分類或調整風險標籤。

- Candidate list：顯示 symbol/name、bucket、repeat status、風險標籤、加入關注與單股分析 link。
- Detail drawer：顯示觀察理由、背景脈絡、`input_snapshot.phase1_avwap_context` 的試驗版 AVWAP 脈絡、技術 trace 與資料日期。
- 試驗版 AVWAP trace：只在 detail drawer 顯示 anchors、距離、資料日期、dataset、adjustment mode 與 missing snapshot 狀態；不得寫入 watchlist/portfolio，也不得改 Daily Radar scoring/ranking/bucket/matched rules。

## API Boundary Validation

TypeScript 只能保證前端程式碼的靜態型別，不能保證後端 runtime response 一定符合 contract。因此前端在高風險 API boundary 加 Zod：

- `frontend/src/lib/portfolioSchemas.ts`
  - 驗證 `GET /portfolio/risk-summary`
  - 目標：風險摘要、position risk、risk budget、data quality 的核心欄位
- `frontend/src/lib/analysisSchemas.ts`
  - 驗證 `POST /analyze`
  - 目標：分析結果頂層 contract、analysis detail、news display、action plan、errors、Phase 1 `phase1_observation` trace

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
- Bundle 已超過 Vite 預設 500 kB warning，可在功能穩定後評估 route-level dynamic import。

## 驗證命令

```bash
cd frontend
pnpm run build
pnpm run lint
```
