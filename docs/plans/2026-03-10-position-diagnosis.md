# Position Diagnosis (持股診斷) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## 進度追蹤（2026-03-10）

| Task | 狀態 | 備註 |
|------|------|------|
| Task 1: GraphState PositionState fields | ✅ 完成 | `state.py` 新增 11 個欄位，6 tests pass |
| Task 2: PositionScorer | ✅ 完成 | `position_scorer.py` 建立，18 tests pass |
| Task 3: Wire into preprocess_node / strategy_node | ✅ 完成 | `nodes.py` 修改，43 tests pass（全套 354 tests 全過）|
| Task 4: position-aware prompt in langchain_analyzer.py | ✅ 完成 | `_POSITION_SYSTEM_PROMPT` + 持倉資訊 block，`analyze()` 新增 `position_context` 參數，2 tests pass |
| Task 5: POST /analyze/position endpoint | ✅ 完成 | `PositionAnalyzeRequest` / `PositionAnalysis` / `_build_response()` helper，4 tests pass |
| Task 6: Frontend PositionPage.tsx | ✅ 完成 | `pages/PositionPage.tsx` 建立，`App.tsx` 新增 tab 導航 |
| Task 7: Full test suite validation | ✅ 完成 | 360 tests 全過（Tasks 4-7 新增 6 tests）|

**Goal:** Add a `POST /analyze/position` endpoint that diagnoses an existing holding using entry price as the anchor, producing trailing stop, position status, and action recommendation — all rule-based Python, no LLM arithmetic.

**Architecture:** Extend the existing LangGraph graph with a new route: when `entry_price` is present in the request, activate `PositionState` fields and a new `PositionScorer` module in `preprocess_node` and `strategy_node`. The `/analyze/position` endpoint runs through the same node pipeline but injects position-specific context into the LLM prompt and overrides strategy output with position-focused fields.

**Tech Stack:** FastAPI, LangGraph, Python TypedDict (`PositionState`), React 19 + Tailwind 4 (frontend), pytest (tests)

---

## Context & Key Files

Before starting, read these files to understand existing patterns:

- [backend/src/ai_stock_sentinel/graph/state.py](backend/src/ai_stock_sentinel/graph/state.py) — existing `GraphState`
- [backend/src/ai_stock_sentinel/graph/nodes.py](backend/src/ai_stock_sentinel/graph/nodes.py) — `preprocess_node`, `strategy_node`
- [backend/src/ai_stock_sentinel/graph/builder.py](backend/src/ai_stock_sentinel/graph/builder.py) — graph wiring
- [backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py) — `AnalyzeRequest`, `AnalyzeResponse`
- [backend/src/ai_stock_sentinel/analysis/strategy_generator.py](backend/src/ai_stock_sentinel/analysis/strategy_generator.py) — `generate_action_plan()`
- [backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py](backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py) — system prompt structure
- [frontend/src/App.tsx](frontend/src/App.tsx) — existing dashboard layout
- [backend/tests/test_strategy_generator.py](backend/tests/test_strategy_generator.py) — test style reference
- [backend/tests/test_graph_nodes.py](backend/tests/test_graph_nodes.py) — node test style reference
- [backend/tests/test_api.py](backend/tests/test_api.py) — API test style reference

---

## Task 1: Extend `GraphState` with `PositionState` fields

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/state.py`
- Test: `backend/tests/test_graph_state.py`

### Step 1: Write the failing test

```python
# backend/tests/test_graph_state.py
# Add these test cases to the existing file (or create if absent)

def test_graph_state_has_position_fields():
    """GraphState must include all PositionState optional fields."""
    from ai_stock_sentinel.graph.state import GraphState
    import typing

    hints = typing.get_type_hints(GraphState)
    for field in [
        "entry_price", "entry_date", "quantity",
        "profit_loss_pct", "cost_buffer_to_support",
        "position_status", "position_narrative",
        "trailing_stop", "trailing_stop_reason",
        "recommended_action", "exit_reason",
    ]:
        assert field in hints, f"GraphState missing field: {field}"
```

### Step 2: Run test to verify it fails

```bash
cd backend
pytest tests/test_graph_state.py::test_graph_state_has_position_fields -v
```

Expected: `FAIL` — fields not yet defined.

### Step 3: Add position fields to `GraphState`

In `backend/src/ai_stock_sentinel/graph/state.py`, append to the `GraphState` TypedDict:

```python
    # --- Position Diagnosis (POST /analyze/position) ---
    entry_price: float | None
    entry_date: str | None
    quantity: int | None

    # preprocess_node calculates these (rule-based, not LLM)
    profit_loss_pct: float | None
    cost_buffer_to_support: float | None
    position_status: str | None          # profitable_safe | at_risk | under_water
    position_narrative: str | None

    # strategy_node populates these for position mode
    trailing_stop: float | None
    trailing_stop_reason: str | None
    recommended_action: str | None       # Hold | Trim | Exit
    exit_reason: str | None
```

> Note: All fields use `| None` so they don't break the existing `/analyze` route.

### Step 4: Run test to verify it passes

```bash
cd backend
pytest tests/test_graph_state.py::test_graph_state_has_position_fields -v
```

Expected: `PASS`

### Step 5: Commit

```bash
cd backend
git add src/ai_stock_sentinel/graph/state.py tests/test_graph_state.py
git commit -m "feat: add PositionState fields to GraphState"
```

---

## Task 2: Implement `PositionScorer`

This is the core rule-based module. It must perform all arithmetic so the LLM never calculates numbers.

**Files:**
- Create: `backend/src/ai_stock_sentinel/analysis/position_scorer.py`
- Test: `backend/tests/test_position_scorer.py`

### Step 1: Write the failing tests

```python
# backend/tests/test_position_scorer.py

