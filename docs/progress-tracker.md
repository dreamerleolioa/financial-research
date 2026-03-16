# AI Stock Sentinel 進度追蹤

> 更新日期：2026-03-10（Phase 6 持股診斷初步完成；360 tests passed）

## 目前完成度（高層）

- **Phase 1（MVP Backend）**：100%（技術債清理完成）
- **Phase 2（LangGraph 回圈）**：100%（骨架 + judge 邏輯 + RSS 新聞抓取 + graph 整合完成）
- **Phase 3（分析能力強化）**：100%（Provider 抽象層 + 多維分析 + Skeptic Mode + 信心分數 + 成本安全鎖 + Anthropic LLM + AnalysisDetail 結構化輸出）
- **Phase 4（前端儀表板）**：100%（核心功能 + Action Plan 燈號 badge + data_confidence 提示 + 分維度分析小卡）
- **Phase 5（基本面估值）**：100%（FinMindFundamentalProvider + PE Band 歷史真實股價修正 + 殖利率 + fetch_fundamental_node + 前端基本面卡片）
- **Phase 6（持股診斷）**：100%（`POST /analyze/position` + PositionScorer + 前端我的持股分頁完成）

---

## 已完成 ✅

### Phase 1：MVP Backend + 技術債

- 專案環境（Git、venv、Makefile、.gitignore）
- `yfinance` 抓取股票快照、LLM fallback 分析介面
- 財經新聞清潔器（schema + CLI + graph `clean_node` 整合）
- FastAPI `/health`、`/analyze` + API 合約測試
- 移除 `StockCrawlerAgent` / `build_agent()`，CLI 改走 graph 流程
- 文件：README、架構需求、任務拆解、後端 API 技術規格

### Phase 2：LangGraph 回圈

- GraphState + 節點 + loop guard（max_retries）
- 完整性判斷節點（snapshot 缺失、新聞過舊、數字不足）
- RSS 自動抓取（RssNewsClient + fetch_news_node）
- Graph 接進 `/analyze` API（`build_graph()` 取代 `StockCrawlerAgent`）
- `clean_node` 插入 judge → analyze 之間

### Phase 3：分析深化

- **籌碼 Provider 抽象層**：`FinMindProvider`（Primary）→ `TwseOpenApiProvider`（Fallback #1）→ `TpexProvider`（Fallback #2）；Router + Schema Mapping；42 tests
- **ContextGenerator**（`analysis/context_generator.py`）：BIAS/RSI/均線/量能/籌碼敘事；29 tests
- **Skeptic Mode Prompt** + 四步驟強制流程（提取→對照→衝突檢查→輸出）
- **信心分數**（`confidence_scorer.py`）：多維加權模型、`data_confidence` + `signal_confidence`；28 tests
- **策略建議模板**（`strategy_generator.py`）：rule-based `generate_strategy()`；23 tests
- **LLM 結構化輸出**（AnalysisDetail）：JSON 解析 + fallback；4 tests
- **Anthropic LLM 串接**：`ChatAnthropic` 優先，無 key 則 fallback OpenAI
- **成本安全鎖**：估算超 $1 USD → `ValueError`
- **AnalyzeResponse** 新增 strategy/confidence 欄位
- **技術位階指標**：`high_20d` / `low_20d` / `support_20d` / `resistance_20d`（`StockSnapshot.__post_init__` 計算）
- **Action Plan 燈號**：後端 `calculate_action_plan_tag(rsi14, flow_label, confidence_score)` → `opportunity` / `overheated` / `neutral`
- **分維度拆解**（`AnalysisDetail`）：`tech_insight` / `inst_insight` / `news_insight` / `final_verdict`

### Phase 3 修正輪

- **新聞摘要品質優化（NQ-1~6）**：title 品質檢查、日期正規化、quality_score、前端提示；198 tests
- **新聞顯示資料拆分（ND-1~5）**：`news_display_items`（最多 5 筆）；205 tests
- **信心分數可靠性優化（CS-1~5）**：`derive_technical_score()`、多維加權、`unknown` 機構資料處理
- **LLM 消息面輸入**：`_HUMAN_PROMPT` 加入 `{news_summary}` 欄位
- **data_confidence 語義修正**：neutral/sideways 視為有取得，只有 unknown 才降低
- **AnalyzeResponse 欄位完整性**：`sentiment_label`、`action_plan`、`data_sources`
- **AnalysisDetail 結構強化**：`institutional_flow`、`sentiment_label` 欄位
- **DATE_UNKNOWN 信心分數懲罰**：`compute_confidence()` 扣 -3 分
- **消息面職責邊界（NM-1~7）**：`NO_FINANCIAL_NUMBERS` 不扣分；`news_display_items` 陣列；295 tests

### Phase 4：前端儀表板

- 串接後端 API（真實資料驅動，含載入/錯誤狀態）
- 信心指數圓弧（`confidence_score`）、快照資訊、AI 萃取摘要
- Action Plan 卡片（2×2 grid：策略方向/入場區間/防守底線/持股期間）
- 燈號標籤（`opportunity` → 🟢 / `overheated` → 🔴 / `neutral` → 🔵）
- LLM 分析報告 → 三張維度小卡（技術/籌碼/消息）+ 綜合仲裁全寬卡
- 基本面卡片右上角 PE Band badge（低估/合理/高估）
- 信心指數 + 快照資訊合併卡片；移除「分析路徑圖」卡片
- `data_confidence < 60` 時顯示「資料不足」灰色提示

