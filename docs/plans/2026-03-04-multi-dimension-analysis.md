# 計劃：多維分析升級（模式一～三實作對齊）

> 日期：2026-03-04 執行
> 前置：`docs/ai-stock-sentinel-architecture-spec.md`、`docs/implementation-task-breakdown.md` 已更新
> 目標：先補齊資料源，再做敘事化，再做矛盾檢查，最後才進前端

---

## 明日執行策略（鎖定）

依風險與依賴關係，採固定順序：

```text
[High] A. 資料源補完（Provider abstraction + fetch_institutional_flow）
→ [Medium] B. Preprocess（generate_technical_context）
→ [Medium] C. 衝突偵測（Skeptic Prompt + rule-based score）
→ [Low] D. 前端展示（若 JSON schema 穩定再做）
```

### 執行原則（避免跨 Session 表現劣化）

- 明日只追求 **Task A 完整交付**；B/C 只在 A 全部驗收通過後才啟動
- 每個 Session 最多只承擔 **1 條主線 + 1 個可驗收輸出**（禁止同時跨 A/B/C）
- 設定 Stop Rule：若 Session 2 結束時 A 尚未穩定，直接凍結 B/C，將剩餘工作順延
- 每次暫停前必須回寫「交接快照」（見文末模板），確保下個 Session 無縫續作

### 需求修訂定案（規劃師視角）

- 資料源優先序：`FinMindProvider`（Primary）→ `TwseOpenApiProvider`（Fallback #1）→ `TpexProvider`（Fallback #2）
- 上市/上櫃碎片化對策：`InstitutionalFlowProvider` 內部自動分流，`.TW` 走 TWSE，`.TWO` 走 TPEX
- 防禦性編程：Provider 層強制 Schema Mapping，無論來源皆輸出一致 JSON；同時處理限流與欄位漂移
- 技術位階補強：`calculate_technical_indicators` 增加 `high_20d` / `low_20d` 與支撐壓力位
- 策略模板補強：輸出固定包含 `entry_zone`、`stop_loss`、`holding_period`
- Schema 補強：`analysis_detail` 新增 `strategy_type`（短線／中線／防守觀望）

---

## Session 拆分（收斂版：建議 3 個 session）

> 原則：每個 session 只處理一條主線，必須有可驗證輸出（程式或測試），避免跨太多模組導致收斂失敗。

### Session 1（必做）｜Provider Router 骨架 + 單一路徑打通
- 範圍：Task A1 + A2（介面、固定優先序、`.TW/.TWO` 分流骨架）
- 交付：`2330.TW` 可跑到資料源呼叫與錯誤碼返回（可先接受部分欄位）
- 驗收：可記錄實際命中的 provider 與 fallback 次序

### Session 2（必做）｜Schema Mapping 正式化 + 雙市場驗收
- 範圍：Task A3 + A4（統一欄位輸出、異常處理、驗證腳本）
- 交付：`2330.TW` + `6488.TWO` 皆能輸出統一 schema（核心欄位齊）
- 驗收：provider 測試覆蓋正常路徑 + fallback + 欄位漂移容錯

### Session 3（有餘裕才做）｜啟動 Task B（不碰 Task C 主體）
- 啟動條件：A-DoD 全部達成
- 範圍：僅 `preprocess_node` 與 `generate_technical_context` 骨架
- 交付：`analyze_node` 可接收 context 字串（先不要求完整敘事品質）
- 驗收：至少 1 個 BIAS/RSI 邊界測試通過

> Task C（Skeptic Prompt + Rule Score）主體延後至下一工作日；但為滿足策略輸出需求，明日先完成 `Task C3` 最小骨架與測試。

> 開始 Task C1/C2 前檢查（LLM 串接提醒）：
> - 向使用者確認/索取 `ANTHROPIC_API_KEY`（已放 `backend/.env`）
> - 向使用者確認模型（預設 `claude-sonnet-4`）
> - 向使用者確認偏好（品質/成本/平衡）、輸出格式（text/JSON）、timeout/retry

---

## 明日單日目標（收斂版）

若只有一天，建議只鎖定：

1. 完成 Session 1 + Session 2（Task A 全交付）
2. 若有餘裕，只啟動 Session 3 骨架（Task B 部分）