import pytest
from ai_stock_sentinel.analysis.position_scorer import (
    compute_position_metrics,
    compute_trailing_stop,
    compute_recommended_action,
)


# ── compute_position_metrics ──────────────────────────────────────────────────

class TestComputePositionMetrics:
    def test_profitable_safe(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=1050.0,
            support_20d=960.0,
        )
        assert result["profit_loss_pct"] == pytest.approx(7.14, abs=0.01)
        assert result["position_status"] == "profitable_safe"
        assert result["cost_buffer_to_support"] == pytest.approx(20.0)

    def test_at_risk_upper(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=1010.0,  # +3.06%
            support_20d=960.0,
        )
        assert result["position_status"] == "at_risk"

    def test_at_risk_lower(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=945.0,  # -3.57%
            support_20d=920.0,
        )
        assert result["position_status"] == "at_risk"

    def test_under_water(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=900.0,  # -8.16%
            support_20d=880.0,
        )
        assert result["position_status"] == "under_water"
        assert result["profit_loss_pct"] == pytest.approx(-8.16, abs=0.01)

    def test_narrative_profitable_safe(self):
        result = compute_position_metrics(980.0, 1050.0, 960.0)
        assert "獲利" in result["position_narrative"]

    def test_narrative_at_risk(self):
        result = compute_position_metrics(980.0, 1010.0, 960.0)
        assert "震盪" in result["position_narrative"]

    def test_narrative_under_water(self):
        result = compute_position_metrics(980.0, 900.0, 880.0)
        assert "套牢" in result["position_narrative"]


# ── compute_trailing_stop ─────────────────────────────────────────────────────

class TestComputeTrailingStop:
    def test_breakeven_protection_when_5pct_profit(self):
        # profit >= 5%: trailing_stop = max(entry_price, support_20d)
        stop, reason = compute_trailing_stop(
            profit_loss_pct=7.0,
            entry_price=980.0,
            support_20d=960.0,
            ma10=970.0,
            high_20d=1060.0,
            current_close=1050.0,
        )
        assert stop == 980.0  # max(980, 960)
        assert "成本價" in reason

    def test_trailing_stop_at_new_high(self):
        # close >= high_20d: trailing_stop = max(MA10, support_20d)
        stop, reason = compute_trailing_stop(
            profit_loss_pct=12.0,
            entry_price=980.0,
            support_20d=960.0,
            ma10=1055.0,
            high_20d=1060.0,
            current_close=1065.0,  # >= high_20d, triggers trailing
        )
        assert stop == max(1055.0, 960.0)
        assert "移動停利" in reason

    def test_trailing_stop_prefers_ma10_over_support(self):
        stop, _ = compute_trailing_stop(
            profit_loss_pct=15.0,
            entry_price=980.0,
            support_20d=1000.0,
            ma10=1020.0,
            high_20d=1060.0,
            current_close=1065.0,
        )
        assert stop == 1020.0  # max(1020, 1000)

    def test_under_water_stop(self):
        # profit < -5%: trailing_stop = entry_price * 0.93
        stop, reason = compute_trailing_stop(
            profit_loss_pct=-8.0,
            entry_price=980.0,
            support_20d=880.0,
            ma10=920.0,
            high_20d=1000.0,
            current_close=900.0,
        )
        assert stop == pytest.approx(980.0 * 0.93)
        assert "停損" in reason

    def test_at_risk_uses_support(self):
        # -5% <= profit < 5%: trailing_stop = support_20d
        stop, reason = compute_trailing_stop(
            profit_loss_pct=2.0,
            entry_price=980.0,
            support_20d=950.0,
            ma10=970.0,
            high_20d=1010.0,
            current_close=1000.0,
        )
        assert stop == 950.0
        assert "支撐" in reason


# ── compute_recommended_action ────────────────────────────────────────────────

class TestComputeRecommendedAction:
    def test_trim_when_distribution_and_profitable(self):
        action, reason = compute_recommended_action(
            flow_label="distribution",
            profit_loss_pct=5.0,
            technical_signal="bullish",
            current_close=1050.0,
            trailing_stop=980.0,
            position_status="profitable_safe",
        )
        assert action == "Trim"
        assert reason is not None

    def test_exit_when_distribution_and_loss(self):
        action, reason = compute_recommended_action(
            flow_label="distribution",
            profit_loss_pct=-3.0,
            technical_signal="neutral",
            current_close=950.0,
            trailing_stop=980.0,
            position_status="at_risk",
        )
        assert action == "Exit"

    def test_exit_when_bearish_below_trailing_stop(self):
        action, reason = compute_recommended_action(
            flow_label="accumulation",
            profit_loss_pct=2.0,
            technical_signal="bearish",
            current_close=940.0,
            trailing_stop=960.0,  # close < trailing_stop
            position_status="at_risk",
        )
        assert action == "Exit"

    def test_exit_when_deep_underwater(self):
        action, reason = compute_recommended_action(
            flow_label="neutral",
            profit_loss_pct=-12.0,
            technical_signal="bearish",
            current_close=860.0,
            trailing_stop=911.4,
            position_status="under_water",
        )
        assert action == "Exit"

    def test_hold_when_all_fine(self):
        action, reason = compute_recommended_action(
            flow_label="accumulation",
            profit_loss_pct=8.0,
            technical_signal="bullish",
            current_close=1060.0,
            trailing_stop=980.0,
            position_status="profitable_safe",
        )
        assert action == "Hold"
        assert reason is None

    def test_exit_reason_not_null_for_distribution_profit(self):
        """Spec §7: when flow=distribution and profit>0, exit_reason must not be null."""
        action, reason = compute_recommended_action(
            flow_label="distribution",
            profit_loss_pct=10.0,
            technical_signal="bullish",
            current_close=1080.0,
            trailing_stop=980.0,
            position_status="profitable_safe",
        )
        assert reason is not None
        assert len(reason) > 0
