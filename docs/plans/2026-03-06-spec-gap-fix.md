# 計劃：規格缺口修補（Spec Gap Fix）

> 日期：2026-03-06
> 狀態：待執行
> 目的：修補 2026-03-05 規格對比發現的四大缺口、一個已排入待辦的前端提示，以及 2026-03-05 文件比對追加的兩個邏輯缺口
> 追蹤文件：`docs/progress-tracker.md` → 「待優化缺口（2026-03-05 規格對比發現）」、「下一輪修正（LLM Prompt 缺少消息面輸入）」、「下一輪修正（data_confidence 語義修正）」
> 原則：完成即補測試（Code Complete ≠ Task Complete；需附對應測試與驗收證據）

---

## 背景說明（給無脈絡的工程師）

本次修補源自對架構規格文件（`ai-stock-sentinel-architecture-spec.md`）與後端程式碼的全面比對，發現下列差距：

1. **技術位階指標缺失**：架構規格要求 `high_20d / low_20d / support_20d / resistance_20d`，但 `StockSnapshot` / `context_generator` / `strategy_generator` 均未實作。目前 `entry_zone` / `stop_loss` 僅輸出描述性文字，缺乏實際價格。
2. **Action Plan 燈號未實作**：架構規格要求後端計算 `action_plan_tag`（機會 / 過熱 / 中性），前端僅做 enum → 顯示映射。
3. **AnalyzeResponse 缺多個頂層欄位**：`sentiment_label`、`action_plan`（完整字典）、`data_sources`、`institutional_flow_label` 均未出現在 API 回應頂層。
4. **AnalysisDetail 結構薄弱**：目前只有 `summary / risks / technical_signal`，缺少 `institutional_flow` 與 `sentiment_label`。
5. **前端 data_confidence 提示**：後端已回傳 `data_confidence`，但前端尚未在信心指數卡片顯示「資料不足」提示。
6. **LLM Prompt 缺少消息面輸入**：`_HUMAN_PROMPT` 沒有傳入 `cleaned_news`，LLM 看不到消息面資料，Skeptic Mode 步驟一「提取新聞數值」形同空轉。
7. **`data_confidence` 語義偏差**：`neutral` 情緒被計為資料缺失，`sideways` 技術訊號也被計為資料不足；實際量的是訊號偏向廣度，非資料取得完整度。

**執行順序依據**：先穩定後端資料計算（Session 1）→ 再補燈號邏輯（Session 2）→ 再收尾 API 結構（Session 3）→ 前端提示（Session 4）→ LLM 三維輸入（Session 5）→ data_confidence 語義（Session 6）。

---

## Session 1：技術位階指標（Support / Resistance）

> **複雜度**：高（跨 `yfinance_client` → `context_generator` → `strategy_generator` → `GraphState`）  
> **對應計劃**：`docs/plans/2026-03-05-deep-analysis-upgrade.md` Session 3

### 範圍

**後端**：

- `yfinance_client.py` `StockSnapshot` 新增四個計算欄位：`high_20d`、`low_20d`、`support_20d`、`resistance_20d`
- `context_generator.py` `generate_technical_context()` 新增支撐/壓力位敘事段落
- `strategy_generator.py` `entry_zone` / `stop_loss` 改以實際價格計算（非描述性文字）
- `graph/state.py` `GraphState` 新增四個選填欄位
- **Fallback 行為**：`low_20d` / `ma60` 不可用時，`entry_zone` 回傳 `"資料不足，建議參考現價 +/- 5%"`，`cross_validation_note` 或 `risks` 標注「20日位階資料不足」；禁止虛構數值

### 詳細任務

#### T1-1：`StockSnapshot` 補齊位階欄位

**檔案**：`backend/src/ai_stock_sentinel/data_sources/yfinance_client.py`

```python
@dataclass
class StockSnapshot:
    # ... 既有欄位 ...
    high_20d: float | None = None       # 近 20 日最高價
    low_20d: float | None = None        # 近 20 日最低價
    support_20d: float | None = None    # 近 20 日支撐位（近 20 日最低收盤 × 0.99）
    resistance_20d: float | None = None # 近 20 日壓力位（近 20 日最高收盤 × 1.01）
```

