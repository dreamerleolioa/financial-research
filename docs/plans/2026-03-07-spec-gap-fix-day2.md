# 計劃：規格缺口修補 Day 2（Spec Gap Fix - Day 2）

> 日期：2026-03-07
> 狀態：待執行
> 目的：完成剩餘五個 Session 的規格缺口修補（Session 3–7）
> 追蹤文件：`docs/progress-tracker.md` → 「待優化缺口（2026-03-05 規格對比發現）」、「下一輪修正（LLM Prompt 缺少消息面輸入）」、「下一輪修正（data_confidence 語義修正）」
> 原則：完成即補測試（Code Complete ≠ Task Complete；需附對應測試與驗收證據）
> 前置文件：`docs/plans/2026-03-06-spec-gap-fix-day1.md`（Session 1、2 必須已完成）

---

## 背景說明（給無脈絡的工程師）

本文件接續 Day 1 的工作，Day 1 已完成：
- Session 1：技術位階指標（`high_20d / low_20d / support_20d / resistance_20d`）
- Session 2：Action Plan 燈號（`action_plan_tag`、`institutional_flow_label`）

Day 2 修補的剩餘缺口：

3. **AnalyzeResponse 缺多個頂層欄位**：`sentiment_label`、`action_plan`（完整字典）、`data_sources`、`institutional_flow_label` 均未出現在 API 回應頂層。
4. **AnalysisDetail 結構薄弱**：目前只有 `summary / risks / technical_signal`，缺少 `institutional_flow` 與 `sentiment_label`。
5. **前端 data_confidence 提示**：後端已回傳 `data_confidence`，但前端尚未在信心指數卡片顯示「資料不足」提示。
6. **LLM Prompt 缺少消息面輸入**：`_HUMAN_PROMPT` 沒有傳入 `cleaned_news`，LLM 看不到消息面資料。
7. **`data_confidence` 語義偏差**：`neutral` 情緒被計為資料缺失，`sideways` 技術訊號也被計為資料不足。
8. **DATE_UNKNOWN 信心分數懲罰**：日期未知新聞應額外扣 -3 分並追加提示。

**Day 2 執行順序**：
- Session 3（依賴 Day 1 Session 2 完成）→ Session 4、5、6、7（互相獨立，可穿插或並行）

---

## Session 3：AnalyzeResponse 欄位完整性 + AnalysisDetail 結構強化

> **複雜度**：中（多個小改動，需確保不破壞現有測試）
> **前提**：Day 1 Session 2 已完成（`institutional_flow_label` 已由 Session 2 處理，此 Session 補齊其餘欄位）
> **對應缺口**：缺口 2（AnalyzeResponse）+ 缺口 3（AnalysisDetail）

### 範圍

**後端 API 層**：
- `AnalyzeResponse` 新增頂層 `sentiment_label: str | null`（從 `cleaned_news.sentiment_label` 浮出）
- `AnalyzeResponse` 新增 `action_plan: dict | null`（rule-based 計算，含 `action` / `target_zone` / `defense_line` / `momentum_expectation`）
- `AnalyzeResponse` 新增 `data_sources: list[str]`（依實際抓取狀況動態填入）

**後端 Analysis 層**：
- `models.py` `AnalysisDetail` 新增 `institutional_flow: str | None` 與 `sentiment_label: str | None`
- `langchain_analyzer.py` System Prompt 要求 LLM 輸出上述欄位

### 詳細任務

#### T3-1：`AnalyzeResponse` 新增 `sentiment_label`

**檔案**：`api.py`

```python
sentiment_label: str | None = None
```

response 建構：
```python
sentiment_label=(
    result.get("cleaned_news", {}).get("sentiment_label")
    if result.get("cleaned_news") else None
),
```

#### T3-2：`action_plan` dict 的 rule-based 計算

**檔案**：`backend/src/ai_stock_sentinel/analysis/strategy_generator.py`，新增 `generate_action_plan()` 函式

```python
def generate_action_plan(
    strategy_type: str,
    entry_zone: str,
    stop_loss: str,
    flow_label: str | None,
    confidence_score: int | None,
) -> dict:
    """
    由 strategy_type / flow_label / confidence_score 推導 action_plan 各欄位。
    純 rule-based Python，不呼叫 LLM。
    """
    if strategy_type == "defensive_wait":
        action = "觀望"
    elif strategy_type == "mid_term":
        action = "分批佈局"
    else:  # short_term
        action = "短線進場"

    momentum = (
        "強（法人集結中）" if flow_label == "institutional_accumulation"
        else "弱（法人出貨中）" if flow_label == "distribution"
        else "中性"
    )

    return {
        "action": action,
        "target_zone": entry_zone,
        "defense_line": stop_loss,
        "momentum_expectation": momentum,
    }
```