```

### Step 2: Run to verify failure

```bash
cd backend
pytest tests/test_position_scorer.py -v
```

Expected: `ImportError` — module doesn't exist yet.

### Step 3: Implement `position_scorer.py`

```python
# backend/src/ai_stock_sentinel/analysis/position_scorer.py

from __future__ import annotations


_NARRATIVES = {
    "profitable_safe": "目前獲利已脫離成本區，持股安全緩衝充足。",
    "at_risk": "股價正在成本價附近震盪，需密切觀察支撐是否有效。",
    "under_water": "目前處於套牢狀態，需評估停損或攤平策略。",
}


def compute_position_metrics(
    entry_price: float,
    current_price: float,
    support_20d: float,
) -> dict:
    """Rule-based position health calculation. No LLM."""
    profit_loss_pct = (current_price - entry_price) / entry_price * 100
    cost_buffer_to_support = entry_price - support_20d

    if profit_loss_pct > 5 and entry_price > support_20d:
        position_status = "profitable_safe"
    elif -5 <= profit_loss_pct <= 5:
        position_status = "at_risk"
    else:
        position_status = "under_water"

    return {
        "profit_loss_pct": round(profit_loss_pct, 2),
        "cost_buffer_to_support": round(cost_buffer_to_support, 2),
        "position_status": position_status,
        "position_narrative": _NARRATIVES[position_status],
    }


def compute_trailing_stop(
    profit_loss_pct: float,
    entry_price: float,
    support_20d: float,
    ma10: float,
    high_20d: float,
    current_close: float,
) -> tuple[float, str]:
    """Return (trailing_stop_price, reason_string). Rule-based only."""
    # Trailing stop at new high (takes priority over breakeven check)
    if current_close >= high_20d:
        stop = max(ma10, support_20d)
        return round(stop, 2), "移動停利：股價創近 20 日新高，防守位上移至 MA10 與支撐位較高值"

    # Breakeven protection
    if profit_loss_pct >= 5:
        stop = max(entry_price, support_20d)
        return round(stop, 2), "獲利超過 5%，停損位上移至成本價保本"

    # Underwater defence
    if profit_loss_pct < -5:
        stop = entry_price * 0.93
        return round(stop, 2), "套牢防守：停損位設於成本價 -7%"

    # At-risk: hold support
    return round(support_20d, 2), "成本邊緣震盪，防守位參考近 20 日支撐"


def compute_recommended_action(
    flow_label: str,
    profit_loss_pct: float,
    technical_signal: str,
    current_close: float,
    trailing_stop: float,
    position_status: str,
) -> tuple[str, str | None]:
    """Return (action, exit_reason). action: Hold | Trim | Exit."""
    # Rule 1: distribution + profitable → Trim
    if flow_label == "distribution" and profit_loss_pct > 0:
        return "Trim", "法人持續出貨，建議逢高分批減碼保護獲利"

    # Rule 2: distribution + loss → Exit
    if flow_label == "distribution" and profit_loss_pct <= 0:
        return "Exit", "法人出貨且持股虧損，建議停損出場"

    # Rule 3: bearish + price below trailing stop → Exit
    if technical_signal == "bearish" and current_close < trailing_stop:
        return "Exit", f"技術面轉空且收盤價 {current_close} 跌破防守線 {trailing_stop}，建議出場"

    # Rule 4: deep underwater → Exit
    if position_status == "under_water" and profit_loss_pct < -10:
        return "Exit", f"深度套牢（{profit_loss_pct:.1f}%），建議停損出場"

    return "Hold", None
```

### Step 4: Run tests to verify all pass

```bash
cd backend
pytest tests/test_position_scorer.py -v
```

Expected: All green.

### Step 5: Commit

```bash
git add backend/src/ai_stock_sentinel/analysis/position_scorer.py \
        backend/tests/test_position_scorer.py
git commit -m "feat: add PositionScorer with rule-based position metrics, trailing stop, and action"
```

---

## Task 3: Wire `PositionScorer` into `preprocess_node` and `strategy_node`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Test: `backend/tests/test_graph_nodes.py`

### Step 1: Write the failing tests

Add to `backend/tests/test_graph_nodes.py`:

```python
# -- Position Diagnosis node tests --

from ai_stock_sentinel.analysis.position_scorer import compute_position_metrics

def _base_position_state():
    """Minimal GraphState with position fields for testing."""
    return {
        "symbol": "2330.TW",
        "entry_price": 980.0,
        "entry_date": None,
        "quantity": None,
        # snapshot fields required by preprocess_node
        "snapshot": {
            "symbol": "2330.TW",
            "currency": "TWD",
            "current_price": 1050.0,
            "volume": 10000,
            "recent_closes": [1040.0, 1045.0, 1050.0],
            "high_20d": 1060.0,
            "low_20d": 960.0,
            "support_20d": 960.0,
            "resistance_20d": 1060.0,
        },
        "news_content": "",
        "cleaned_news": [],
        "institutional_flow": None,
        "fundamental_data": None,
        "errors": [],
    }


def test_preprocess_node_computes_position_metrics_when_entry_price_set():
    from ai_stock_sentinel.graph.nodes import preprocess_node

    state = _base_position_state()
    result = preprocess_node(state)

    assert "profit_loss_pct" in result
    assert "position_status" in result
    assert "position_narrative" in result
    assert result["position_status"] in ("profitable_safe", "at_risk", "under_water")