計算方式（使用現有 `recent_closes`）：
- `high_20d`：`max(recent_closes[-20:])`
- `low_20d`：`min(recent_closes[-20:])`
- `support_20d`：`low_20d * 0.99`（保守緩衝 1%）
- `resistance_20d`：`high_20d * 1.01`（保守緩衝 1%）
- 若 `recent_closes` 少於 20 筆，使用全部資料；少於 2 筆則保留 `None`

#### T1-2：`GraphState` 補欄位

**檔案**：`backend/src/ai_stock_sentinel/graph/state.py`

新增：
```python
high_20d: float | None
low_20d: float | None
support_20d: float | None
resistance_20d: float | None
```

#### T1-3：`preprocess_node` 將位階欄位寫入 state

**檔案**：`backend/src/ai_stock_sentinel/graph/nodes.py`，`preprocess_node` 函式

從 `state["snapshot"]` 取出 `high_20d`、`low_20d`、`support_20d`、`resistance_20d`，寫入 `return dict`。

#### T1-4：`generate_technical_context` 加入支撐壓力位敘事

**檔案**：`backend/src/ai_stock_sentinel/analysis/context_generator.py`

新增 `_price_level_narrative(close, support, resistance, high_20d, low_20d)` 函式：

```python
def _price_level_narrative(
    close: float | None,
    support: float | None,
    resistance: float | None,
    high_20d: float | None,
    low_20d: float | None,
) -> str:
    if None in (close, support, resistance):
        return "近20日支撐壓力位資料不足，無法判斷位階。"
    lines = [
        f"近20日高點：{high_20d:.1f}，低點：{low_20d:.1f}",
        f"支撐參考位：{support:.1f}（近20日低點 -1%）",
        f"壓力參考位：{resistance:.1f}（近20日高點 +1%）",
    ]
    if close <= support * 1.02:
        lines.append("現價接近支撐位，下檔空間有限，可留意反彈機會。")
    elif close >= resistance * 0.98:
        lines.append("現價接近壓力位，上漲動能需確認突破，注意回測風險。")
    else:
        lines.append("現價處於支撐與壓力之間，位階中立。")
    return "\n".join(lines)
```

`generate_technical_context` 的回傳 `technical_context` 字串末尾附加此段落。

#### T1-5：`strategy_generator.py` 改用實際價格

**檔案**：`backend/src/ai_stock_sentinel/analysis/strategy_generator.py`

修改 `generate_strategy` 簽名，新增 `close`、`support_20d`、`resistance_20d`、`low_20d`、`ma60` 欄位讀取：

- `entry_zone`：
  - 若 `support_20d` 與 `ma20` 均可用：`f"{support_20d:.1f}–{ma20:.1f}（support_20d ~ MA20）"`
  - 若 BIAS > 5% 且資料可用：`f"拉回 MA20（{ma20:.1f}）附近分批佈局"`
  - Fallback：`"資料不足，建議參考現價 +/- 5%"`

- `stop_loss`：
  - 若 `low_20d` 與 `ma60` 均可用：`f"{low_20d * 0.97:.1f}（近20日低點×0.97）或跌破 MA60（{ma60:.1f}），取較寬者"`
  - 若只有 `low_20d`：`f"{low_20d * 0.97:.1f}（近20日低點×0.97）"`
  - Fallback：`"近20日低點 - 3%（位階資料不足，以描述性規則代替）"`

#### T1-6：`api.py` `initial_state` 補欄位

`initial_state` 補上四個 `None`：`high_20d`, `low_20d`, `support_20d`, `resistance_20d`。

### DoD（完成定義）

- `StockSnapshot` 包含四個位階欄位，測試確認計算結果
- `strategy_node` 輸出的 `entry_zone` 包含具體數值（含 `support_20d` 或現價）
- `strategy_node` 輸出的 `stop_loss` 包含具體數值（含 `low_20d * 0.97` 或 MA60）
- `recent_closes` 資料不足（< 2 筆）時，安全降級，不崩潰，`entry_zone` 輸出 fallback 字串
- 新增測試全數通過；既有策略測試不破壞（回歸）

