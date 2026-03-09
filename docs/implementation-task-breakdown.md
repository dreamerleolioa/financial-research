# AI Stock Sentinel 實作任務拆解（Execution Plan）

> 版本：v2.0
> 更新日期：2026-03-09（Phase 1~5 全數完成，302 tests passed）

## 0) 範圍說明

本文件依據 [ai-stock-sentinel-architecture-spec.md](ai-stock-sentinel-architecture-spec.md) 拆解成可執行工程任務，供開發排程與追蹤。

---

## Phase 1：核心資料流（MVP Backend）✅

- **P1-1**：`yfinance` 抓取股票快照（`YFinanceCrawler` + `StockSnapshot`）
- **P1-2**：財經新聞清潔器（schema + LLM + heuristic fallback + CLI）
- **P1-3**：Crawler + Cleaner 整合；CLI 支援 `--news-text` / `--news-file`
- **技術債**：移除 `StockCrawlerAgent` / `build_agent()`，CLI 改走 graph 流程
- **API**：FastAPI `/health`、`/analyze` + API 合約測試

---

## Phase 2：LangGraph 協作與補抓回圈 ✅

- **P2-1**：GraphState + 節點 stub + loop guard（max_retries）
- **P2-2**：完整性判斷節點（snapshot 缺失、新聞過舊、數字不足）
- **P2-3**：RSS 自動抓取（`RssNewsClient` + `fetch_news_node`，stdlib，無外部依賴）
- **P2-4**：Graph 接進 `/analyze` API（`build_graph()` 取代 `StockCrawlerAgent`）
- **clean_node** 插入 judge → analyze 之間

---

## Phase 3：分析能力強化 ✅

- **P3-0（籌碼 Provider 抽象層）**：`FinMindProvider`（Primary）→ `TwseOpenApiProvider`（Fallback #1）→ `TpexProvider`（Fallback #2）；Router + `.TW/.TWO` 自動分流 + Schema Mapping；42 tests
- **P3-2（ContextGenerator）**：`generate_technical_context(df_price, inst_data)`，BIAS/RSI/均線/量能/籌碼敘事；29 tests
- **P3-3（Skeptic Mode + 信心分數）**：四步驟 Prompt + `adjust_confidence_by_divergence()` 多維加權；`score_node` 串接在 analyze 前
- **策略建議模板**：`strategy_generator.py` rule-based `generate_strategy()`；23 tests
- **LLM 結構化輸出**（AnalysisDetail）：JSON 解析 + fallback；`tech_insight` / `inst_insight` / `news_insight` / `final_verdict` 四欄位
- **Anthropic LLM 串接**：`ChatAnthropic` 優先，無 key 則 fallback OpenAI
- **成本安全鎖**：估算超 $1 USD → `ValueError`
- **技術位階指標**：`high_20d` / `low_20d` / `support_20d` / `resistance_20d`（`StockSnapshot.__post_init__` 自動計算）
- **Action Plan 燈號**：`calculate_action_plan_tag(rsi14, flow_label, confidence_score)` → `opportunity` / `overheated` / `neutral`
- **信心分數拆分**：`data_confidence`（資料完整度）+ `signal_confidence`（訊號強度）
- **AnalyzeResponse**：`sentiment_label`、`action_plan`、`data_sources`、`action_plan_tag` 等頂層欄位

### Phase 3 修正輪（全數完成）
- **NQ-1~6（新聞摘要品質）**：title 品質檢查、日期正規化、quality_score、前端提示
- **ND-1~5（新聞顯示拆分）**：`news_display_items` 陣列（最多 5 筆）
- **CS-1~5（信心分數可靠性）**：`derive_technical_score()` 多因子加權；`unknown` 機構資料處理
- **NM-1~7（消息面職責邊界）**：`NO_FINANCIAL_NUMBERS` 不扣分；LLM Prompt 聚焦情緒語義

---

## Phase 4：前端儀表板 ✅

