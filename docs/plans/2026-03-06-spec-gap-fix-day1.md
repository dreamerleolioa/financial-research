# 計劃：規格缺口修補 Day 1（Spec Gap Fix - Day 1）

> 日期：2026-03-06
> 狀態：已完成（2026-03-06）
> 目的：修補技術位階指標缺失（Session 1）與 Action Plan 燈號缺失（Session 2）
> 追蹤文件：`docs/progress-tracker.md` → 「待優化缺口（2026-03-05 規格對比發現）」
> 原則：完成即補測試（Code Complete ≠ Task Complete；需附對應測試與驗收證據）
> 接續文件：`docs/plans/2026-03-07-spec-gap-fix-day2.md`

---

## 背景說明（給無脈絡的工程師）

本次修補源自對架構規格文件（`ai-stock-sentinel-architecture-spec.md`）與後端程式碼的全面比對，發現下列差距：

1. **技術位階指標缺失**：架構規格要求 `high_20d / low_20d / support_20d / resistance_20d`，但 `StockSnapshot` / `context_generator` / `strategy_generator` 均未實作。目前 `entry_zone` / `stop_loss` 僅輸出描述性文字，缺乏實際價格。
2. **Action Plan 燈號未實作**：架構規格要求後端計算 `action_plan_tag`（機會 / 過熱 / 中性），前端僅做 enum → 顯示映射。

**Day 1 執行順序**：先穩定後端資料計算（Session 1）→ 再補燈號邏輯（Session 2）

Session 2 依賴 Session 1 完成後 `GraphState` 中 `rsi14` 欄位的可讀性，因此必須按序執行。

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

**檔案**：`backend/src/ai_stock_sentinel/models.py`（⚠️ 實際實作位置與原計劃不同；`StockSnapshot` 定義在 `models.py`，欄位與計算邏輯一併放此，`yfinance_client.py` 僅負責抓取原始 `recent_closes`）

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

> ⚠️ **MA60 資料量前提**：`stop_loss` 計算需要 `ma60`，而 `ma60` 需要至少 60 筆收盤資料。
> 必須確認 `yfinance_client.py` 的 `fetch_basic_snapshot()` 所用 `period` 足以涵蓋 60 個交易日（建議 `period="6mo"` 或 `period="3mo"` 確認約有 60+ 筆）。
> 若 `recent_closes` 不足 60 筆，`ma60` 將為 `None`，T1-5 的 `stop_loss` 會自動走 `only low_20d` 或 fallback 分支——這是預期行為，但需在實作前確認 period 設定，避免不必要的 fallback。

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

參考文件：docs/plans/2026-03-06-spec-gap-fix-day1.md 的 Session 1 詳細任務（T1-1 ~ T1-6）

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
> **前提**：Session 1 完成後才啟動（`GraphState` 已具備完整欄位基礎）

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

#### T2-0：`GraphState` 新增 `rsi14` 欄位 + `preprocess_node` 寫入（T2-3 前提）

> ⚠️ **此任務是 T2-3 的強依賴前提**，必須先完成。

**問題**：`rsi14` 目前僅以文字形式存在於 `technical_context` 敘事字串內（由 `_rsi_narrative()` 嵌入），並無獨立浮點數欄位。`strategy_node` 若直接從字串反解，屬脆弱的 implementation detail。

**解法**：在 `preprocess_node` 計算完 `rsi14` 後，同時將數值寫入 `GraphState`，使 `strategy_node` 可透過 `state["rsi14"]` 直接讀取，進行 `rsi14 < 30` / `rsi14 > 70` 的硬邏輯判斷。

**檔案**：`backend/src/ai_stock_sentinel/graph/state.py`

```python
rsi14: float | None
```

**檔案**：`backend/src/ai_stock_sentinel/graph/nodes.py`，`preprocess_node` 函式

在既有敘事生成後，額外寫入：

```python
# rsi14 數值獨立寫入 state，供 strategy_node 燈號判斷使用
rsi14_val: float | None = None
if len(closes_list) >= 15:  # RSI14 需要 15 個資料點（14 期差值）
    rsi14_val = calc_rsi(closes_list, period=14)
return {
    ...,
    "rsi14": rsi14_val,
}
```

> `calc_rsi` 已為 public 函式（`context_generator.py`），直接引用即可，不重複實作。

---

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

**檔案**：`graph/state.py` 新增 `action_plan_tag: str | None`（`rsi14: float | None` 已在 T2-0 加入）
**檔案**：`api.py` `initial_state` 補 `action_plan_tag: None`、`rsi14: None`