### 預計測試案例

- `test_stock_snapshot_computes_high_low_support_resistance_from_closes`
- `test_stock_snapshot_price_levels_none_when_insufficient_data`
- `test_generate_technical_context_includes_price_level_narrative`
- `test_strategy_entry_zone_contains_numeric_price_from_support_20d`
- `test_strategy_stop_loss_contains_numeric_price_from_low_20d`
- `test_strategy_fallback_uses_descriptive_text_when_price_levels_missing`
- `test_strategy_fallback_when_low20d_unavailable_uses_close_plus_minus_5pct`

### 執行 Prompt（Session 1）

```
請幫我實作 Session 1 的技術位階指標（Support / Resistance）：

參考文件：docs/plans/2026-03-06-spec-gap-fix.md 的 Session 1 詳細任務（T1-1 ~ T1-6）

執行順序：
1. T1-1：yfinance_client.py StockSnapshot 補齊 high_20d / low_20d / support_20d / resistance_20d 計算
2. T1-2：graph/state.py GraphState 新增四個選填欄位
3. T1-3：nodes.py preprocess_node 將位階欄位從 snapshot 寫入 state
4. T1-4：context_generator.py 新增 _price_level_narrative()，整合進 generate_technical_context
5. T1-5：strategy_generator.py entry_zone / stop_loss 改以實際價格計算，補 fallback 行為
6. T1-6：api.py initial_state 補欄位

每步完成後補對應單元測試，最後執行 make test 確認全套通過。

⚠️ 禁止 LLM 自行估算價格；所有數值必須由 Python 計算後傳入。
⚠️ 資料不足時必須安全降級，不得拋例外中斷流程。
```

---

## Session 2：Action Plan 燈號

> **複雜度**：中（純新增，不修改既有邏輯）  
> **對應計劃**：`docs/plans/2026-03-05-deep-analysis-upgrade.md` Session 4

### 範圍

**後端**：
- 新增 `calculate_action_plan_tag(rsi14, flow_label, confidence_score)` 純 Python 函式
- `graph/state.py` 新增 `action_plan_tag` 欄位
- `graph/nodes.py` `strategy_node` 或新增 `tag_node` 計算並寫入
- `api.py` `AnalyzeResponse` 新增 `action_plan_tag: str | null`
- 同步補齊頂層 `institutional_flow_label: str | null`（從 `institutional_flow.flow_label` 浮出，`action_plan_tag` 計算也依賴此值）

**前端**：
- Action Plan 卡片標題旁顯示燈號標籤
- `action_plan_tag` 為 null 時不顯示，不崩潰

### 詳細任務

#### T2-1：新增 `calculate_action_plan_tag`

**檔案**：`backend/src/ai_stock_sentinel/analysis/strategy_generator.py`（或新建 `action_plan_tagger.py`）

```python
def calculate_action_plan_tag(
    rsi14: float | None,
    flow_label: str | None,
    confidence_score: int | None,
) -> str:
    """
    純 rule-based，依固定優先序判斷行動建議燈號。
    任一輸入為 None → 降級為 "neutral"。

    opportunity：rsi14 < 30 AND flow_label = "institutional_accumulation" AND confidence_score > 70
    overheated ：rsi14 > 70 AND flow_label = "distribution"
    neutral    ：其餘（含部分命中）
    """
    if rsi14 is None or flow_label is None or confidence_score is None:
        return "neutral"
    if rsi14 < 30 and flow_label == "institutional_accumulation" and confidence_score > 70:
        return "opportunity"
    if rsi14 > 70 and flow_label == "distribution":
        return "overheated"
    return "neutral"
```

#### T2-2：`GraphState` / `initial_state` 補欄位

**檔案**：`graph/state.py` 新增 `action_plan_tag: str | None`  
**檔案**：`api.py` `initial_state` 補 `action_plan_tag: None`

#### T2-3：`strategy_node` 計算並寫入 `action_plan_tag`

**檔案**：`backend/src/ai_stock_sentinel/graph/nodes.py`，`strategy_node` 函式末尾