`GraphState` 新增 `action_plan: dict | None`；`strategy_node` 計算後寫入；`AnalyzeResponse` 新增 `action_plan: dict | None = None`。

#### T3-3：`data_sources` 動態填入

**檔案**：`api.py`

```python
data_sources: list[str] = Field(default_factory=list)
```

response 建構時依 state 結果判斷：
```python
_sources = []
if result.get("raw_news_items"):
    _sources.append("google-news-rss")
if result.get("snapshot"):
    _sources.append("yfinance")
inst = result.get("institutional_flow")
if inst and not inst.get("error"):
    provider = inst.get("provider", "twse-openapi")
    _sources.append(provider)
data_sources=_sources,
```

> 需在 `InstitutionalFlowData` 或 state 中保留 `provider` 欄位以識別來源；若尚未有此欄位，先以 `"institutional-api"` 占位。

#### T3-4：`AnalysisDetail` 新增欄位

**檔案**：`backend/src/ai_stock_sentinel/models.py`

```python
@dataclass
class AnalysisDetail:
    summary: str
    risks: list[str] = field(default_factory=list)
    technical_signal: Literal["bullish", "bearish", "sideways"] = "sideways"
    institutional_flow: str | None = None   # 新增
    sentiment_label: str | None = None      # 新增
```

#### T3-5：LLM System Prompt 同步更新

**檔案**：`backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`

System Prompt 的 JSON schema 範例補上兩個欄位，並說明：
- `institutional_flow`：從已提供的 `institutional_flow` 資料中讀取 `flow_label`，直接填入，**不得修改**
- `sentiment_label`：從已提供的 `cleaned_news` 資料中讀取 `sentiment_label`，直接填入，**不得修改**

`_parse_analysis()` 讀取這兩個欄位時保持 None-safe（欄位缺失不崩潰）。

### DoD（完成定義）

- `AnalyzeResponse` 包含 `sentiment_label`、`action_plan`（dict）、`data_sources`（list）
- `action_plan` 由 rule-based Python 計算；`strategy_type = "defensive_wait"` 時 `action = "觀望"`
- `data_sources` 依實際抓取成功的來源動態填入，最少包含 `"yfinance"`（只要 snapshot 存在）
- `AnalysisDetail` 包含 `institutional_flow` 與 `sentiment_label`
- `_parse_analysis` None-safe：LLM 未回傳新欄位時 fallback 為 `None`，不崩潰
- `make test` 全數通過

### 預計測試案例

- `test_api_response_includes_sentiment_label_from_cleaned_news`
- `test_api_response_sentiment_label_is_none_when_cleaned_news_absent`
- `test_generate_action_plan_returns_defensive_wait_action`
- `test_generate_action_plan_returns_momentum_based_on_flow_label`
- `test_api_response_includes_action_plan_dict`
- `test_api_response_data_sources_includes_yfinance_when_snapshot_present`
- `test_analysis_detail_has_institutional_flow_and_sentiment_label_fields`
- `test_parse_analysis_handles_missing_new_fields_gracefully`

### 執行 Prompt（Session 3）

```
請幫我實作 Session 3 的 AnalyzeResponse 欄位完整性 + AnalysisDetail 結構強化：

參考文件：docs/plans/2026-03-07-spec-gap-fix-day2.md 的 Session 3 詳細任務（T3-1 ~ T3-5）

執行順序：
1. T3-1：api.py AnalyzeResponse 新增頂層 sentiment_label（從 cleaned_news 浮出）
2. T3-2：strategy_generator.py 新增 generate_action_plan()；GraphState 新增 action_plan 欄位；strategy_node 計算並寫入；AnalyzeResponse 新增 action_plan 欄位
3. T3-3：AnalyzeResponse 新增 data_sources，依 state 結果動態填入
4. T3-4：models.py AnalysisDetail 新增 institutional_flow 與 sentiment_label 欄位（欄位有預設 None，向後相容）
5. T3-5：langchain_analyzer.py System Prompt JSON schema 補上新欄位；_parse_analysis() 保持 None-safe

每步完成後補對應單元測試，最後執行 make test 確認全套通過。

⚠️ generate_action_plan() 為純 rule-based Python，不呼叫 LLM。
⚠️ AnalysisDetail 新欄位需保持向後相容（有 default=None），不破壞既有 _parse_analysis fallback 邏輯。
```