#### T2-3：`strategy_node` 計算並寫入 `action_plan_tag`

**檔案**：`backend/src/ai_stock_sentinel/graph/nodes.py`，`strategy_node` 函式末尾

前提：`rsi14` 已由 T2-0 寫入 `GraphState`，直接讀取 `state.get("rsi14")`。

```python
rsi14 = state.get("rsi14")
flow_label = (state.get("institutional_flow") or {}).get("flow_label")
action_plan_tag = calculate_action_plan_tag(
    rsi14=rsi14,
    flow_label=flow_label,
    confidence_score=state.get("confidence_score"),
)
```

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

- `test_preprocess_node_writes_rsi14_float_to_state`
- `test_preprocess_node_rsi14_is_none_when_insufficient_data`
- `test_calculate_action_plan_tag_returns_opportunity_when_all_conditions_met`
- `test_calculate_action_plan_tag_returns_overheated_when_rsi_high_and_distribution`
- `test_calculate_action_plan_tag_returns_neutral_for_partial_match`
- `test_calculate_action_plan_tag_falls_back_to_neutral_when_any_input_none`
- `test_api_response_includes_action_plan_tag_field`
- `test_api_response_includes_institutional_flow_label_field`

### 執行 Prompt（Session 2）

```
請幫我實作 Session 2 的 Action Plan 燈號：

參考文件：docs/plans/2026-03-06-spec-gap-fix-day1.md 的 Session 2 詳細任務（T2-0 ~ T2-5）

執行順序：
0. T2-0：graph/state.py 新增 rsi14: float | None 欄位；preprocess_node 計算後寫入數值（為 T2-3 提供乾淨的浮點數，不從敘事字串反解）
1. T2-1：新增 calculate_action_plan_tag() 純 Python rule-based 函式（三情境 + None 安全降級）
2. T2-2：graph/state.py 新增 action_plan_tag 欄位；api.py initial_state 補 None
3. T2-3：strategy_node 從 state["rsi14"] 直接讀取浮點數，計算 action_plan_tag 並寫入 state
4. T2-4：AnalyzeResponse 新增 action_plan_tag 與 institutional_flow_label 兩個頂層欄位
5. T2-5：前端 App.tsx Action Plan 卡片標題旁加入燈號 badge

每步完成後補對應單元測試，最後執行 make test 確認全套通過。

⚠️ 燈號判斷邏輯 100% 在後端，前端僅做 enum → emoji/文字映射，不含 rsi14 < 30 等條件判斷。
⚠️ action_plan_tag 為 null 時前端不顯示，不崩潰。
```

---

## 驗收標準（Day 1）

- `make test` 全數通過（含所有新增測試）
- `strategy_node` 輸出的 `entry_zone` 包含具體價格數值（非純描述性文字）
- `/analyze` 回傳 `action_plan_tag` 與 `institutional_flow_label` 欄位
- 前端 Action Plan 卡片顯示燈號；null 時不顯示，不崩潰

---

## Handoff Snapshot — 2026-03-06 Day 1 結束

- 已完成（Session 1）：
  - `StockSnapshot.__post_init__` 自動計算 `high_20d / low_20d / support_20d / resistance_20d`
  - `GraphState` 新增四個位階欄位 + `rsi14` + `action_plan_tag`
  - `preprocess_node` 將位階欄位與 `rsi14` 寫入 state
  - `context_generator.py` 新增 `_price_level_narrative()`，整合進 `generate_technical_context`
  - `strategy_generator.py` `entry_zone` / `stop_loss` 改以實際價格計算（`support_20d`、`low_20d`、`ma60`）
  - `api.py` `initial_state` 補六個欄位

- 已完成（Session 2）：
  - `calculate_action_plan_tag()` 純 rule-based 函式（三情境 + None 安全降級）
  - `strategy_node` 從 `state["rsi14"]` 讀取浮點數，計算 `action_plan_tag` 並寫入 state
  - `AnalyzeResponse` 新增 `action_plan_tag` 與 `institutional_flow_label` 頂層欄位
  - 前端 `App.tsx` Action Plan 卡片標題旁加入燈號 badge（`ACTION_TAG_MAP`）

- 進行中：
  - 無

- 阻塞點：
  - 無

- 下一步（優先序）：
  1. Day 2：`docs/plans/2026-03-07-spec-gap-fix-day2.md`（Session 3 ~ 7）

- 驗收證據：
  - make test → 263 passed