從 snapshot 取 `rsi14`（`context_generator` 已計算過，可從 snapshot `recent_closes` 重新計算或快取）、`institutional_flow.flow_label`、`state["confidence_score"]`，呼叫 `calculate_action_plan_tag`。

> 注意：`rsi14` 計算已在 `preprocess_node` 完成過，可透過新增 `rsi14: float | None` 至 `GraphState`（選填）並在 `preprocess_node` 寫入，讓 `strategy_node` 直接讀取。

#### T2-4：`AnalyzeResponse` 新增欄位

**檔案**：`api.py`

```python
action_plan_tag: str | None = None
institutional_flow_label: str | None = None  # flow_label 浮出
```

response 建構時從 `result` 讀取：
```python
action_plan_tag=result.get("action_plan_tag"),
institutional_flow_label=(
    result.get("institutional_flow", {}).get("flow_label")
    if result.get("institutional_flow") and not result["institutional_flow"].get("error")
    else None
),
```

#### T2-5：前端顯示燈號

**檔案**：`frontend/src/App.tsx`

在 Action Plan 卡片標題旁新增 tag badge：

```tsx
const ACTION_TAG_MAP: Record<string, { emoji: string; label: string; color: string }> = {
  opportunity: { emoji: "🟢", label: "機會", color: "text-green-600" },
  overheated:  { emoji: "🔴", label: "過熱", color: "text-red-600" },
  neutral:     { emoji: "🔵", label: "中性", color: "text-blue-500" },
};

// 卡片標題旁，action_plan_tag 有值才顯示
{result.action_plan_tag && ACTION_TAG_MAP[result.action_plan_tag] && (
  <span className={`text-sm font-medium ${ACTION_TAG_MAP[result.action_plan_tag].color}`}>
    {ACTION_TAG_MAP[result.action_plan_tag].emoji} {ACTION_TAG_MAP[result.action_plan_tag].label}
  </span>
)}
```

### DoD（完成定義）

- `calculate_action_plan_tag` 三情境（opportunity / overheated / neutral）各有單元測試
- None 安全：任一輸入為 null 時回傳 `"neutral"`，不崩潰
- `/analyze` 回傳 `action_plan_tag` 與 `institutional_flow_label` 欄位
- 前端 Action Plan 卡片正確顯示三種燈號；null 時不顯示標籤，不崩潰
- 既有測試全數通過

### 預計測試案例

- `test_calculate_action_plan_tag_returns_opportunity_when_all_conditions_met`
- `test_calculate_action_plan_tag_returns_overheated_when_rsi_high_and_distribution`
- `test_calculate_action_plan_tag_returns_neutral_for_partial_match`
- `test_calculate_action_plan_tag_falls_back_to_neutral_when_any_input_none`
- `test_api_response_includes_action_plan_tag_field`
- `test_api_response_includes_institutional_flow_label_field`

### 執行 Prompt（Session 2）

```
請幫我實作 Session 2 的 Action Plan 燈號：

參考文件：docs/plans/2026-03-06-spec-gap-fix.md 的 Session 2 詳細任務（T2-1 ~ T2-5）

執行順序：
1. T2-1：新增 calculate_action_plan_tag() 純 Python rule-based 函式（三情境 + None 安全降級）
2. T2-2：graph/state.py 新增 action_plan_tag 欄位；api.py initial_state 補 None
3. T2-3：strategy_node 計算 action_plan_tag 並寫入 state（需確認 rsi14 如何從 state 讀取）
4. T2-4：AnalyzeResponse 新增 action_plan_tag 與 institutional_flow_label 兩個頂層欄位
5. T2-5：前端 App.tsx Action Plan 卡片標題旁加入燈號 badge

每步完成後補對應單元測試，最後執行 make test 確認全套通過。

⚠️ 燈號判斷邏輯 100% 在後端，前端僅做 enum → emoji/文字映射，不含 rsi14 < 30 等條件判斷。
⚠️ action_plan_tag 為 null 時前端不顯示，不崩潰。
```

---

## Session 3：AnalyzeResponse 欄位完整性 + AnalysisDetail 結構強化

> **複雜度**：中（多個小改動，需確保不破壞現有測試）  
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