> 不建議明天進入 Task C 主體/D，避免 context switch 過高導致品質下滑；僅保留 `Task C3` 最小骨架與測試。

---

## Task A（High）｜籌碼情報來源補完

### A1. 建立 Provider 抽象層
- 新增 `InstitutionalFlowProvider` 介面
- Router 支援多 Provider 依序嘗試與來源追蹤
- 依 symbol suffix 自動分流：`.TW` 走上市路徑、`.TWO` 走上櫃路徑

### A2. 實作 Primary / Fallback
- Primary：`FinMindProvider`
- Fallback #1：`TwseOpenApiProvider`（優先覆蓋上市標的）
- Fallback #2：`TpexProvider`（處理上櫃標的，先完成可用骨架）
- 不可依賴單一資料源，必須符合固定優先序：`FinMind -> TWSE Open API -> TPEX`

### A3. 實作 `fetch_institutional_flow`
- 先鎖定 `2330.TW`（映射 `2330`）
- 補充驗證 `6488.TWO`（上櫃路徑 smoke test）
- 回傳欄位至少包含：
  - `foreign_buy`
  - `investment_trust_buy`
  - `dealer_buy`
  - `margin_delta`
- Provider 層必做 Schema Mapping，確保所有來源輸出一致 JSON 格式

### A4. 新增驗證腳本
- 在 `backend/utils/` 新增資料拉取驗證腳本
- 明日先跑一次真實資料確認可用性

### A-DoD
- `2330.TW` 可抓到近 5 日法人與融資欄位
- `6488.TWO` 可通過上櫃路徑基本抓取（至少 1 次成功）
- Primary 失敗可自動切換 Fallback（依固定優先序）
- 錯誤時記錄 `INSTITUTIONAL_FETCH_ERROR` 且流程不中斷
- 欄位漂移時不直接炸裂：未映射欄位需告警、已定義核心欄位仍保持穩定輸出

---

## Task B（Medium）｜Preprocess Node：把數值變劇本

### B1. 新增 `preprocess_node`
- 位置：`calculate_indicators_node` → `preprocess_node` → `analyze_node`

### B2. 實作 ContextGenerator
- 函式：`generate_technical_context(df_price, inst_data)`
- 純 Python rule-based，不呼叫 LLM
- 產出：
  - `technical_context`（BIAS/RSI/均線/量能敘事）
  - `institutional_context`（法人近三日淨買賣敘事）

### B-DoD
- `analyze_node` 接收 context 敘事字串，不依賴裸數值推理
- 單元測試覆蓋 BIAS 與 RSI 邊界值

---

## Task C（Medium）｜衝突偵測：Skeptic Mode

### C1. 改寫 System Prompt
- 強制流程：提取 → 對照 → 衝突檢查 → 只輸出事實與推論
- 明確禁止補造資料與來源
- prompt 可讀取 `confidence_score` 與 `cross_validation_note`（由前置 `score_node` 計算完畢）

### C2. 規則計分（Python）
- 在 `score_node` 實作 `adjust_confidence_by_divergence`：
  - `base_score = 50`（中性基準），clamp 至 [0, 100]
  - `sentiment=positive` + `flow_label=institutional_accumulation` + `technical=bullish` → +15，`"三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高"`
  - `sentiment=positive` + `flow_label=distribution` → -20，`"警示：基本面利多但法人同步出貨，疑似趁消息出貨，建議保守觀察"`
  - `flow_label=retail_chasing` → -15，`"散戶追高風險：融資餘額異常激增，法人同步減碼，籌碼結構偏不健康"`
  - `sentiment=negative` + `technical=bullish` → +10，`"利空不跌訊號：股價守穩支撐且技術偏強，逆勢佈局機會，需觀察持續性"`
- `cross_validation_note` 為 **rule-based 固定字串**，**不呼叫 LLM**
- LLM 在 `analyze_node` 中讀取 `confidence_score` 與 `cross_validation_note`，用於生成 `risks` / `summary`，**不得修改分數**
- Graph flow：`preprocess → score → analyze → strategy → END`（score 在 analyze 前）