---

## Session 4：前端 data_confidence 提示

> **複雜度**：低（純前端，限一個元件）
> **獨立性**：與 Session 3、5、6、7 無相依，可任意穿插執行
> **對應缺口**：Phase 4 前端待辦

### 範圍

- 前端信心指數卡片：`data_confidence < 60` 時卡片下方顯示「資料不足，分數僅供參考」灰色提示
- `data_confidence` 已由後端 `AnalyzeResponse` 回傳，僅需前端讀取並判斷

### 詳細任務

#### T4-1：前端信心指數卡片加入資料不足提示

**檔案**：`frontend/src/App.tsx`

在信心指數圓弧下方（`cross_validation_note` 之後），新增：

```tsx
{result.data_confidence !== null && result.data_confidence !== undefined
  && result.data_confidence < 60 && (
  <p className="text-xs text-gray-400 mt-1">
    ⚠️ 資料不足（完整度 {result.data_confidence}%），分數僅供參考
  </p>
)}
```

`data_confidence` 為 null / undefined 時不顯示，不崩潰。

### DoD（完成定義）

- `data_confidence < 60` 時信心指數卡片正確顯示提示文字（含百分比）
- `data_confidence >= 60` 或 null 時不顯示提示
- 既有視覺佈局不破壞

### 執行 Prompt（Session 4）

```
請幫我實作 Session 4 的前端 data_confidence 提示：

參考文件：docs/plans/2026-03-07-spec-gap-fix-day2.md 的 Session 4 詳細任務（T4-1）

工作：
- 在 frontend/src/App.tsx 信心指數卡片中，cross_validation_note 之後加入 data_confidence 提示
- data_confidence < 60 時顯示「⚠️ 資料不足（完整度 {data_confidence}%），分數僅供參考」灰色小字
- data_confidence 為 null / undefined 或 >= 60 時不顯示
- 不破壞既有信心指數圓弧、cross_validation_note、卡片佈局

完成後，在瀏覽器手動驗收。
```

---

## Session 5：LLM Prompt 補齊消息面輸入

> **複雜度**：低（修改一個函式簽名 + Prompt + analyze_node）
> **獨立性**：與 Session 3、4、6、7 無相依，可任意穿插執行
> **問題根源**：`langchain_analyzer.py` 的 `_HUMAN_PROMPT` 未傳入 `cleaned_news`，LLM 看不到任何消息面資料，架構規格要求的三維輸入（消息面 + 技術面 + 籌碼面）實際上只有兩維。
> **重要限制**：消息面來源為 **Google News RSS**，內容是市場事件標題與短摘要，**不保證含有財報數字**（EPS / 營收等）。LLM 應識別的是事件語義與情緒傾向，而非期待提取結構化財務數值。

### 範圍

- `langchain_analyzer.py` `_HUMAN_PROMPT` 新增 `{news_summary}` 段落
- `analyze()` 新增 `news_summary: str | None = None` 參數
- `graph/nodes.py` `analyze_node` 從 `state["cleaned_news"]` 組合後傳入
- `analysis/interface.py` `StockAnalyzer` Protocol 同步更新

### 詳細任務

#### T5-1：`_HUMAN_PROMPT` 新增消息面段落

**檔案**：`backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`

在 `【技術面敘事】` 段落之前插入：

```
【消息面摘要】
{news_summary}
```

`news_summary` 為 None 或空字串時，填入 `"（本次無新聞摘要）"`，確保 Prompt 結構不破壞。

#### T5-2：`analyze()` 新增參數

```python
def analyze(
    self,
    snapshot: StockSnapshot,
    *,
    news_summary: str | None = None,      # 新增
    technical_context: str | None = None,
    institutional_context: str | None = None,
    confidence_score: int | None = None,
    cross_validation_note: str | None = None,
) -> AnalysisDetail:
```

`_estimate_cost()` 同步納入 `news_summary` 長度估算。

#### T5-3：`analyze_node` 組合 `news_summary` 並傳入

**檔案**：`backend/src/ai_stock_sentinel/graph/nodes.py`