def test_preprocess_node_skips_position_metrics_when_no_entry_price():
    from ai_stock_sentinel.graph.nodes import preprocess_node

    state = _base_position_state()
    state["entry_price"] = None
    result = preprocess_node(state)

    assert result.get("profit_loss_pct") is None
    assert result.get("position_status") is None


def test_strategy_node_computes_trailing_stop_when_position_mode():
    from ai_stock_sentinel.graph.nodes import strategy_node

    state = _base_position_state()
    # Add required preprocess outputs
    state.update({
        "profit_loss_pct": 7.14,
        "position_status": "profitable_safe",
        "position_narrative": "獲利安全區",
        "technical_context": "",
        "rsi14": 55.0,
        "support_20d": 960.0,
        "resistance_20d": 1060.0,
        "high_20d": 1060.0,
        "low_20d": 960.0,
        "analysis_detail": {
            "technical_signal": "bullish",
            "institutional_flow": "institutional_accumulation",
            "sentiment_label": "positive",
            "summary": "test",
            "risks": [],
            "tech_insight": "",
            "inst_insight": "",
            "news_insight": "",
            "final_verdict": "",
            "fundamental_insight": "",
        },
        "confidence_score": 70,
    })
    result = strategy_node(state)

    assert "trailing_stop" in result
    assert result["trailing_stop"] is not None
    assert "trailing_stop_reason" in result
    assert "recommended_action" in result
    assert result["recommended_action"] in ("Hold", "Trim", "Exit")
```

### Step 2: Run to verify failure

```bash
cd backend
pytest tests/test_graph_nodes.py -k "position" -v
```

Expected: `FAIL` — nodes don't yet have position logic.

### Step 3: Modify `preprocess_node` in `nodes.py`

Find `preprocess_node` and add position metric computation at the end, before `return`:

```python
# Inside preprocess_node, just before the return statement:

# ── Position Diagnosis (only when entry_price is provided) ──
entry_price = state.get("entry_price")
if entry_price is not None:
    from ai_stock_sentinel.analysis.position_scorer import compute_position_metrics
    support_20d = state.get("support_20d") or (
        snapshot.get("support_20d") if snapshot else None
    )
    current_price = snapshot.get("current_price") if snapshot else None
    if current_price and support_20d:
        pos_metrics = compute_position_metrics(
            entry_price=entry_price,
            current_price=current_price,
            support_20d=support_20d,
        )
        updates.update(pos_metrics)
    else:
        updates.update({
            "profit_loss_pct": None,
            "cost_buffer_to_support": None,
            "position_status": None,
            "position_narrative": None,
        })
else:
    updates.update({
        "profit_loss_pct": None,
        "cost_buffer_to_support": None,
        "position_status": None,
        "position_narrative": None,
    })
```

> Note: Read `preprocess_node` carefully first — it accumulates state into an `updates` dict. Add the block just before `return updates`.

### Step 4: Modify `strategy_node` in `nodes.py`

Find `strategy_node` and add trailing stop + recommended action computation after existing logic:

```python
# At the end of strategy_node, inside the return/updates dict:

# ── Position trailing stop (only when entry_price is provided) ──
entry_price = state.get("entry_price")
if entry_price is not None:
    from ai_stock_sentinel.analysis.position_scorer import (
        compute_trailing_stop,
        compute_recommended_action,
    )
    snapshot = state.get("snapshot") or {}
    inst_flow = state.get("institutional_flow") or {}
    analysis = state.get("analysis_detail") or {}

    profit_loss_pct = state.get("profit_loss_pct", 0.0) or 0.0
    support_20d = state.get("support_20d") or snapshot.get("support_20d", 0.0)
    high_20d = state.get("high_20d") or snapshot.get("high_20d", 0.0)
    current_close = snapshot.get("current_price", entry_price)

    # MA10: derive from recent_closes if available
    recent_closes = snapshot.get("recent_closes", [])
    ma10 = sum(recent_closes[-10:]) / len(recent_closes[-10:]) if len(recent_closes) >= 10 else current_close

    trailing_stop, trailing_stop_reason = compute_trailing_stop(
        profit_loss_pct=profit_loss_pct,
        entry_price=entry_price,
        support_20d=support_20d,
        ma10=ma10,
        high_20d=high_20d,
        current_close=current_close,
    )

    flow_label = inst_flow.get("flow_label", "neutral") if isinstance(inst_flow, dict) else "neutral"
    technical_signal = analysis.get("technical_signal", "neutral") if isinstance(analysis, dict) else "neutral"
    position_status = state.get("position_status", "at_risk") or "at_risk"

    recommended_action, exit_reason = compute_recommended_action(
        flow_label=flow_label,
        profit_loss_pct=profit_loss_pct,
        technical_signal=technical_signal,
        current_close=current_close,
        trailing_stop=trailing_stop,
        position_status=position_status,
    )

    updates["trailing_stop"] = trailing_stop
    updates["trailing_stop_reason"] = trailing_stop_reason
    updates["recommended_action"] = recommended_action
    updates["exit_reason"] = exit_reason
else:
    updates["trailing_stop"] = None
    updates["trailing_stop_reason"] = None
    updates["recommended_action"] = None
    updates["exit_reason"] = None
```

> Note: Read the actual `strategy_node` implementation first to understand how it builds its `updates` dict.

### Step 5: Run tests to verify all pass

```bash
cd backend
pytest tests/test_graph_nodes.py -v
```

Expected: All green (including the new position tests and all pre-existing tests).

### Step 6: Commit

```bash
git add backend/src/ai_stock_sentinel/graph/nodes.py \
        backend/tests/test_graph_nodes.py