### C3. 策略建議模板（新增）
- 以 Python rule-based 產出：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
- 映射規則：
  - `short_term`（1-2 週）：新聞利多 + RSI 超賣反彈
  - `mid_term`（1-3 個月）：法人吸籌 + 均線多頭排列
  - `defensive_wait`：訊號衝突或高檔乖離過大
- 價位規則：
  - 入場：若 BIAS 偏高，改為「拉回 MA20 分批佈局」
  - 停損：`近20日低點 - 3%` 或 `破 MA60` 觸發

### C4. LLM 呼叫前成本安全鎖（新增）
- 位置：`LangChainStockAnalyzer.analyze()` 呼叫 LLM 前
- 估算方式：將所有送入 prompt 的字串長度加總，除以 4 得到預估 input token 數（不安裝 tiktoken）
- 費率：sonnet-4 input $3 / million tokens
- 門檻：預估費用 > $1 USD → 拋出 `ValueError`，訊息包含估算 token 數與費用，不呼叫 LLM
- 正常請求（~640 tokens ≈ $0.002）永遠不會觸發，只防止意外傳入超大字串

### C-DoD
- 至少 2 個衝突情境測試可重現分數變化
- `/analyze` 回傳包含 `confidence_score` 與 `cross_validation_note`
- `/analyze` 回傳包含 `strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
- 至少 2 個訊號組合測試可命中對應策略模板
- 成本安全鎖：超過門檻時拋出 `ValueError`，正常請求不觸發

---

## Task D（Low）｜前端展示（延後）

- 僅在 A/B/C 完成且 JSON schema 穩定後開始
- 避免重刻 UI

---

## 明日交付清單（最小可驗收）

- Provider 抽象層 + `FinMindProvider` + `TwseOpenApiProvider` + `TpexProvider` 路由骨架
- `fetch_institutional_flow("2330.TW", days=5)` 與 `fetch_institutional_flow("6488.TWO", days=5)` 可執行
- Provider 層 Schema Mapping 生效（核心欄位統一）
- `backend/utils/` 驗證腳本可輸出法人欄位與命中來源
- 技術指標可輸出 `high_20d` / `low_20d`（供策略模板計算）

> `generate_technical_context` 與完整 Skeptic 文案可順延；但 `Task C3` 的策略模板欄位需至少完成骨架與測試。

---

## 備註

- 前端不阻擋明日主要交付；若時間緊，D 可延至後續工作日。
- 若 FinMind 流量受限，優先保證 fallback 可運作，並在回應中標註資料來源。
- TWSE/TPEX API 與欄位可能變動，明日實作以「Provider 內部 Schema Mapping + 明確告警」作為防禦性編程基線。

### 跨 Session 交接快照（每次暫停必填）

```markdown
## Handoff Snapshot — 2026-03-04 Session 3 結束

- 已完成：
  - [Task A / P3-0] InstitutionalFlowProvider 介面、Router（固定優先序 + .TW/.TWO 分流）
  - [Task A / P3-0] FinMindProvider / TwseOpenApiProvider / TpexProvider 骨架 + Schema Mapping
  - [Task A / P3-0] fetch_institutional_flow 工具函式（tools.py）
  - [Task A / P3-0] 42 個 Provider 測試全通過（Router / fallback / twse_only / schema）
  - [Task B / P3-2] analysis/context_generator.py：generate_technical_context（純 rule-based）
    - 輸出 technical_context（BIAS/RSI/均線/量能敘事）、institutional_context（法人籌碼敘事）
  - [Task B / P3-2] graph/state.py 新增：technical_context / institutional_context / institutional_flow
  - [Task B / P3-2] graph/nodes.py 新增：preprocess_node（clean → preprocess → analyze）
  - [Task B / P3-2] graph/builder.py 串接：clean → preprocess → analyze
  - [Task B / P3-2] 29 個 ContextGenerator 測試全通過（BIAS/RSI 邊界值 + preprocess_node 整合）
  - 全套測試：94 passed（含既有 65 + 新增 29）

- 進行中：
  - 無（Session 3 目標已全部達成）

- 阻塞點：
  - 無

- 下一步（單一步驟）：
  - Task C3：以 Python rule-based 實作策略建議模板
    （strategy_type / entry_zone / stop_loss / holding_period，映射規則詳見計劃文件）
  - 完成後才啟動 Task C1/C2（Skeptic Prompt + adjust_confidence_by_divergence）