```python
cleaned = state.get("cleaned_news")
news_summary: str | None = None
if cleaned:
    parts = []
    if cleaned.get("title"):
        parts.append(f"標題：{cleaned['title']}")
    # mentioned_numbers 為財經語意過濾後的數值（若 RSS 新聞恰好含有），不強制存在
    nums = cleaned.get("mentioned_numbers") or []
    if nums:
        parts.append(f"新聞數值線索：{', '.join(str(n) for n in nums)}")
    sentiment = cleaned.get("sentiment_label")
    if sentiment:
        parts.append(f"情緒判斷：{sentiment}")
    news_summary = "\n".join(parts) if parts else None

result = analyzer.analyze(
    snapshot,
    news_summary=news_summary,
    technical_context=...,
    ...
)
```

#### T5-4：`StockAnalyzer` Protocol 同步更新

**檔案**：`backend/src/ai_stock_sentinel/analysis/interface.py`

`analyze()` 方法簽名新增 `news_summary: str | None = None`。

### DoD（完成定義）

- LLM 收到的 Prompt 包含 `【消息面摘要】` 段落（含標題 / 關鍵數字 / 情緒標籤）
- `cleaned_news` 為 None 時 Prompt 顯示 `（本次無新聞摘要）`，不崩潰
- `_estimate_cost()` 納入 `news_summary` 長度，不低估 token 數
- `make test` 全數通過

### 預計測試案例

- `test_analyze_node_passes_news_summary_to_analyzer`
- `test_news_summary_fallback_when_cleaned_news_is_none`
- `test_estimate_cost_includes_news_summary_length`
- `test_human_prompt_contains_news_summary_section`

### 執行 Prompt（Session 5）

```
請幫我實作 Session 5 的 LLM Prompt 消息面輸入補齊：

參考文件：docs/plans/2026-03-07-spec-gap-fix-day2.md 的 Session 5 詳細任務（T5-1 ~ T5-4）

執行順序：
1. T5-1：langchain_analyzer.py _HUMAN_PROMPT 新增【消息面摘要】段落（{news_summary}，None 時顯示預設文字）
2. T5-2：analyze() 新增 news_summary 參數；_estimate_cost() 納入長度估算
3. T5-3：analyze_node 從 state["cleaned_news"] 組合 news_summary 後傳入 analyzer
4. T5-4：interface.py StockAnalyzer Protocol 同步更新簽名

每步完成後補對應單元測試，最後執行 make test 確認全套通過。

⚠️ news_summary 為 None 時 Prompt 不得出現空白段落或崩潰。
⚠️ 不得修改 Prompt 中 confidence_score 的相關文字（LLM 不得修改此值）。
```

---

## Session 6：data_confidence 語義修正

> **複雜度**：低（修改一個函式 + 補測試）
> **獨立性**：與 Session 3、4、5、7 無相依，可任意穿插執行
> **問題根源**：`compute_confidence()` 把 `neutral` 情緒與 `sideways` 技術訊號計為「資料缺失」，但兩者是合法的輸出值。目前 `data_confidence` 量的是「訊號偏向廣度」（幾個維度有偏多/偏空），不是「資料取得完整度」（幾個維度成功取得資料）。

### 範圍

- `confidence_scorer.py` `compute_confidence()` 修正資料完整度判斷邏輯
- `GraphState` / `AnalyzeResponse` 不需改動（欄位名稱維持 `data_confidence`）

### 詳細任務

#### T6-1：修正 `compute_confidence()` 的資料完整度計算

**檔案**：`backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`

**現行邏輯（錯誤）**：
```python
available = sum([
    news_sentiment not in ("neutral",),      # neutral 被計為無資料
    inst_flow not in ("neutral", "unknown"),  # neutral 被計為無資料
    technical_signal not in ("sideways", "unknown"),  # sideways 被計為無資料
])
```

**修正後邏輯**：
```python
# 資料完整度：只判斷「資料是否成功取得」
# - 新聞：有任何 sentiment_label（含 neutral）視為有資料
# - 籌碼：flow_label 非 "unknown"（含 neutral）視為有資料
# - 技術：technical_signal 非 "unknown"（含 sideways）視為有資料
#
# 注意：架構規格（v2.4）明確定義 technical_signal 不輸出 "unknown"
#（資料不足時降級為 "sideways"），因此 technical_signal != "unknown"
# 在現行架構永遠為 True，但保留此判斷作為防禦性設計，
# 避免未來若新增 unknown 值時靜默影響 data_confidence 計算。
data_available = sum([
    news_sentiment != "unknown",           # neutral 也算有取得
    inst_flow != "unknown",               # neutral 也算有取得
    technical_signal != "unknown",        # sideways 也算有取得；現行架構不會出現 unknown
])
data_confidence = round(data_available / 3 * 100)
```