git commit -m "feat: wire PositionScorer into preprocess_node and strategy_node"
```

---

## Task 4: Add position-aware system prompt to `langchain_analyzer.py`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`
- Test: `backend/tests/test_langchain_analyzer.py`

### Step 1: Write the failing test

Add to `backend/tests/test_langchain_analyzer.py`:

```python
def test_position_prompt_injected_when_position_context_provided(monkeypatch):
    """When position context is provided, the LLM receives position-mode instructions."""
    from ai_stock_sentinel.analysis.langchain_analyzer import LangchainAnalyzer

    captured_prompts = []

    class FakeLLM:
        def with_structured_output(self, schema):
            return self
        def invoke(self, messages):
            captured_prompts.extend(messages)
            from ai_stock_sentinel.models import AnalysisDetail
            return AnalysisDetail(
                summary="test",
                risks=[],
                technical_signal="bullish",
                institutional_flow="neutral",
                sentiment_label="positive",
                tech_insight="tech",
                inst_insight="inst",
                news_insight="news",
                final_verdict="ok",
                fundamental_insight="",
            )

    analyzer = LangchainAnalyzer(llm=FakeLLM())
    analyzer.analyze(
        technical_context="RSI=55",
        institutional_context="法人買超",
        news_summary="無重大消息",
        fundamental_context="PE 合理",
        position_context={
            "entry_price": 980.0,
            "profit_loss_pct": 7.14,
            "position_status": "profitable_safe",
            "position_narrative": "獲利已脫離成本區",
            "trailing_stop": 980.0,
            "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價",
            "recommended_action": "Hold",
        },
    )

    full_text = " ".join(
        m.content if hasattr(m, "content") else str(m)
        for m in captured_prompts
    )
    assert "持有倉位" in full_text or "entry_price" in full_text
    assert "出場" in full_text or "減碼" in full_text


def test_analyze_without_position_context_unchanged(monkeypatch):
    """Existing /analyze route: no position_context → no position block in prompt."""
    from ai_stock_sentinel.analysis.langchain_analyzer import LangchainAnalyzer

    captured_prompts = []

    class FakeLLM:
        def with_structured_output(self, schema):
            return self
        def invoke(self, messages):
            captured_prompts.extend(messages)
            from ai_stock_sentinel.models import AnalysisDetail
            return AnalysisDetail(
                summary="test", risks=[], technical_signal="bullish",
                institutional_flow="neutral", sentiment_label="positive",
                tech_insight="t", inst_insight="i", news_insight="n",
                final_verdict="ok", fundamental_insight="",
            )

    analyzer = LangchainAnalyzer(llm=FakeLLM())
    analyzer.analyze(
        technical_context="RSI=55",
        institutional_context="法人買超",
        news_summary="無重大消息",
        fundamental_context="PE 合理",
        position_context=None,
    )

    full_text = " ".join(
        m.content if hasattr(m, "content") else str(m)
        for m in captured_prompts
    )
    # The standard prompt must not contain position-specific wording
    assert "entry_price" not in full_text
```

### Step 2: Run to verify failure

```bash
cd backend
pytest tests/test_langchain_analyzer.py -k "position" -v
```

Expected: `FAIL` — `analyze()` doesn't accept `position_context` yet.

### Step 3: Modify `langchain_analyzer.py`

Read the file first to understand the `_HUMAN_PROMPT` construction and the `analyze()` method signature.

Add `position_context: dict | None = None` parameter to `analyze()`.

Add a `_POSITION_SYSTEM_PROMPT` constant:

```python
_POSITION_SYSTEM_PROMPT = """
你正在診斷使用者的持有倉位，而非尋找新的買點。

核心任務：
1. 以購入成本價（entry_price）為錨點，判斷當前持股是否值得繼續持有
2. 法人資金方向是最重要的出場訊號——若法人轉為出貨，獲利中亦須警示了結
3. 你的輸出必須聚焦「出場」、「減碼」、「防守」，不得建議加碼或新進場
4. 禁止以樂觀語氣淡化風險；若存在出場理由，必須明確標記

出場觸發條件（任一命中即須評估出場）：
- flow_label = distribution 且 profit_loss_pct > 0（獲利出場警示）
- flow_label = distribution 且 position_status = under_water（停損評估）
- technical_signal = bearish 且 close < trailing_stop（跌破防守線）
"""
```

In `analyze()`, when `position_context` is not `None`:
1. Append `_POSITION_SYSTEM_PROMPT` to the system message.
2. Append a formatted position block to the human message:

```python
_POSITION_HUMAN_BLOCK = """
【持倉資訊】
- 購入成本價：{entry_price}
- 當前損益：{profit_loss_pct:.2f}%
- 倉位狀態：{position_status}（{position_narrative}）
- 動態防守位：{trailing_stop}（{trailing_stop_reason}）
- 系統建議動作：{recommended_action}

請根據以上持倉資訊，從「防守」視角撰寫 tech_insight、inst_insight、final_verdict。
"""
```

Format and inject this block into the human message only when `position_context` is provided.

### Step 4: Run tests to verify all pass

```bash
cd backend
pytest tests/test_langchain_analyzer.py -v
```

Expected: All green.

### Step 5: Also update `analyze_node` in `nodes.py` to pass position context

In `nodes.py`, find `analyze_node`. When calling `analyzer.analyze(...)`, pass:

```python
position_context = None
if state.get("entry_price") is not None:
    position_context = {
        "entry_price": state.get("entry_price"),
        "profit_loss_pct": state.get("profit_loss_pct"),
        "position_status": state.get("position_status"),
        "position_narrative": state.get("position_narrative"),
        "trailing_stop": state.get("trailing_stop"),
        "trailing_stop_reason": state.get("trailing_stop_reason"),
        "recommended_action": state.get("recommended_action"),
    }
# Then call:
analysis = analyzer.analyze(..., position_context=position_context)
```