- **P4-1**：React + TypeScript + Tailwind；API 串接
- **P4-2**：信心指數圓弧、快照資訊、cleaned_news 摘要（真實資料驅動）
- **P4-3**：loading 狀態、error banner
- **P4-4**：Action Plan 全寬 2×2 卡片 + `cross_validation_note`
- **P4-6**：Action Plan 燈號標籤（🟢/🔴/🔵）
- **P4-7**：`data_confidence < 60` → 信心指數卡片「資料不足」灰色提示
- **P4-8**：多筆新聞列表（最多 5 筆，每筆可點擊連結）
- **分維度小卡**：技術面/籌碼面/消息面三張小卡 + 綜合仲裁全寬卡；各卡維度燈號
- **基本面 PE Band badge**：低估（綠）/ 合理（灰）/ 高估（紅）
- **版面重構**：信心指數 + 快照資訊合併卡片；移除「分析路徑圖」卡片

---

## Phase 5：基本面估值 ✅

- **Task 1**：`FundamentalData` dataclass + `FundamentalProvider` Protocol + Error 型別
- **Task 2**：`FinMindFundamentalProvider`：PE Band + 殖利率估值 + TTM EPS；`_fetch_historical_prices()` 用 yfinance 各季末真實收盤價（修正歷史 PE 邏輯）
- **Task 3**：`fetch_fundamental_data` 工具函式（失敗回傳 error dict，不拋例外）
- **Task 4**：`generate_fundamental_context()` 敘事產生器
- **Task 5**：Graph 整合（`fetch_fundamental_node` + `GraphState` 欄位 + `builder.py`）
- **Task 6**：`AnalysisDetail.fundamental_insight` + LLM Prompt `【基本面估值】` 段落
- **Task 7**：`AnalyzeResponse.fundamental_data` + `api.py` / `main.py` 補欄位
- **Task 8**：前端基本面估值小卡（PE/殖利率，無資料時灰化）

---

## 進行中 ⏳

### 2026-03-10：Strategy Action Plan 深化

> 計劃文件：`docs/plans/2026-03-10-strategy-action-plan-deepening.md`

**目標**：深化 `action_plan` 輸出，加入保本點位提示、分批操作量化文字、If-Then 情境觸發

**Task 1：`action` 文字量化 + `breakeven_note` 新欄位**
- `generate_action_plan()` 新增 `resistance_20d` / `support_20d` 參數
- `action` 改為帶部位比例的描述（「分批佈局（首筆 30%）」/ 「短線進場（首筆 50%，確認站穩再加碼）」/ 「觀望（待訊號明確再試單）」）
- `breakeven_note`：mid_term → 「當帳面獲利達 5% 時，建議停損位上移至入場成本價」；其餘 None
- 更新測試 exact-match assertions + 新增 breakeven_note 測試

**Task 2：If-Then 情境觸發（`momentum_expectation` 擴充）**
- `institutional_accumulation` + `resistance_20d` → 「若突破 XXX 壓力則動能轉強」
- `distribution` + `support_20d` → 「若跌破 XXX 支撐則轉向 Bearish」
- `neutral` + 兩個價位 → 同時附帶突破/跌破提示
- 無價位資料時維持舊有格式（向後相容）
- 更新 `strategy_node` 補傳 `resistance_20d` / `support_20d`

---

## Phase 6：持股診斷（待開發）

> 規格文件：`docs/ai-stock-sentinel-position-diagnosis-spec.md`
> API 契約：`docs/backend-api-technical-spec.md` v3（`POST /analyze/position`）

- **Task 1**：建立 `POST /analyze/position` 路由 + `PositionState` GraphState 欄位（`entry_price` / `profit_loss_pct` / `position_status` / `trailing_stop` / `recommended_action` / `exit_reason`）
- **Task 2**：`PositionScorer`（`analysis/position_scorer.py`）：損益位階、移動停利、`recommended_action`（Hold/Trim/Exit）、`exit_reason`
- **Task 3**：持股診斷版 System Prompt（出場/保本推理強化，禁止加碼建議）
- **Task 4**：前端「我的持股」分頁（損益對照卡 + 持股版戰術卡 + 出場警示框 + 三維 Insight 小卡）

---

## Cross-cutting（跨階段）

### C-2 可觀測性與日誌
- 統一 request_id；節點耗時、失敗原因

### C-3 測試策略
- 單元測試：cleaner、計算工具
- 整合測試：crawler → cleaner → analyzer
- **規則**：每功能完成後立即補測試，PR 必須含至少一個對應測試案例

### 其他長期待辦
- Docker / Railway 部署準備
- `calculate_growth_rate` 等進階基本面指標
- `/events`（SSE）串流支援