> **補充**：現行 `news_sentiment` 不會出現 `"unknown"`（Cleaner 只輸出 positive/negative/neutral），但加入此判斷可讓邏輯語義一致，未來若新增 `"unknown"` 值也不需再改。

#### T6-2：更新函式 docstring

說明 `data_confidence` 的正確語義：資料成功取得的維度比例（0 / 33 / 67 / 100），而非訊號偏向廣度。

### DoD（完成定義）

- `news_sentiment="neutral"` 時 `data_confidence` = 33（一個維度）而非 0
- `inst_flow="neutral"`、`technical_signal="sideways"` 同理不降低 `data_confidence`
- 三個維度均有有效值時 `data_confidence` = 100（不論各維度是否中性）
- 只有 `inst_flow="unknown"` 時才降低 `data_confidence`
- `make test` 全數通過

### 預計測試案例

- `test_data_confidence_is_100_when_all_dims_valid_including_neutral`
- `test_data_confidence_is_67_when_inst_flow_unknown`
- `test_data_confidence_is_33_when_only_news_available`
- `test_data_confidence_neutral_sentiment_does_not_reduce_score`
- `test_data_confidence_sideways_signal_does_not_reduce_score`

### 執行 Prompt（Session 6）

```
請幫我實作 Session 6 的 data_confidence 語義修正：

參考文件：docs/plans/2026-03-07-spec-gap-fix-day2.md 的 Session 6 詳細任務（T6-1 ~ T6-2）

工作：
1. T6-1：confidence_scorer.py compute_confidence() 修正資料完整度計算邏輯
   - neutral 情緒 / neutral 籌碼 / sideways 技術 均視為「有取得資料」
   - 只有 unknown 才視為「無資料」
2. T6-2：更新 docstring 說明 data_confidence 正確語義

補齊測試（至少5個），最後執行 make test 確認全套通過。

⚠️ 不得修改 signal_confidence / adjust_confidence_by_divergence 邏輯。
⚠️ 現有通過的 data_confidence 測試若有語義錯誤需一併修正。
```

---

## Session 7：DATE_UNKNOWN 信心分數懲罰

> **複雜度**：低（純新增，修改一個函式 + 補測試）
> **獨立性**：與 Session 3、4、5、6 無相依，可任意穿插執行
> **對應規格**：`docs/specs/ai-stock-sentinel-architecture-spec.md` v2.4，新聞摘要品質門檻段落

### 背景

架構規格 v2.4 新增：日期未知的新聞（`DATE_UNKNOWN` 旗標）時效性無法驗證，應在 `score_node` 的 rule-based Python 對 `signal_confidence` 額外扣 -3，並在 `cross_validation_note` 追加固定提示。

### 範圍

- `analysis/confidence_scorer.py`：`compute_confidence()` 新增 `date_unknown` 參數，在各維度加總後、clamp 前套用 -3
- `graph/nodes.py`：`score_node` 從 `state["cleaned_news_quality"]` 讀取 `quality_flags`，判斷是否含 `DATE_UNKNOWN`，傳入 `compute_confidence()`
- `cross_validation_note` 追加邏輯：由 `score_node` 在 rule-based 字串後附加固定字串

### 詳細任務

#### T7-1：`compute_confidence()` 新增 `date_unknown` 參數

**檔案**：`backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`

```python
def compute_confidence(
    base_score: int,
    news_sentiment: str,
    inst_flow: str,
    technical_signal: str,
    date_unknown: bool = False,   # 新增
) -> dict:
    ...
    # 各維度加總後、clamp 前
    if date_unknown:
        score -= 3
    score = max(0, min(100, score))
    ...
```

#### T7-2：`score_node` 讀取 `quality_flags` 並傳入

**檔案**：`backend/src/ai_stock_sentinel/graph/nodes.py`，`score_node`