> Note: `trailing_stop` is computed in `strategy_node` which runs after `analyze_node`. In `analyze_node`, `trailing_stop` won't be available yet. Pass whatever is available from `preprocess_node` outputs (`profit_loss_pct`, `position_status`, `position_narrative`). Omit `trailing_stop` from the context here — it will be in the response separately.

Adjust `_POSITION_HUMAN_BLOCK` to make `trailing_stop` fields optional (use `.get()` with defaults).

### Step 6: Run full test suite

```bash
cd backend
pytest tests/ -v
```

Expected: All green.

### Step 7: Commit

```bash
git add backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py \
        backend/src/ai_stock_sentinel/graph/nodes.py \
        backend/tests/test_langchain_analyzer.py
git commit -m "feat: inject position-diagnosis prompt into LLM when entry_price is provided"
```

---

## Task 5: Add `POST /analyze/position` API endpoint

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Test: `backend/tests/test_api.py`

### Step 1: Write the failing tests

Add to `backend/tests/test_api.py`:

```python
# ── POST /analyze/position ────────────────────────────────────────────────────

def test_analyze_position_returns_position_analysis_block(mock_graph):
    """The /analyze/position endpoint must return a position_analysis object."""
    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
    })
    assert response.status_code == 200
    body = response.json()
    assert "position_analysis" in body
    pa = body["position_analysis"]
    assert "entry_price" in pa
    assert "profit_loss_pct" in pa
    assert "position_status" in pa
    assert "trailing_stop" in pa
    assert "recommended_action" in pa
    assert pa["recommended_action"] in ("Hold", "Trim", "Exit")


def test_analyze_position_entry_price_required():
    """entry_price is required for /analyze/position."""
    response = client.post("/analyze/position", json={"symbol": "2330.TW"})
    assert response.status_code == 422


def test_analyze_position_optional_fields_accepted(mock_graph):
    """entry_date and quantity are accepted but optional."""
    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
        "entry_date": "2026-01-15",
        "quantity": 1000,
    })
    assert response.status_code == 200


def test_analyze_position_exit_reason_not_null_when_distribution_profit(mock_graph_distribution):
    """Spec §7: exit_reason must not be null when flow=distribution and profit>0."""
    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 800.0,  # current price ~1050 → profitable
    })
    assert response.status_code == 200
    body = response.json()
    pa = body["position_analysis"]
    if pa["recommended_action"] in ("Trim", "Exit"):
        assert pa["exit_reason"] is not None
```

> Note: `mock_graph` and `mock_graph_distribution` are fixtures. Read existing `test_api.py` for how the current `mock_graph` fixture is set up and replicate the pattern.

### Step 2: Run to verify failure

```bash
cd backend
pytest tests/test_api.py -k "position" -v
```

Expected: `FAIL` — endpoint doesn't exist.

### Step 3: Add `PositionAnalyzeRequest` and `PositionAnalysis` models to `api.py`

```python
class PositionAnalyzeRequest(BaseModel):
    symbol: str
    entry_price: float
    entry_date: str | None = None
    quantity: int | None = None


class PositionAnalysis(BaseModel):
    entry_price: float
    profit_loss_pct: float | None
    position_status: str | None
    position_narrative: str | None
    recommended_action: str | None
    trailing_stop: float | None
    trailing_stop_reason: str | None
    exit_reason: str | None
```

### Step 4: Add `POST /analyze/position` route to `api.py`

```python
@app.post("/analyze/position", response_model=AnalyzeResponse)
async def analyze_position(
    request: PositionAnalyzeRequest,
    graph=Depends(get_graph),
):
    initial_state: GraphState = {
        "symbol": request.symbol,
        "entry_price": request.entry_price,
        "entry_date": request.entry_date,
        "quantity": request.quantity,
        # zero-out fields not used in position mode
        "news_content": "",
        "cleaned_news": [],
        "errors": [],
    }
    result = graph.invoke(initial_state)
    return _build_response(result)
```

Extract a `_build_response(result: GraphState) -> AnalyzeResponse` helper so both `/analyze` and `/analyze/position` share the same serialization logic.

`AnalyzeResponse` must include `position_analysis: PositionAnalysis | None = None`.

Populate `position_analysis` from the result state when `entry_price` is present:

```python
position_analysis = None
if result.get("entry_price") is not None:
    position_analysis = PositionAnalysis(
        entry_price=result["entry_price"],
        profit_loss_pct=result.get("profit_loss_pct"),
        position_status=result.get("position_status"),
        position_narrative=result.get("position_narrative"),
        recommended_action=result.get("recommended_action"),
        trailing_stop=result.get("trailing_stop"),
        trailing_stop_reason=result.get("trailing_stop_reason"),
        exit_reason=result.get("exit_reason"),
    )
```

### Step 5: Run tests to verify all pass

```bash
cd backend
pytest tests/test_api.py -v
```

Expected: All green.

### Step 6: Smoke test manually

```bash
cd backend
uvicorn ai_stock_sentinel.api:app --reload &
curl -X POST http://127.0.0.1:8000/analyze/position \
  -H "Content-Type: application/json" \
  -d '{"symbol":"2330.TW","entry_price":980.0}' | python -m json.tool
```

Expected: JSON with `position_analysis` block.

### Step 7: Commit

```bash
git add backend/src/ai_stock_sentinel/api.py \
        backend/tests/test_api.py
git commit -m "feat: add POST /analyze/position endpoint with PositionAnalysis response"
```

---

## Task 6: Frontend — 「我的持股」Page (`PositionPage.tsx`)