### Phase 5：基本面估值

- `FundamentalData` 介面（dataclass + Protocol + Error）
- `FinMindFundamentalProvider`：PE Band + 殖利率估值 + TTM EPS
- `fetch_fundamental_data` 工具函式（失敗回傳 error dict）
- `generate_fundamental_context()` 敘事產生器
- Graph 整合：`fetch_fundamental_node` + `GraphState` 欄位 + `builder.py`
- `AnalysisDetail.fundamental_insight` + LLM Prompt `【基本面估值】` 段落
- `AnalyzeResponse.fundamental_data` + `api.py` / `main.py` 補欄位
- 前端基本面估值小卡（PE/殖利率顯示，無資料時灰化）
- **Bug Fix**：PE Band 歷史股價改用 yfinance 各季末真實收盤價

---

## 進行中 / 待完成 ⏳

> 文件規則提醒：每次完成計劃中的任務，需當日回寫對應 `docs/plans/*.md`；若該需求尚無計劃文件，需先補產計劃文件再標記完成。
> 高層後續路徑入口：`docs/research/post-new-position-strategy-optimization-roadmap.md`

### 2026-03-16：Analyze 頁策略語義調整 ⏳

> 計劃文件：`docs/plans/2026-03-17-analyze-new-position-strategy.md`
> **目標**：將 Analyze 頁底部區塊正式調整為「新倉策略建議」，與 `/analyze/position` 的持股操作建議語義切開

- [ ] **Task 1**：更新 Analyze 頁文案與標題，避免誤解為持股中的即時操作指令
- [ ] **Task 2**：保留 Position 頁既有持股操作語義，不修改 `recommended_action` / `exit_reason` 呈現
- [ ] **Task 3**：同步檢查相關文件與驗收文案，確認 `/analyze` = 新倉、`/analyze/position` = 持股

### 2026-03-16：新倉策略建議演算法優化 ⏳

> 計劃文件：`docs/plans/2026-03-17-new-position-strategy-algorithm-upgrade.md`
> **目標**：提升 `/analyze` 新倉策略建議的可靠度與可解釋性，將現行模板化規則升級為 evidence-based 的 rule-based 決策引擎

- [ ] **Task 1**：重新設計新倉策略輸入特徵與策略分級規則，降低目前三分類過度收斂的問題
- [ ] **Task 2**：擴充 `action_plan` 輸出，加入理由、觸發條件、失效條件與建議倉位強度
- [ ] **Task 3**：加入安全降級規則，於低信心、盤中或訊號衝突時限制策略積極度
- [ ] **Task 4**：補齊測試與驗證策略案例，為後續歷史回測奠定基礎

### 2026-03-10：Strategy Action Plan 深化 ✅

> 計劃文件：`docs/plans/2026-03-10-strategy-action-plan-deepening.md`
> **目標**：深化 `action_plan` 輸出，加入保本點位提示、分批操作量化文字、If-Then 情境觸發

- [x] **Task 1**：更新 `action` 文字（帶部位比例）+ 新增 `breakeven_note` 欄位
  - `strategy_generator.py` `generate_action_plan()` 新增 `resistance_20d` / `support_20d` 參數
  - `action` 改為「分批佈局（首筆 30%）」/ 「短線進場（首筆 50%，確認站穩再加碼）」/ 「觀望（待訊號明確再試單）」
  - `breakeven_note`：mid_term → 帶獲利 5% 保本提示；其餘 None
  - 更新測試 exact-match assertions + 新增 breakeven_note 測試

- [x] **Task 2**：If-Then 情境觸發（擴充 `momentum_expectation`）
  - `institutional_accumulation` + `resistance_20d` → 「若突破 XXX 壓力則動能轉強」
  - `distribution` + `support_20d` → 「若跌破 XXX 支撐則轉向 Bearish」
  - `neutral` + 兩個價位 → 同時附帶突破/跌破提示
  - 無價位資料時維持舊有格式（向後相容）
  - 更新 `strategy_node` 補傳 `resistance_20d` / `support_20d`

### Phase 6：持股診斷（`POST /analyze/position`）✅

> **計劃文件**：`docs/plans/2026-03-10-position-diagnosis.md`
> **狀態**：完成，360 tests passed

- [x] **Task 1**：`GraphState` 新增 11 個 PositionState 欄位
- [x] **Task 2**：`PositionScorer`（`compute_position_metrics` / `compute_trailing_stop` / `compute_recommended_action`，18 tests）
- [x] **Task 3**：`preprocess_node` / `strategy_node` 接入 PositionScorer；position-aware LLM prompt
- [x] **Task 4**：`POST /analyze/position` 端點（`PositionAnalyzeRequest` / `PositionAnalysis`）
- [x] **Task 5**：前端「我的持股」分頁（`PositionPage.tsx`；倉位狀態卡 / 操作建議卡 / 出場警示 banner）

---

## 目前可用指令

```bash
cd backend && make run
```

```bash
cd backend
PYTHONPATH=src ./venv/bin/python -m ai_stock_sentinel.main --symbol 2330.TW --news-text "..."
```

---