```python
quality = state.get("cleaned_news_quality") or {}
flags = quality.get("quality_flags") or []
date_unknown = "DATE_UNKNOWN" in flags

result = compute_confidence(
    base_score=50,
    news_sentiment=...,
    inst_flow=...,
    technical_signal=...,
    date_unknown=date_unknown,
)
```

若 `date_unknown` 為 True，在 `cross_validation_note` **末尾追加**（不覆蓋原有內容）：

```python
if date_unknown:
    note = note + "（注意：新聞日期不明，時效性未驗證）"
```

> ⚠️ **必須使用追加（`+`）而非賦值（`=`）**，確保使用者能同時看到 `adjust_confidence_by_divergence` 產生的「三維訊號共振 / 利多出貨背離」等分析結論，以及本警告提示。覆蓋原文會造成關鍵分析資訊的靜默遺失。

#### T7-3：`cleaned_news_quality` 需在 `GraphState` 可讀

確認 `GraphState` 有 `cleaned_news_quality` 欄位（`quality_gate_node` 應已寫入）；若無，補至 `state.py`。

### DoD（完成定義）

- `DATE_UNKNOWN` 存在時 `signal_confidence` 比無旗標低 3 分
- `DATE_UNKNOWN` 不影響 `data_confidence`
- `cross_validation_note` 末尾出現提示字串
- `cleaned_news_quality` 為 None 或 `quality_flags` 為空時安全降級，不崩潰
- `make test` 全數通過

### 預計測試案例

- `test_date_unknown_flag_reduces_signal_confidence_by_3`
- `test_date_unknown_does_not_affect_data_confidence`
- `test_date_unknown_appends_note_to_cross_validation_note`
- `test_no_penalty_when_quality_flags_empty`
- `test_no_penalty_when_cleaned_news_quality_is_none`

### 執行 Prompt（Session 7）

```
請幫我實作 Session 7 的 DATE_UNKNOWN 信心分數懲罰：

參考文件：docs/plans/2026-03-07-spec-gap-fix-day2.md 的 Session 7 詳細任務（T7-1 ~ T7-3）
架構規格：docs/specs/ai-stock-sentinel-architecture-spec.md v2.4，新聞摘要品質門檻段落

執行順序：
1. T7-1：confidence_scorer.py compute_confidence() 新增 date_unknown 參數，在 clamp 前扣 3 分
2. T7-2：score_node 從 state["cleaned_news_quality"]["quality_flags"] 讀取 DATE_UNKNOWN，傳入 compute_confidence()；date_unknown=True 時在 cross_validation_note 末尾追加固定字串
3. T7-3：確認 GraphState 有 cleaned_news_quality 欄位，不足則補

補齊測試（至少 5 個），最後執行 make test 確認全套通過。

⚠️ 此懲罰針對 signal_confidence，不影響 data_confidence。
⚠️ cleaned_news_quality 為 None 時安全降級，不崩潰。
```

---

## 驗收標準（Day 2）

- `make test` 全數通過（含所有新增測試）
- `/analyze` 回傳：`sentiment_label`、`action_plan`（dict）、`data_sources`（list）
- 前端：`data_confidence < 60` 時信心指數卡片顯示資料不足提示
- LLM Prompt 包含 `【消息面摘要】` 段落；`cleaned_news` 為 None 時顯示預設占位文字，不崩潰
- `data_confidence` 正確反映「資料取得完整度」：`neutral` 情緒 / `sideways` 技術訊號不降低分數，僅 `unknown` 才計為未取得
- `DATE_UNKNOWN` 旗標存在時 `signal_confidence` 自動 -3，`cross_validation_note` 末尾追加時效性未驗證提示；`data_confidence` 不受影響

---

## 最終步驟：Spec Review（Day 2 結束後）

所有 Session 完成後，對照 `docs/specs/ai-stock-sentinel-architecture-spec.md` 與 `docs/progress-tracker.md`，確認：

1. 本計劃修補的七大缺口均已正確實作，與架構規格描述一致
2. 未引入新的規格缺口（特別是跨 Session 的欄位變動）
3. 若發現新缺口，補記至 `progress-tracker.md` 的「待優化缺口」區塊，並決定是否需新建計劃文件

---

## Handoff Snapshot 模板（每 Session 結束填寫）

```markdown
## Handoff Snapshot — 2026-03-07 Session N 結束

- 已完成（本 Session）：
  -

- 進行中：
  - 無

- 阻塞點：
  -

- 下一步（優先序）：
  1.

- 驗收證據：
  - make test → N passed
```