**Files:**
- Create: `frontend/src/pages/PositionPage.tsx`
- Modify: `frontend/src/App.tsx` (add tab navigation)
- Test: Manual browser verification (no unit test framework for React in this project)

### Step 1: Add tab navigation to `App.tsx`

Read `App.tsx` first. Add a state variable `activeTab: "analyze" | "position"` and two tab buttons at the top of the layout:

```tsx
const [activeTab, setActiveTab] = useState<"analyze" | "position">("analyze");
```

Tab bar (Tailwind classes, match existing style):

```tsx
<div className="flex gap-2 mb-6">
  <button
    onClick={() => setActiveTab("analyze")}
    className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors ${
      activeTab === "analyze"
        ? "bg-blue-600 text-white"
        : "bg-gray-800 text-gray-400 hover:bg-gray-700"
    }`}
  >
    個股分析
  </button>
  <button
    onClick={() => setActiveTab("position")}
    className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors ${
      activeTab === "position"
        ? "bg-blue-600 text-white"
        : "bg-gray-800 text-gray-400 hover:bg-gray-700"
    }`}
  >
    我的持股
  </button>
</div>

{activeTab === "analyze" && <ExistingAnalyzeContent />}
{activeTab === "position" && <PositionPage />}
```

Wrap existing analyze content in a fragment (do not extract to a new component to avoid large refactor).

### Step 2: Create `frontend/src/pages/PositionPage.tsx`

```tsx
// frontend/src/pages/PositionPage.tsx

import { useState } from "react";

interface PositionAnalysis {
  entry_price: number;
  profit_loss_pct: number | null;
  position_status: "profitable_safe" | "at_risk" | "under_water" | null;
  position_narrative: string | null;
  recommended_action: "Hold" | "Trim" | "Exit" | null;
  trailing_stop: number | null;
  trailing_stop_reason: string | null;
  exit_reason: string | null;
}

interface PositionResponse {
  symbol: string;
  current_price: number;
  position_analysis: PositionAnalysis;
  confidence_score: number;
  technical_signal: string;
  institutional_flow: string;
  tech_insight: string;
  inst_insight: string;
  news_insight: string;
  final_verdict: string;
}

const STATUS_CONFIG = {
  profitable_safe: { label: "獲利安全區", color: "text-green-400", bg: "bg-green-900/30", dot: "🟢" },
  at_risk: { label: "成本邊緣", color: "text-yellow-400", bg: "bg-yellow-900/30", dot: "🟡" },
  under_water: { label: "套牢防守", color: "text-red-400", bg: "bg-red-900/30", dot: "🔴" },
} as const;

const ACTION_CONFIG = {
  Hold: { label: "續抱", color: "text-green-400", bg: "bg-green-900/20" },
  Trim: { label: "減碼", color: "text-yellow-400", bg: "bg-yellow-900/20" },
  Exit: { label: "出場", color: "text-red-400", bg: "bg-red-900/20" },
} as const;

export default function PositionPage() {
  const [symbol, setSymbol] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [entryDate, setEntryDate] = useState("");
  const [quantity, setQuantity] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PositionResponse | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol || !entryPrice) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const body: Record<string, unknown> = {
        symbol,
        entry_price: parseFloat(entryPrice),
      };
      if (entryDate) body.entry_date = entryDate;
      if (quantity) body.quantity = parseInt(quantity);

      const res = await fetch("http://127.0.0.1:8000/analyze/position", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "請求失敗");
    } finally {
      setLoading(false);
    }
  }

  const pa = result?.position_analysis;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Input Form */}
      <form onSubmit={handleSubmit} className="bg-gray-800/50 rounded-xl p-6 space-y-4">
        <h2 className="text-lg font-semibold text-white">持股診斷</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">股票代碼 *</label>
            <input
              type="text"
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              placeholder="2330.TW"
              required
              className="w-full bg-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">購入成本價 *</label>
            <input
              type="number"
              value={entryPrice}
              onChange={e => setEntryPrice(e.target.value)}
              placeholder="980"
              required
              min="0.01"
              step="0.01"
              className="w-full bg-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">購入日期（選填）</label>
            <input
              type="date"
              value={entryDate}
              onChange={e => setEntryDate(e.target.value)}
              className="w-full bg-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">持有數量（選填）</label>
            <input
              type="number"
              value={quantity}
              onChange={e => setQuantity(e.target.value)}
              placeholder="1000"
              min="1"
              className="w-full bg-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>
        <button
          type="submit"
          disabled={loading || !symbol || !entryPrice}
          className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg px-4 py-2 font-medium transition-colors"
        >
          {loading ? "診斷中..." : "開始診斷"}
        </button>
      </form>

      {/* Error */}
      {error && (
        <div className="bg-red-900/30 border border-red-500/30 rounded-xl p-4 text-red-400">
          {error}
        </div>
      )}

      {/* Results */}
      {result && pa && (
        <div className="space-y-4">
          {/* Exit Warning */}
          {pa.exit_reason && (
            <div className="bg-red-900/40 border border-red-500/50 rounded-xl p-4">
              <div className="font-semibold text-red-400 mb-1">出場警示</div>
              <div className="text-red-300">{pa.exit_reason}</div>
            </div>
          )}

          {/* Position Status Card */}
          {pa.position_status && (
            <div className={`rounded-xl p-5 ${STATUS_CONFIG[pa.position_status].bg} border border-gray-700`}>
              <div className="flex items-center justify-between mb-3">
                <span className="text-gray-400 text-sm">倉位狀態</span>
                <span className={`font-bold text-lg ${STATUS_CONFIG[pa.position_status].color}`}>
                  {STATUS_CONFIG[pa.position_status].dot} {STATUS_CONFIG[pa.position_status].label}
                </span>
              </div>
              <p className="text-gray-300 text-sm">{pa.position_narrative}</p>

              {/* P&L Bar */}
              <div className="mt-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-gray-400">損益</span>
                  <span className={pa.profit_loss_pct != null && pa.profit_loss_pct >= 0 ? "text-green-400" : "text-red-400"}>
                    {pa.profit_loss_pct != null ? `${pa.profit_loss_pct > 0 ? "+" : ""}${pa.profit_loss_pct.toFixed(2)}%` : "—"}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-sm mt-2">
                  <div className="text-center">
                    <div className="text-gray-500 text-xs">成本價</div>
                    <div className="text-white font-mono">{pa.entry_price}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-gray-500 text-xs">現價</div>
                    <div className="text-white font-mono">{result.current_price}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-gray-500 text-xs">防守位</div>
                    <div className="text-orange-400 font-mono">{pa.trailing_stop ?? "—"}</div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Action Card */}
          {pa.recommended_action && (
            <div className={`rounded-xl p-5 ${ACTION_CONFIG[pa.recommended_action].bg} border border-gray-700`}>
              <div className="flex items-center justify-between">
                <span className="text-gray-400">操作建議</span>
                <span className={`font-bold text-xl ${ACTION_CONFIG[pa.recommended_action].color}`}>
                  {ACTION_CONFIG[pa.recommended_action].label}
                </span>
              </div>
              {pa.trailing_stop_reason && (
                <p className="text-gray-400 text-sm mt-2">{pa.trailing_stop_reason}</p>
              )}
            </div>
          )}

          {/* Insight Cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              { label: "技術面防守", content: result.tech_insight },
              { label: "主力動向", content: result.inst_insight },
              { label: "消息面風險", content: result.news_insight },
            ].map(({ label, content }) => (
              <div key={label} className="bg-gray-800/50 rounded-xl p-4 border border-gray-700">
                <div className="text-gray-400 text-sm mb-2">{label}</div>
                <p className="text-gray-300 text-sm leading-relaxed">{content || "—"}</p>
              </div>
            ))}
          </div>

          {/* Final Verdict */}
          <div className="bg-gray-800/50 rounded-xl p-5 border border-gray-700">
            <div className="text-gray-400 text-sm mb-2">綜合研判</div>
            <p className="text-white leading-relaxed">{result.final_verdict}</p>
          </div>

          {/* Disclaimer */}
          <p className="text-gray-600 text-xs text-center">
            本診斷結果僅供參考，不構成投資建議。防守位每次查詢動態計算，非靜態設定。
          </p>
        </div>
      )}
    </div>
  );
}
```

