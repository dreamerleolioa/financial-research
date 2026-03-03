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

---

## Session 拆分（建議 5 個 session）

> 原則：每個 session 只處理一條主線，必須有可驗證輸出（程式或測試），避免跨太多模組導致收斂失敗。

### Session 1（必做）｜Provider 抽象層 + 2330 實測
- 範圍：Task A1 + A2（介面、router、primary/fallback 骨架）
- 交付：可執行 `fetch_institutional_flow("2330.TW", days=5)`，即使 fallback 也要回資料或明確錯誤碼
- 驗收：`backend/utils/` 驗證腳本跑通一次

### Session 2｜Provider 正式化 + 測試
- 範圍：Task A3 + A4（欄位標準化、異常處理、錯誤碼）
- 交付：`foreign_buy/investment_trust_buy/dealer_buy/margin_delta` 穩定輸出
- 驗收：provider 單元測試（正常路徑 + fallback 路徑）

### Session 3｜Preprocess Node + ContextGenerator
- 範圍：Task B（`preprocess_node`、`generate_technical_context`）
- 交付：`analyze_node` 可接收 `technical_context` + `institutional_context`
- 驗收：BIAS/RSI 邊界值測試通過

### Session 4｜Skeptic Prompt + Rule Score
- 範圍：Task C（system prompt 與 `score_node`）
- 交付：衝突規則 `-30 / +10` 生效，輸出 `cross_validation_note`
- 驗收：至少 2 個衝突情境測試穩定通過

> 開始 Session 4 前檢查（LLM 串接提醒）：
> - 向使用者確認/索取 `ANTHROPIC_API_KEY`（已放 `backend/.env`）
> - 向使用者確認模型（預設 `claude-sonnet-4`）
> - 向使用者確認偏好（品質/成本/平衡）、輸出格式（text/JSON）、timeout/retry

### Session 5（可延後）｜前端串接
- 範圍：Task D
- 交付：前端顯示穩定 schema，不做額外 UI 擴張
- 驗收：`/analyze` 欄位完整渲染

---

## 明日單日目標（收斂版）

若只有一天，建議只鎖定：

1. 完成 Session 1（必做）
2. 若有餘裕，再做 Session 2 的測試補強

> 不建議明天同時跨到 Session 3/4，避免 context switch 過高導致交付不完整。

---

## Task A（High）｜籌碼情報來源補完

### A1. 建立 Provider 抽象層
- 新增 `InstitutionalFlowProvider` 介面
- Router 支援多 Provider 依序嘗試

### A2. 實作 Primary / Fallback
- Primary：`FinMindProvider`
- Fallback：`TwstockProvider` 或 `TwseOpenApiProvider`
- 不可依賴單一資料源

### A3. 實作 `fetch_institutional_flow`
- 先鎖定 `2330.TW`（映射 `2330`）
- 回傳欄位至少包含：
  - `foreign_buy`
  - `investment_trust_buy`
  - `dealer_buy`
  - `margin_delta`

### A4. 新增驗證腳本
- 在 `backend/utils/` 新增資料拉取驗證腳本
- 明日先跑一次真實資料確認可用性

### A-DoD
- `2330.TW` 可抓到近 5 日法人與融資欄位
- Primary 失敗可自動切換 Fallback
- 錯誤時記錄 `INSTITUTIONAL_FETCH_ERROR` 且流程不中斷

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

### C2. 規則計分（Python）
- 在 `score_node` 實作：
  - `[新聞=利多] + [法人=大賣]` → `confidence_score -30`，註記「警惕利多出貨」
  - `[新聞=利空] + [股價不跌反漲]` → `confidence_score +10`，註記「利空不跌，籌碼轉強」
- LLM 只負責輸出 `cross_validation_note` / `risks` 文案

### C-DoD
- 至少 2 個衝突情境測試可重現分數變化
- `/analyze` 回傳包含 `confidence_score` 與 `cross_validation_note`

---

## Task D（Low）｜前端展示（延後）

- 僅在 A/B/C 完成且 JSON schema 穩定後開始
- 避免重刻 UI

---

## 明日交付清單（最小可驗收）

- Provider 抽象層 + `FinMindProvider` + 一個 fallback provider
- `fetch_institutional_flow("2330.TW", days=5)` 可執行
- `backend/utils/` 驗證腳本可輸出法人欄位
- `generate_technical_context` 可產生技術與籌碼敘事
- Skeptic Prompt + rule score 完成並可輸出矛盾報告欄位

---

## 備註

- 前端不阻擋明日主要交付；若時間緊，D 可延至後續工作日。
- 若 FinMind 流量受限，優先保證 fallback 可運作，並在回應中標註資料來源。
