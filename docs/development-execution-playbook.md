# AI Stock Sentinel 開發執行手冊（Execution Playbook）

> 版本：v2.0
> 更新日期：2026-03-09（Phase 1~5 全數完成，302 tests passed）

## 1) 現況基線

| Phase | 完成度 | 說明 |
|-------|--------|------|
| Phase 1（MVP Backend） | **100%** | 技術債清理完成 |
| Phase 2（LangGraph 回圈） | **100%** | 骨架 + judge + RSS 抓取 + Graph 接進 API |
| Phase 3（分析能力強化） | **100%** | Provider 抽象 + 信心分數 + Skeptic Mode + LLM 串接 + 策略模板 |
| Phase 4（前端儀表板） | **100%** | 分維度小卡 + 基本面估值卡 + 信心指數合併卡 |
| Phase 5（基本面估值） | **100%** | FinMindFundamentalProvider + PE Band + 殖利率 + fetch_fundamental_node |
| Phase 6（持股診斷） | **0%** | 規格文件定稿，待開發 |

詳細進度見 `docs/progress-tracker.md`；Phase 6 規格：`docs/ai-stock-sentinel-position-diagnosis-spec.md`

---

## 2) 開發原則與週節奏

### 開發原則
- 每週只追一個主目標，避免同時多線半成品。
- 每項任務需定義 DoD（可驗收條件）與測試證據。
- 每項功能完成後，當日補上對應測試（單元或整合）再視為 Done。
- 每週五必須可 Demo（至少一條可操作流程）。
- 文件與程式同步更新，不可脫節。

### 週節奏（固定）
- 週一：規劃（目標、風險、依賴、決策）
- 週二～週四：開發與整合
- 週五：Checkpoint（Demo + Gate 判定 + 文件更新）

---

## 3) Roadmap

### Week 1~6：Phase 1~5（已完成）✅

| Week | 主目標 | 交付摘要 |
|------|--------|----------|
| Week 1 | API 契約鎖定 | AnalyzeResponse v1、`/health`、`/analyze`、合約測試 |
| Week 2 | LangGraph 最小回圈 | GraphState、judge 節點、max_retries、loop guard |
| Week 3 | RSS 新聞自動化 | RssNewsClient、metadata 標準化、`clean_node` 整合 |
| Week 4 | 分析深化 | Provider 抽象、Skeptic Mode、信心分數、ContextGenerator、策略模板 |
| Week 5 | 前端真資料串接 | 分維度小卡、Action Plan 燈號、基本面估值卡 |
| Week 6 | 穩定化 | 規格缺口補齊（NQ/ND/CS/NM 系列）、302 tests passed |

### Week 7：Strategy Action Plan 深化（進行中）

- **目標**：深化 `action_plan` 輸出，加入保本點位、分批操作量化文字、If-Then 情境觸發
- **交付**：
  - `action` 改為帶部位比例描述（首筆 30% / 首筆 50% / 觀望）
  - 新增 `breakeven_note` 欄位（mid_term 時輸出保本提醒）
  - `momentum_expectation` 附帶「若突破/跌破 XXX 則…」條件觸發
- **Checkpoint**：`generate_action_plan()` 全部新測試通過；`strategy_node` 補傳 `resistance_20d` / `support_20d`；無回歸
- **計劃文件**：`docs/plans/2026-03-10-strategy-action-plan-deepening.md`

### Week 8：Phase 6 持股診斷

- **目標**：建立 `POST /analyze/position` 獨立流程，提供倉位風險評估與出場建議
- **交付**：
  - `PositionScorer`（損益位階 + 移動停利 + `recommended_action`）
  - 持股診斷版 System Prompt（出場/保本推理強化，禁止加碼建議）
  - 前端「我的持股」分頁（損益對照卡 + 持股版戰術卡 + 出場警示框）
- **Checkpoint**：輸入 `symbol` + `entry_price` 可取得 `position_analysis`（含 `recommended_action` / `trailing_stop` / `exit_reason`）；`flow_label = distribution` 且獲利中時 `exit_reason` 非 null
- **規格文件**：`docs/ai-stock-sentinel-position-diagnosis-spec.md`

---

## 4) Gate 機制（未過不進下一階段）

| Gate | 條件 | 狀態 |
|------|------|------|
| G1（契約 Gate） | API 契約、錯誤碼、schema 定稿 | ✅ |
| G2（回圈 Gate） | LangGraph 補抓回圈與重試上限可運行 | ✅ |
| G3（分析 Gate） | 事實/情緒分離 + 指標工具 + confidence 可用 | ✅ |
| G4（整合 Gate） | 前端完成真資料串接且狀態完整 | ✅ |
| G5（驗收 Gate） | 測試、文件、風險方案齊備 | ✅ |
| G6（持股診斷 Gate） | `/analyze/position` 通過 DoD；`PositionScorer` 覆蓋率達標；前端「我的持股」可 Demo | ⏳ |

---

## 5) 每週 Checkpoint 模板（可複製）

- 週次 / 日期：
- 本週目標（1 句）：
- 已完成（可驗收項）：
- 未完成與原因：
- Gate 狀態（G1~G6）：
- 風險與阻塞（含 owner + ETA）：
- 測試結果（單元/整合/手測）：
- 指標快照（成功率/錯誤率/平均耗時）：
- 下週承諾（Top 3）：
- 文件同步確認（README / progress / breakdown）：
- 計劃文件同步確認（本週完成項目是否已回寫 plans；若原無計劃文件，是否已補產）：

---

## 6) 文件維護規範

- 進度唯一真相來源：`docs/progress-tracker.md`
- 任務唯一真相來源：`docs/implementation-task-breakdown.md`
- API 技術契約唯一真相來源：`docs/backend-api-technical-spec.md`（個股分析 + 持股診斷）
- 持股診斷功能規格唯一真相來源：`docs/ai-stock-sentinel-position-diagnosis-spec.md`
- 入口與操作唯一真相來源：`README.md`
- 每週五 Checkpoint 後 24 小時內同步更新三份文件。
- Gate 結果變更必須當日更新文件並註明原因。
- **每次完成「計劃文件中的任務」時，必須於當日更新對應計劃文件（`docs/plans/*.md`）的完成狀態與交付結果。**
- **若完成的需求原先沒有計劃文件，必須先補產一份計劃文件（至少含目標、範圍、DoD、完成紀錄）再標記任務完成。**