### Step 3: Import `PositionPage` in `App.tsx`

```tsx
import PositionPage from "./pages/PositionPage";
```

### Step 4: Manual browser verification checklist

Start dev servers:
```bash
# Terminal 1
cd backend && make dev   # or: uvicorn ai_stock_sentinel.api:app --reload

# Terminal 2
cd frontend && pnpm dev
```

Verify:
- [x] Two tabs visible: 個股分析 / 我的持股
- [x] 我的持股 tab shows the input form
- [x] Submitting `2330.TW` + `entry_price=980` returns results without error
- [x] Position status card shows correct status label and color
- [x] Trailing stop displayed in 防守位 cell
- [x] If `recommended_action = Exit`, exit warning banner appears in red
- [x] Disclaimer text visible at bottom

### Step 5: Commit

```bash
git add frontend/src/pages/PositionPage.tsx \
        frontend/src/App.tsx
git commit -m "feat: add 我的持股 tab and PositionPage with position diagnosis UI"
```

---

## Task 7: Run full test suite and validate acceptance criteria

### Step 1: Run all backend tests

```bash
cd backend
pytest tests/ -v --tb=short
```

Expected: All tests green (existing 295+ tests + new position tests).

### Step 2: Validate acceptance criteria from spec §7

Run targeted tests covering each criterion:

```bash
cd backend
pytest tests/test_position_scorer.py tests/test_graph_nodes.py tests/test_api.py -v -k "position"
```

Verify manually against spec §7:
- [x] `entry_price` + `entry_price` triggers position route (independent of `/analyze`) — `test_analyze_position_*` 4 tests pass
- [x] `profit_loss_pct`, `position_status`, `trailing_stop` computed by Python (not LLM) — `test_position_scorer.py` 18 tests pass
- [x] `recommended_action` follows the 4-rule table in spec §4 — `TestComputeRecommendedAction` 6 tests pass
- [x] When `flow_label=distribution` + `profit>0`, `exit_reason` is not null — `test_exit_reason_not_null_for_distribution_profit` pass
- [x] Frontend: position status card shows correct label and color — 已目視確認
- [x] Frontend: when `recommended_action=Exit`, red warning banner with `exit_reason` — 已目視確認

### Step 3: Final commit

```bash
git add .
git commit -m "feat: complete Phase 6 position diagnosis — backend + frontend"
```

---

## Acceptance Criteria Summary (from spec §7)

| Criterion | Test Location |
|-----------|--------------|
| `entry_price` triggers position route, independent of `/analyze` | `test_api.py::test_analyze_position_*` |
| `profit_loss_pct`, `position_status`, `trailing_stop` by Python rule | `test_position_scorer.py` |
| `recommended_action` follows 4-rule table | `test_position_scorer.py::TestComputeRecommendedAction` |
| `exit_reason` not null when `distribution` + profit | `test_position_scorer.py::test_exit_reason_not_null_for_distribution_profit` |
| Frontend status card correct labels/colors | Manual browser check |
| `Exit` action shows red warning with `exit_reason` | Manual browser check |
| Technical indicators reuse existing tools | Architecture (no new data fetching) |