參考文件：docs/plans/2026-03-06-spec-gap-fix.md 的 Session 3 詳細任務（T3-1 ~ T3-5）

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

參考文件：docs/plans/2026-03-06-spec-gap-fix.md 的 Session 4 詳細任務（T4-1）

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

參考文件：docs/plans/2026-03-06-spec-gap-fix.md 的 Session 5 詳細任務（T5-1 ~ T5-4）

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

參考文件：docs/plans/2026-03-06-spec-gap-fix.md 的 Session 6 詳細任務（T6-1 ~ T6-2）

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
> **對應規格**：`docs/ai-stock-sentinel-architecture-spec.md` v2.4，新聞摘要品質門檻段落

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

若 `date_unknown` 為 True，在 `cross_validation_note` 末尾追加：`「（注意：新聞日期不明，時效性未驗證）」`

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

參考文件：docs/plans/2026-03-06-spec-gap-fix.md 的 Session 7 詳細任務（T7-1 ~ T7-3）
架構規格：docs/ai-stock-sentinel-architecture-spec.md v2.4，新聞摘要品質門檻段落

執行順序：
1. T7-1：confidence_scorer.py compute_confidence() 新增 date_unknown 參數，在 clamp 前扣 3 分
2. T7-2：score_node 從 state["cleaned_news_quality"]["quality_flags"] 讀取 DATE_UNKNOWN，傳入 compute_confidence()；date_unknown=True 時在 cross_validation_note 末尾追加固定字串
3. T7-3：確認 GraphState 有 cleaned_news_quality 欄位，不足則補

補齊測試（至少 5 個），最後執行 make test 確認全套通過。

⚠️ 此懲罰針對 signal_confidence，不影響 data_confidence。
⚠️ cleaned_news_quality 為 None 時安全降級，不崩潰。
```

---

## 執行節奏建議

```
Session 1（高複雜）→ Session 2（中）→ Session 3（中）→ Session 4（低）→ Session 5（低）→ Session 6（低）→ Session 7（低）
```

- Session 1 完成才啟動 Session 2（Session 2 依賴 `rsi14` 是否從 state 可讀）
- Session 2 完成才啟動 Session 3（Session 3 補充 `institutional_flow_label` 時已由 Session 2 處理）
- Session 4 獨立，可提前至任意 Session 後執行
- Session 5、Session 6、Session 7 互相獨立，可並行或穿插執行

每個 Session 結束前必做：
1. `make test` 全數通過
2. 回寫 Handoff Snapshot（已完成、阻塞點、下一 Session 前提）

---

## 驗收標準（整體）

- `make test` 全數通過（含所有新增測試）
- `/analyze` 回傳：`action_plan_tag`、`institutional_flow_label`、`sentiment_label`、`action_plan`、`data_sources`
- 策略輸出：`entry_zone` 包含具體價格數值（非純描述性文字）
- 前端：Action Plan 卡片顯示燈號；`data_confidence < 60` 時信心指數卡片顯示資料不足提示
- LLM Prompt 包含 `【消息面摘要】` 段落；`cleaned_news` 為 None 時顯示預設占位文字，不崩潰
- `data_confidence` 正確反映「資料取得完整度」：`neutral` 情緒 / `sideways` 技術訊號不降低分數，僅 `unknown` 才計為未取得
- `DATE_UNKNOWN` 旗標存在時 `signal_confidence` 自動 -3，`cross_validation_note` 末尾追加時效性未驗證提示；`data_confidence` 不受影響

---

## 最終步驟：Spec Review

所有 Session 完成後，對照 `docs/ai-stock-sentinel-architecture-spec.md` 與 `docs/progress-tracker.md`，確認：

1. 本計劃修補的六大缺口均已正確實作，與架構規格描述一致
2. 未引入新的規格缺口（特別是跨 Session 的欄位變動）
3. 若發現新缺口，補記至 `progress-tracker.md` 的「待優化缺口」區塊，並決定是否需新建計劃文件

---

## Handoff Snapshot 模板（每 Session 結束填寫）

```markdown
## Handoff Snapshot — 2026-03-06 Session N 結束

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