- 驗收證據：
  - backend/tests/test_institutional_flow.py：42 passed
  - backend/tests/test_context_generator.py：29 passed
  - 全套：`PYTHONPATH=src python -m pytest tests/ -q` → 94 passed
```

```markdown
## Handoff Snapshot — 2026-03-04 Session 5 結束

- 已完成（本 Session）：
  - [Task C2 / P3-C2] analysis/confidence_scorer.py：adjust_confidence_by_divergence（純 rule-based）
    - 四規則優先序（三維共振+15 / 利多出貨-20 / 散戶追高-15 / 利空不跌+10），clamp [0,100]
  - [Task C2] graph/state.py 新增：confidence_score / cross_validation_note
  - [Task C2] graph/nodes.py 新增：score_node（preprocess 後、analyze 前）
    - _derive_technical_signal 輔助函式（close>ma5>ma20 且 rsi∈[50,70] → bullish 等）
  - [Task C2] graph/builder.py 更新：preprocess → score → analyze → strategy → END
  - [Task C1 / P3-C1] langchain_analyzer.py 升級為 Skeptic Mode：
    - 四步驟強制流程（提取→對照→衝突檢查→輸出事實與推論）
    - analyze() 接收 technical_context / institutional_context / confidence_score / cross_validation_note
  - [Task C1] analysis/interface.py StockAnalyzer Protocol 同步升級（四個 keyword-only 參數）
  - [Task C1] graph/nodes.py analyze_node 從 state 取上述欄位傳入 analyzer.analyze()
  - 11 個信心分數測試全通過；全套測試：128 passed（含既有 117 + 新增 11）

- 進行中：
  - 無（Task C1/C2 全部 DoD 已達成）

- 阻塞點：
  - 無

- 下一步（優先序）：
  1. Task 7.5：串接 LLM Provider（ANTHROPIC_API_KEY 在 backend/.env，模型 claude-sonnet-4）
     - 建立 anthropic langchain 整合，讓 analyze_node 真正呼叫 LLM 並回傳 summary / risks
  2. Task 8：/analyze API 回傳 confidence_score / cross_validation_note / strategy_* 欄位
  3. Task 9：整合測試（make test 全通過）

- 驗收證據：
  - backend/tests/test_confidence_scorer.py：11 passed
  - 全套：`PYTHONPATH=src python -m pytest tests/ -q` → 128 passed
```

```markdown
## Handoff Snapshot — 2026-03-04 Session 4 結束

- 已完成（本 Session）：
  - [Task C3 / P3-C3] analysis/strategy_generator.py：generate_strategy（純 rule-based）
    - 映射規則：short_term（利多+RSI<30）/ mid_term（法人吸籌+均線多頭排列）/ defensive_wait（訊號衝突/高乖離）
    - 入場規則：BIAS>5% → 拉回 MA20 分批佈局；否則現價附近分批買進
    - 停損規則：固定字串「近20日低點 - 3% 或跌破 MA60（取較寬者）」
  - [Task C3] graph/state.py 新增：strategy_type / entry_zone / stop_loss / holding_period
  - [Task C3] graph/nodes.py 新增：strategy_node（analyze → strategy → END）
  - [Task C3] graph/builder.py 串接：analyze → strategy → END
  - [Task C3] 技術指標輔助函式升為 public API（calc_bias / calc_rsi / ma），舊名保留別名
  - [Task C3] 23 個策略測試全通過（訊號組合 + 優先序 + 邊界值 + None 安全）
  - 全套測試：117 passed（含既有 94 + 新增 23）

- 進行中：
  - 無（Task C3 全部 DoD 已達成）

- 阻塞點：
  - 無

- 下一步（優先序）：
  1. Task C1/C2：Skeptic Prompt + adjust_confidence_by_divergence（需確認 ANTHROPIC_API_KEY）
  2. Task 7：升級 langchain_analyzer.py Prompt，接收 technical_context / institutional_context 敘事字串
  3. Task 8：/analyze API 回傳 strategy 欄位

- 驗收證據：
  - backend/tests/test_strategy_generator.py：23 passed
  - 全套：`PYTHONPATH=src python -m pytest tests/ -q` → 117 passed
```
